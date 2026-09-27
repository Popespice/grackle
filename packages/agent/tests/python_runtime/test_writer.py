"""Tests for python_runtime.writer — JSONL write/read atomicity."""

from __future__ import annotations

import contextlib
import errno
import io
import json
import os
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest

from grackle.python_runtime.writer import JsonlPartWriter, read_jsonl, write_jsonl

if TYPE_CHECKING:
    from collections.abc import Buffer

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


def test_write_jsonl_emits_lf_never_crlf(tmp_path: Path) -> None:
    """write_jsonl writes bytes, not text, so grackle only ever emits LF.

    Discriminating on Windows only — a text-mode write (``Path.write_text``,
    what this used to be) applies universal-newline translation there and
    emits CRLF, diverging from JsonlPartWriter/RecordingSink and making the
    same ``grackle trace -o`` produce different bytes per OS. On POSIX both
    forms produce LF, so the Windows CI leg is what actually guards this.
    """
    dest = tmp_path / "out.jsonl"
    write_jsonl(_events(3), dest)
    raw = dest.read_bytes()
    assert b"\r\n" not in raw
    assert raw.count(b"\n") == 3
    assert raw.endswith(b"}\n")


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


def test_part_writer_paths_are_pinned_to_construction_cwd(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A relative destination is anchored at construction, not at finalize.

    The tracer runs the traced script in-process, so a script calling
    os.chdir() moves the cwd out from under an in-flight writer. Both paths
    must already be absolute or the finalize rename resolves against the wrong
    directory.
    """
    home = tmp_path / "home"
    home.mkdir()
    away = tmp_path / "away"
    away.mkdir()
    monkeypatch.chdir(home)

    writer = JsonlPartWriter(Path("out.jsonl"))
    assert writer.final_path.is_absolute()
    assert writer.part_path.is_absolute()

    writer.write(_events(1)[0])
    monkeypatch.chdir(away)  # the traced script wanders off
    writer.finalize()

    assert (home / "out.jsonl").exists()
    assert not (away / "out.jsonl").exists()
    assert not (home / "out.jsonl.part").exists()


def test_part_writer_discard_reports_whether_the_part_is_gone(tmp_path: Path) -> None:
    """discard() returns False when the .part survives, so callers can log it.

    An undeleted .part blocks the next exclusive-create at that path, so
    RecordingSink needs to be able to say so rather than failing silently.
    """
    dest = tmp_path / "out.jsonl"
    writer = JsonlPartWriter(dest)
    writer.write(_events(1)[0])
    assert writer.discard() is True

    stuck = JsonlPartWriter(tmp_path / "stuck.jsonl")

    def _explode(*_args: object, **_kwargs: object) -> None:
        raise OSError("simulated undeletable file")

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(Path, "unlink", _explode)
        assert stuck.discard() is False
    stuck.part_path.unlink(missing_ok=True)


def test_part_writer_discard_never_raises_on_missing_file(tmp_path: Path) -> None:
    dest = tmp_path / "out.jsonl"
    writer = JsonlPartWriter(dest)
    # Close the handle BEFORE unlinking: Windows refuses to delete a file that
    # any process still holds open (WinError 32), so unlinking first would fail
    # the test on the very platform it is meant to protect. discard() then hits
    # both branches it exists to survive — a redundant close and a missing file.
    writer._f.close()  # noqa: SLF001
    writer.part_path.unlink()  # simulate the file vanishing out from under it
    writer.discard()  # must not raise
    assert not writer.part_path.exists()


# ---------------------------------------------------------------------------
# Test campaign T5-4 (docs/test-campaigns/phase-12.md): disk-full surfaces
# below the write buffer
# ---------------------------------------------------------------------------


class _DiskFullRaw(io.RawIOBase):
    """The OS side of a filling disk, placed where the real one sits: BELOW
    the BufferedWriter. Accepts *limit* bytes in total, short-writes whatever
    still fits of the write that crosses it, then fails every later write
    with ENOSPC — what write(2) does as a volume fills.

    _FlakyFile above injects its failure ABOVE the buffer (it wraps the
    BufferedWriter), where the failing write() is the one that raises. That
    models a failure inside grackle's own write call, not a full disk."""

    def __init__(self, path: Path, limit: int) -> None:
        super().__init__()
        self._file = path.open("r+b", buffering=0)
        self._room = limit

    def writable(self) -> bool:
        return True

    def seekable(self) -> bool:
        return True

    def write(self, b: Buffer) -> int:
        if self._room <= 0:
            raise OSError(errno.ENOSPC, os.strerror(errno.ENOSPC))
        n = self._file.write(bytes(memoryview(b)[: self._room]))
        self._room -= n
        return n

    def seek(self, offset: int, whence: int = 0) -> int:
        return self._file.seek(offset, whence)

    def tell(self) -> int:
        return self._file.tell()

    def truncate(self, size: int | None = None) -> int:
        return self._file.truncate(size)

    def fileno(self) -> int:
        return self._file.fileno()

    def close(self) -> None:
        if not self.closed:
            self._file.close()
        super().close()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T5-4: ENOSPC surfaces at a later flush or at close(), so the "
        "truncate-salvage never fires and the tracked offset/count overstate "
        "what reached disk (docs/test-campaigns/phase-12.md)"
    ),
)
@pytest.mark.parametrize(
    ("limit", "n_events"),
    [
        # Everything fits in the 8 KiB buffer: every write() "succeeds" and
        # the disk-full error first appears at finalize()'s close().
        (1_000, 40),
        # The first buffer flush fits, a later one does not: the error
        # appears at some later write(), not the one whose bytes were lost.
        (10_000, 150),
    ],
    ids=["surfaces-at-close", "surfaces-at-a-later-write"],
)
def test_part_writer_disk_full_below_the_buffer_leaves_only_complete_lines(
    tmp_path: Path, limit: int, n_events: int
) -> None:
    """Whatever file survives a disk-full run — finalized or .part — holds
    only complete lines, and writer.count (what the CLI reports as "wrote N
    events" and RecordingSink registers as event_count) matches them."""
    dest = tmp_path / "out.jsonl"
    writer = JsonlPartWriter(dest)
    writer._f.close()  # noqa: SLF001
    writer._f = io.BufferedWriter(_DiskFullRaw(writer.part_path, limit))  # noqa: SLF001

    for event in _events(n_events):
        try:
            writer.write(event)
        except OSError:
            break
    with contextlib.suppress(OSError):
        writer.finalize()

    data = (dest if dest.exists() else writer.part_path).read_bytes()
    assert data.endswith(b"\n"), f"torn tail survived: {data[-40:]!r}"
    lines = data.decode("utf-8").splitlines()
    for line in lines:
        json.loads(line)
    assert writer.count == len(lines)


