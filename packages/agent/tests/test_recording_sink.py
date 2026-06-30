"""Tests for grackle.python_runtime.recording_sink (Phase 9.3, ADR-0020 amendment)."""

from __future__ import annotations

import os
import time
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import pytest

from grackle.python_runtime.jsonl_index import JsonlIndex
from grackle.python_runtime.recording_sink import (
    RecordingSink,
    is_safe_session_id,
    sweep_orphaned_recordings,
)
from grackle.python_runtime.writer import read_jsonl
from grackle.session_store import SessionStore

if TYPE_CHECKING:
    from pathlib import Path


def _payload(i: int) -> dict[str, Any]:
    return {
        "event": "call",
        "node_id": f"script.py:func_{i}",
        "ts_ns": i * 1_000_000,
        "thread_id": 1,
        "frame_depth": 0,
        "metadata": {"count": 1},
    }


class _FlakyFile:
    """Proxies a real binary file handle. The (fail_after+1)-th write writes a
    PARTIAL chunk of its bytes to disk and THEN raises — modelling a real disk
    failure mid-write that leaves a torn trailing line. This forces the salvage
    path (truncate back to the last good offset) to actually do work; a no-op
    salvage would leave the partial bytes and make read_jsonl raise."""

    def __init__(self, real: Any, fail_after: int) -> None:
        self._real = real
        self._fail_after = fail_after
        self._calls = 0

    def write(self, data: bytes) -> int:
        self._calls += 1
        if self._calls > self._fail_after:
            # Write a partial fragment to disk, then fail — leaving a torn line.
            self._real.write(data[: max(1, len(data) // 2)])
            raise OSError("disk full")
        return int(self._real.write(data))

    def truncate(self, size: int | None = None) -> int:
        return int(self._real.truncate(size))

    def close(self) -> None:
        self._real.close()


async def test_write_then_finalize_creates_jsonl_and_row(tmp_path: Path) -> None:
    store = SessionStore.open(tmp_path / "sessions.db")
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()

    sink = RecordingSink(recordings_dir, "sess-1", store, "python")
    for i in range(3):
        sink.write(_payload(i))
    await sink.finalize()

    final = recordings_dir / "sess-1.jsonl"
    assert final.exists()
    assert not (recordings_dir / "sess-1.jsonl.part").exists()

    events = read_jsonl(final)
    assert len(events) == 3
    assert events[0]["node_id"] == "script.py:func_0"

    meta = store.get_session("sess-1")
    assert meta is not None
    assert meta.event_count == 3
    assert meta.language == "python"
    assert meta.source_path == str(final.resolve())
    store.close()


async def test_finalize_idempotent(tmp_path: Path) -> None:
    store = SessionStore.open(tmp_path / "sessions.db")
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()

    sink = RecordingSink(recordings_dir, "sess-2", store, "python")
    sink.write(_payload(0))
    await sink.finalize()
    await sink.finalize()  # must not raise or duplicate

    sessions = store.list_sessions()
    assert len(sessions) == 1
    store.close()


async def test_finalize_zero_events_skips_save(tmp_path: Path) -> None:
    store = SessionStore.open(tmp_path / "sessions.db")
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()

    sink = RecordingSink(recordings_dir, "sess-empty", store, "python")
    await sink.finalize()

    assert store.get_session("sess-empty") is None
    assert not (recordings_dir / "sess-empty.jsonl").exists()
    assert not (recordings_dir / "sess-empty.jsonl.part").exists()
    store.close()


async def test_broken_write_on_first_event_discards(tmp_path: Path) -> None:
    """A write failure before any event was successfully written has
    nothing to salvage -- finalize takes the discard branch."""
    store = SessionStore.open(tmp_path / "sessions.db")
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()

    sink = RecordingSink(recordings_dir, "sess-empty-break", store, "python")
    sink._f = _FlakyFile(sink._f, fail_after=0)  # type: ignore[assignment]

    sink.write(_payload(0))  # raises immediately; swallowed, no events recorded
    assert sink._event_count == 0
    assert sink._broken is True

    await sink.finalize()

    assert store.get_session("sess-empty-break") is None
    assert not (recordings_dir / "sess-empty-break.jsonl").exists()
    assert not (recordings_dir / "sess-empty-break.jsonl.part").exists()
    store.close()


async def test_broken_write_after_events_salvages_prior_events(tmp_path: Path) -> None:
    """A write failure after N good events salvages those N events instead
    of discarding the whole recording."""
    store = SessionStore.open(tmp_path / "sessions.db")
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()

    sink = RecordingSink(recordings_dir, "sess-salvage", store, "python")
    sink._f = _FlakyFile(sink._f, fail_after=2)  # type: ignore[assignment]

    sink.write(_payload(0))
    sink.write(_payload(1))
    # The 3rd write pushes a PARTIAL fragment of event 2 into the file, then
    # raises — leaving a torn trailing line that salvage must truncate away.
    sink.write(_payload(2))
    assert sink._event_count == 2
    assert sink._broken is True

    await sink.finalize()

    final = recordings_dir / "sess-salvage.jsonl"
    assert final.exists()
    assert not (recordings_dir / "sess-salvage.jsonl.part").exists()

    # read_jsonl would raise json.JSONDecodeError on the torn fragment if
    # salvage had NOT truncated it away — so this both counts and proves the
    # file is clean JSONL.
    events = read_jsonl(final)
    assert len(events) == 2
    assert events[0]["node_id"] == "script.py:func_0"
    assert events[1]["node_id"] == "script.py:func_1"

    meta = store.get_session("sess-salvage")
    assert meta is not None
    assert meta.event_count == 2
    store.close()


async def test_save_session_failure_does_not_raise(tmp_path: Path) -> None:
    """A transient store error during finalize's save_session must not
    propagate -- the file still lands even though the row doesn't."""
    store = SessionStore.open(tmp_path / "sessions.db")
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()

    sink = RecordingSink(recordings_dir, "sess-db-fail", store, "python")
    sink.write(_payload(0))

    def _boom_save(meta: object) -> None:
        raise RuntimeError("database is locked")

    store.save_session = _boom_save  # type: ignore[method-assign]

    await sink.finalize()  # must not raise

    # The file lands (close+rename happened before the failing store write)...
    final = recordings_dir / "sess-db-fail.jsonl"
    assert final.exists()
    # ...but no row was registered (the patched save_session raised; get_session
    # is unpatched and reads the real, empty table).
    assert store.get_session("sess-db-fail") is None
    store.close()


def test_duplicate_session_id_raises_file_exists_error(tmp_path: Path) -> None:
    """Two RecordingSinks for the same session_id must not silently
    truncate each other's file -- the second open fails loudly."""
    store = SessionStore.open(tmp_path / "sessions.db")
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()

    first = RecordingSink(recordings_dir, "sess-dup", store, "python")
    try:
        with pytest.raises(FileExistsError):
            RecordingSink(recordings_dir, "sess-dup", store, "python")
    finally:
        first._f.close()
    store.close()


async def test_finalized_file_is_loadable_seekable(tmp_path: Path) -> None:
    store = SessionStore.open(tmp_path / "sessions.db")
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()

    sink = RecordingSink(recordings_dir, "sess-4", store, "python")
    for i in range(5):
        sink.write(_payload(i))
    await sink.finalize()

    final = recordings_dir / "sess-4.jsonl"
    index = JsonlIndex.build(final)
    assert len(index) == 5
    store.close()


async def test_tmp_uses_name_append_not_with_suffix(tmp_path: Path) -> None:
    """The intermediate path must be <id>.jsonl.part (name-append), matching
    the project's atomic-write convention (see python_runtime/writer.py)."""
    store = SessionStore.open(tmp_path / "sessions.db")
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()

    sink = RecordingSink(recordings_dir, "sess-5", store, "python")
    assert sink._tmp_path.name == "sess-5.jsonl.part"
    assert sink._final_path.name == "sess-5.jsonl"

    sink.write(_payload(0))
    # While open, only the .part file should exist on disk.
    assert (recordings_dir / "sess-5.jsonl.part").exists()
    assert not (recordings_dir / "sess-5.jsonl").exists()

    await sink.finalize()
    store.close()


@pytest.mark.parametrize(
    ("session_id", "expected"),
    [
        ("a1b2c3", True),
        ("A1B2_c3.d-4", True),
        ("", False),
        (".", False),
        ("..", False),
        ("../escape", False),
        ("a/b", False),
        ("a\\b", False),
        ("a\x00b", False),
        (".hidden", False),  # leading dot
        ("-flag", False),  # leading dash (argv-injection guard)
        ("a b", False),  # whitespace
        ("x" * 129, False),  # over the length cap
        ("x" * 128, True),  # at the length cap
    ],
)
def test_is_safe_session_id(session_id: str, expected: bool) -> None:
    assert is_safe_session_id(session_id) is expected


def test_is_safe_session_id_accepts_uuid4() -> None:
    assert is_safe_session_id(str(uuid4())) is True


def test_sweep_orphaned_recordings_removes_old_part_files(tmp_path: Path) -> None:
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()
    orphan = recordings_dir / "dead-session.jsonl.part"
    orphan.write_text('{"event": "call"}\n', encoding="utf-8")
    old_time = time.time() - 3600
    os.utime(orphan, (old_time, old_time))
    keep = recordings_dir / "finished.jsonl"
    keep.write_text('{"event": "call"}\n', encoding="utf-8")

    sweep_orphaned_recordings(recordings_dir, min_age_s=30.0)

    assert not orphan.exists()
    assert keep.exists()


def test_sweep_orphaned_recordings_keeps_fresh_part_files(tmp_path: Path) -> None:
    """A .part younger than min_age_s is left alone -- it may belong to a
    concurrently-starting recording, not a hard-killed one."""
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()
    fresh = recordings_dir / "in-progress.jsonl.part"
    fresh.write_text('{"event": "call"}\n', encoding="utf-8")

    sweep_orphaned_recordings(recordings_dir, min_age_s=30.0)

    assert fresh.exists()


def test_sweep_orphaned_recordings_missing_dir_is_noop(tmp_path: Path) -> None:
    sweep_orphaned_recordings(tmp_path / "does-not-exist")
