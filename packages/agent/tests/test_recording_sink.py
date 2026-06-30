"""Tests for grackle.python_runtime.recording_sink (Phase 9.3, ADR-0020 amendment)."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from grackle.python_runtime.jsonl_index import JsonlIndex
from grackle.python_runtime.recording_sink import RecordingSink, sweep_orphaned_recordings
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


async def test_write_error_disables_sink_no_raise(tmp_path: Path) -> None:
    store = SessionStore.open(tmp_path / "sessions.db")
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()

    sink = RecordingSink(recordings_dir, "sess-3", store, "python")
    sink.write(_payload(0))

    class _Boom:
        def write(self, _data: str) -> int:
            raise OSError("disk full")

    sink._f = _Boom()  # type: ignore[assignment]
    sink.write(_payload(1))  # must swallow, not raise

    await sink.finalize()  # broken sink -> no DB row, no leftover files
    assert store.get_session("sess-3") is None
    assert not (recordings_dir / "sess-3.jsonl").exists()
    assert not (recordings_dir / "sess-3.jsonl.part").exists()
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


def test_sweep_orphaned_recordings_removes_part_files(tmp_path: Path) -> None:
    recordings_dir = tmp_path / "recordings"
    recordings_dir.mkdir()
    orphan = recordings_dir / "dead-session.jsonl.part"
    orphan.write_text('{"event": "call"}\n', encoding="utf-8")
    keep = recordings_dir / "finished.jsonl"
    keep.write_text('{"event": "call"}\n', encoding="utf-8")

    sweep_orphaned_recordings(recordings_dir)

    assert not orphan.exists()
    assert keep.exists()


def test_sweep_orphaned_recordings_missing_dir_is_noop(tmp_path: Path) -> None:
    sweep_orphaned_recordings(tmp_path / "does-not-exist")