# ---------------------------------------------------------------------------
# Test campaign T5-6 (docs/test-campaigns/phase-12.md): finalize() failing
# at replace()
# ---------------------------------------------------------------------------


def _fail_part_replace_once(monkeypatch: pytest.MonkeyPatch) -> None:
    """Make the first .part -> final rename fail the way Windows refuses to
    replace a destination another process holds open; later renames go
    through (the other process let go)."""
    real_replace = Path.replace
    failed = False

    def _replace(self: Path, target: Any) -> Path:
        nonlocal failed
        if self.name.endswith(".part") and not failed:
            failed = True
            raise PermissionError(13, "destination is open in another process")
        return real_replace(self, target)

    monkeypatch.setattr(Path, "replace", _replace)


def test_part_writer_replace_failure_keeps_a_complete_closed_part(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """T5-6 (probed, pinned): replace() is the last step, so by then the
    .part is already closed and complete — finalize() raises, the .part
    holds every event, and nothing still has it open (Windows refuses to
    unlink or rename a file with an open handle)."""
    _fail_part_replace_once(monkeypatch)
    dest = tmp_path / "out.jsonl"
    writer = JsonlPartWriter(dest)
    for event in _events(3):
        writer.write(event)

    with pytest.raises(PermissionError):
        writer.finalize()

    assert not dest.exists()
    assert writer._f.closed  # noqa: SLF001
    assert read_jsonl(writer.part_path) == _events(3)


@pytest.mark.parametrize(
    "broken",
    [
        False,
        pytest.param(
            True,
            marks=pytest.mark.xfail(
                strict=True,
                reason=(
                    "T5-6: a retried finalize() on a broken writer re-runs "
                    "truncate() on the already-closed handle and raises "
                    "ValueError (docs/test-campaigns/phase-12.md)"
                ),
            ),
        ),
    ],
    ids=["intact-writer", "broken-writer"],
)
def test_part_writer_finalize_retry_after_replace_failure_completes(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path, broken: bool
) -> None:
    """finalize() leaves the .part in place on failure, so a caller can retry
    once whatever blocked the rename lets go. Its contract is "raises
    OSError and leaves .part in place if any step fails" — a retry that
    raises ValueError instead escapes any caller written to that contract.
    No caller retries today, which is why this is latent."""
    _fail_part_replace_once(monkeypatch)
    dest = tmp_path / "out.jsonl"
    writer = JsonlPartWriter(dest)
    writer.write(_events(1)[0])
    if broken:
        writer._f = _FlakyFile(writer._f, fail_after=0)  # type: ignore[assignment]  # noqa: SLF001
        with pytest.raises(OSError, match="disk full"):
            writer.write(_events(1)[0])

    with pytest.raises(PermissionError):
        writer.finalize()
    writer.finalize()

    assert read_jsonl(dest) == _events(1)
    assert not writer.part_path.exists()
