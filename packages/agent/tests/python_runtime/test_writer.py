"""Tests for python_runtime.writer — JSONL write/read atomicity."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

import pytest

from grackle.python_runtime.writer import JsonlPartWriter, read_jsonl, write_jsonl

if TYPE_CHECKING:
    from pathlib import Path

    from grackle.adapters.base import TraceEvent


def _events(n: int) -> list[TraceEvent]:
    return [
        {
            "event": "call",
            "node_id": f"src/app.py:fn{i}",
            "ts_ns": i * 1000,
            "thread_id": 1,
            "frame_depth": i,
            "metadata": {},
        }
        for i in range(n)
    ]


# ---------------------------------------------------------------------------
# write_jsonl
# ---------------------------------------------------------------------------


def test_write_creates_file(tmp_path: Path) -> None:
    dest = tmp_path / "out.jsonl"
    write_jsonl(_events(3), dest)
    assert dest.exists()


def test_write_returns_event_count(tmp_path: Path) -> None:
    dest = tmp_path / "out.jsonl"
    count = write_jsonl(_events(5), dest)
    assert count == 5


def test_write_zero_events_creates_empty_file(tmp_path: Path) -> None:
    dest = tmp_path / "empty.jsonl"
    count = write_jsonl([], dest)
    assert count == 0
    assert dest.read_text(encoding="utf-8") == ""


def test_each_line_is_valid_json(tmp_path: Path) -> None:
    dest = tmp_path / "out.jsonl"
    write_jsonl(_events(4), dest)
    for line in dest.read_text(encoding="utf-8").splitlines():
        obj = json.loads(line)
        assert isinstance(obj, dict)


def test_no_tmp_file_left_behind(tmp_path: Path) -> None:
    dest = tmp_path / "out.jsonl"
    write_jsonl(_events(2), dest)
    # The new tmp name is "<name>.tmp" (appended), so check both
    # the modern shape and the legacy with_suffix shape — neither should
    # leak after a successful write.
    assert not (tmp_path / "out.jsonl.tmp").exists()
    assert not (tmp_path / "out.tmp").exists()


def test_tmp_path_uses_append_not_with_suffix(tmp_path: Path) -> None:
    """Regression: foo.tar.gz must produce foo.tar.gz.tmp, not foo.tar.tmp.

    ``with_suffix(".tmp")`` only replaces the final extension and would
    collide when multiple destinations share a stem. The writer appends
    ``.tmp`` to the full filename instead.
    """
    # Multi-suffix file — with_suffix would strip ".gz" and collide.
    dest = tmp_path / "trace.tar.gz"
    write_jsonl(_events(1), dest)
    assert dest.exists()
    # The wrong-but-tempting shape must not exist
    assert not (tmp_path / "trace.tar.tmp").exists()


def test_atomic_write_replaces_existing(tmp_path: Path) -> None:
    dest = tmp_path / "out.jsonl"
    write_jsonl(_events(2), dest)
    first_content = dest.read_text(encoding="utf-8")
    write_jsonl(_events(3), dest)
    second_content = dest.read_text(encoding="utf-8")
    assert second_content != first_content
    assert len(second_content.splitlines()) == 3


# ---------------------------------------------------------------------------
# read_jsonl
# ---------------------------------------------------------------------------


def test_roundtrip(tmp_path: Path) -> None:
    dest = tmp_path / "trace.jsonl"
    original = _events(6)
    write_jsonl(original, dest)
    loaded = read_jsonl(dest)
    assert len(loaded) == 6
    for orig, loaded_e in zip(original, loaded, strict=True):
        assert loaded_e["node_id"] == orig["node_id"]
        assert loaded_e["event"] == orig["event"]
        assert loaded_e["ts_ns"] == orig["ts_ns"]


def test_read_skips_blank_lines(tmp_path: Path) -> None:
    dest = tmp_path / "trace.jsonl"
    dest.write_text(
        '{"event":"call","node_id":"a.py","ts_ns":1,"thread_id":1,"frame_depth":0,"metadata":{}}\n'
        "\n"
        '{"event":"return","node_id":"a.py","ts_ns":2,"thread_id":1,"frame_depth":0,"metadata":{}}\n',
        encoding="utf-8",
    )
    events = read_jsonl(dest)
    assert len(events) == 2


def test_read_raises_on_malformed_json(tmp_path: Path) -> None:
    dest = tmp_path / "bad.jsonl"
    dest.write_text("not-valid-json\n", encoding="utf-8")
    with pytest.raises(json.JSONDecodeError):
        read_jsonl(dest)


# ---------------------------------------------------------------------------
# JsonlPartWriter (Phase 12.0)
# ---------------------------------------------------------------------------


class _FlakyFile:
    """Proxies a real binary file handle. The (fail_after+1)-th write writes a
    PARTIAL chunk of its bytes to disk and THEN raises — modelling a real disk
    failure mid-write that leaves a torn trailing line. Mirrors the fixture in
    tests/test_recording_sink.py (the mechanism JsonlPartWriter was extracted
    from) so both suites exercise the identical salvage scenario."""

    def __init__(self, real: Any, fail_after: int) -> None:
        self._real = real
        self._fail_after = fail_after
        self._calls = 0

    def write(self, data: bytes) -> int:
        self._calls += 1
        if self._calls > self._fail_after:
            self._real.write(data[: max(1, len(data) // 2)])
            raise OSError("disk full")
        return int(self._real.write(data))

    def truncate(self, size: int | None = None) -> int:
        return int(self._real.truncate(size))

    def close(self) -> None:
        self._real.close()


def test_part_writer_byte_identical_to_write_jsonl(tmp_path: Path) -> None:
    events = _events(4)
    via_write_jsonl = tmp_path / "a.jsonl"
    write_jsonl(events, via_write_jsonl)

    via_part_writer = tmp_path / "b.jsonl"
    writer = JsonlPartWriter(via_part_writer)
    for event in events:
        writer.write(event)
    writer.finalize()

    assert via_part_writer.read_bytes() == via_write_jsonl.read_bytes()


def test_part_writer_byte_identical_to_write_jsonl_empty_case(tmp_path: Path) -> None:
    via_write_jsonl = tmp_path / "a.jsonl"
    write_jsonl([], via_write_jsonl)

    via_part_writer = tmp_path / "b.jsonl"
    writer = JsonlPartWriter(via_part_writer)
    writer.finalize()

    assert via_part_writer.read_bytes() == via_write_jsonl.read_bytes() == b""


def test_part_writer_part_exists_final_does_not(tmp_path: Path) -> None:
    dest = tmp_path / "out.jsonl"
    JsonlPartWriter(dest)
    assert (tmp_path / "out.jsonl.part").exists()
    assert not dest.exists()


def test_part_writer_no_advance_on_injected_failure(tmp_path: Path) -> None:
    dest = tmp_path / "out.jsonl"
    writer = JsonlPartWriter(dest)
    writer.write(_events(1)[0])
    assert writer.count == 1
    offset_before = writer._last_good_offset  # noqa: SLF001

    writer._f = _FlakyFile(writer._f, fail_after=0)  # type: ignore[assignment]  # noqa: SLF001
    with pytest.raises(OSError, match="disk full"):
        writer.write(_events(1)[0])

    assert writer.count == 1
    assert writer._last_good_offset == offset_before  # noqa: SLF001
    assert writer.broken is True


def test_part_writer_broken_writes_are_silent_noops(tmp_path: Path) -> None:
    dest = tmp_path / "out.jsonl"
    writer = JsonlPartWriter(dest)
    writer._f = _FlakyFile(writer._f, fail_after=0)  # type: ignore[assignment]  # noqa: SLF001
    with pytest.raises(OSError):
        writer.write(_events(1)[0])
    assert writer.broken is True

    # A second write after broken must not raise or advance anything.
    writer.write(_events(1)[0])
    assert writer.count == 0


def test_part_writer_finalize_truncates_torn_tail(tmp_path: Path) -> None:
    dest = tmp_path / "out.jsonl"
    writer = JsonlPartWriter(dest)
    writer.write(_events(1)[0])  # good event
    writer._f = _FlakyFile(writer._f, fail_after=0)  # type: ignore[assignment]  # noqa: SLF001
    with pytest.raises(OSError):
        writer.write(_events(1)[0])  # writes a torn fragment, then raises

    writer.finalize()

    events = read_jsonl(dest)  # would raise json.JSONDecodeError if untruncated
    assert len(events) == 1


def test_part_writer_finalize_idempotent(tmp_path: Path) -> None:
    dest = tmp_path / "out.jsonl"
    writer = JsonlPartWriter(dest)
    writer.write(_events(1)[0])
    writer.finalize()
    writer.finalize()  # must not raise or touch the file again

    events = read_jsonl(dest)
    assert len(events) == 1


def test_part_writer_finalize_raises_and_keeps_part_on_failure(tmp_path: Path) -> None:
    """A failure during finalize (here: close()) must raise and leave the
    .part file in place — replace() only runs after a successful close, so
    the file is never renamed away."""
    dest = tmp_path / "out.jsonl"
    writer = JsonlPartWriter(dest)
    writer.write(_events(1)[0])

    class _CloseFails:
        def close(self) -> None:
            raise OSError("cannot close")

    writer._f = _CloseFails()  # type: ignore[assignment]  # noqa: SLF001

    with pytest.raises(OSError, match="cannot close"):
        writer.finalize()

    assert writer.part_path.exists()
    assert not dest.exists()


def test_part_writer_raises_file_exists_on_existing_part(tmp_path: Path) -> None:
    dest = tmp_path / "out.jsonl"
    (tmp_path / "out.jsonl.part").write_bytes(b"stale")
    with pytest.raises(FileExistsError):
        JsonlPartWriter(dest)


def test_part_writer_discard_removes_part(tmp_path: Path) -> None:
    dest = tmp_path / "out.jsonl"
    writer = JsonlPartWriter(dest)
    writer.write(_events(1)[0])
    writer.discard()

    assert not writer.part_path.exists()
    assert not dest.exists()


def test_part_writer_discard_never_raises_on_missing_file(tmp_path: Path) -> None:
    dest = tmp_path / "out.jsonl"
    writer = JsonlPartWriter(dest)
    writer.part_path.unlink()  # simulate the file vanishing out from under it
    writer.discard()  # must not raise
