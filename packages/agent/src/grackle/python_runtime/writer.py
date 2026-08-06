"""JSONL writer for trace events.

Serialises a sequence of ``TraceEvent`` dicts to a file, one JSON object per
line. Writes are atomic: the full output is written to a sibling ``.tmp`` file
and then ``Path.replace()``-d to the destination, preventing a partial file if
the process is interrupted.

See ``grackle.cache._atomic_write`` for the established project pattern.

Also home to :class:`JsonlPartWriter` — the *incremental* counterpart used
when the whole run should not be buffered in memory (Phase 12.0). Where
``write_jsonl`` collects a complete, in-memory sequence and writes it once,
``JsonlPartWriter`` writes one event at a time to a ``.part`` file as it
arrives, so a killed process keeps everything written so far.
"""

from __future__ import annotations

import contextlib
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping
    from pathlib import Path
    from typing import Any, BinaryIO

    from grackle.adapters.base import TraceEvent


def write_jsonl(events: Iterable[TraceEvent], dest: Path) -> int:
    """Write *events* to *dest* as JSONL (one JSON object per line).

    The write is atomic: output goes to a sibling tmp file, then ``Path.replace()``
    swaps it into place.

    The tmp path is constructed by appending ``".tmp"`` to the destination's
    name — NOT by ``dest.with_suffix(".tmp")`` — because
    ``with_suffix`` only replaces the last suffix, so ``foo.tar.gz`` would
    become ``foo.tar.tmp`` and collide if multiple files share a stem.
    Appending preserves the full filename ("foo.tar.gz.tmp") for
    collision-free atomic swaps.

    Written in **binary** mode with an explicit ``\\n`` terminator, so the
    bytes on disk are identical on every platform. Text mode would apply
    universal-newline translation and emit CRLF on Windows — which would
    (a) diverge from :class:`JsonlPartWriter` and ``RecordingSink``, both of
    which have always written LF, and (b) make the same ``grackle trace -o``
    invocation produce different bytes on different OSes. JSONL is an
    LF-delimited format; ``read_jsonl`` accepts either, but grackle only
    ever *emits* LF.

    Args:
        events: Iterable of ``TraceEvent`` dicts.
        dest:   Destination path (created if it does not exist; parent must
                exist).

    Returns:
        Number of events written.
    """
    lines: list[str] = []
    for event in events:
        lines.append(json.dumps(event, ensure_ascii=False))

    tmp = dest.parent / (dest.name + ".tmp")
    body = "\n".join(lines) + ("\n" if lines else "")
    tmp.write_bytes(body.encode("utf-8"))
    tmp.replace(dest)
    return len(lines)


def read_jsonl(path: Path) -> list[TraceEvent]:
    """Read a JSONL file and return a list of TraceEvent dicts.

    Blank lines are skipped. Raises ``json.JSONDecodeError`` if a non-blank
    line is not valid JSON.

    Uses ``str.split("\\n")`` rather than ``str.splitlines()`` to match the
    binary-``\\n`` line splitting in ``JsonlIndex.build``.  Both treat only
    the ASCII newline (U+000A) as a line boundary.  With ``ensure_ascii=False``
    in ``write_jsonl``, JSON values may embed U+0085 / U+2028 / U+2029 as raw
    characters; ``splitlines()`` would incorrectly treat those as additional
    line boundaries and break JSON parsing of the affected lines.
    """
    events: list[TraceEvent] = []
    for raw_line in path.read_text(encoding="utf-8").split("\n"):
        line = raw_line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events


class JsonlPartWriter:
    """Incrementally writes JSONL events to ``<final_path>.part``, finalizing
    into *final_path* via truncate-then-atomic-rename.

    The policy-free core extracted from
    :class:`~grackle.python_runtime.recording_sink.RecordingSink` (Phase 9.3,
    ADR-0020 amendment) and generalized in Phase 12.0 so the CLI's ``-o`` and
    ``--stream`` tee paths can persist incrementally too. Binary mode: each
    event is encoded to UTF-8 bytes with an explicit ``\\n`` terminator, so
    the tracked byte offset is exact on every platform including Windows, and
    a torn write can be salvaged with a single ``truncate(offset)``.

    Unlike :func:`write_jsonl` (one atomic write of a complete, in-memory
    list), this class writes one event at a time as it arrives — a killed
    process keeps every fully-written event in the ``.part`` file instead of
    losing the whole run. There is no flush/fsync on the hot path: the
    durability target is process-kill (the kernel page cache survives
    process death, even a SIGKILL), not power loss — a coarse time-based
    flush is possible future work, not built here.

    ``write()`` and :meth:`finalize` RAISE on I/O failure, and
    :meth:`finalize` LEAVES the ``.part`` file in place on failure — this
    class has no opinion on whether a failure should discard or keep the
    partial data; callers decide (``RecordingSink`` discards; the CLI keeps
    and reports the surviving ``.part`` — the file IS the product there).

    Raises:
        FileExistsError: if *final_path*'s ``.part`` sibling already exists —
            opening in exclusive-create mode makes a collision (e.g. a stale
            ``.part`` from a prior kill, left uncleared by the caller) fail
            loudly instead of silently truncating another writer's file.
    """

    def __init__(self, final_path: Path) -> None:
        # Anchored to the cwd at construction time. The Python tracer runs the
        # traced script IN-PROCESS via runpy, so a script that calls os.chdir()
        # moves the agent's cwd between the .part being opened and finalize()'s
        # rename — a relative path would then resolve the rename against the
        # NEW directory and fail with FileNotFoundError, stranding the events
        # under the original cwd. absolute(), not resolve(): the goal is to pin
        # the cwd, not to follow symlinks the user deliberately pointed at.
        self.final_path = final_path.absolute()
        # Name-append, never with_suffix — see write_jsonl's docstring for
        # why (multi-suffix filenames like "foo.tar.gz" would collide).
        self.part_path = self.final_path.parent / (self.final_path.name + ".part")
        self.count = 0
        self.broken = False
        self._last_good_offset = 0
        self._finalized = False
        self._f: BinaryIO = self.part_path.open("xb")

    def write(self, event: Mapping[str, Any]) -> None:
        """Append one event as one JSON line.

        Silently no-ops once broken (a caller need not re-check after
        catching the first failure). On the first I/O failure, marks the
        writer broken and RE-RAISES — the caller decides what happens next
        (RecordingSink swallows and logs; the CLI propagates via the
        tracer's sink-exception path).
        """
        if self.broken:
            return
        try:
            data = (json.dumps(event, ensure_ascii=False) + "\n").encode("utf-8")
            self._f.write(data)
            # Advance count and offset together, only on a fully successful
            # write, so they can never disagree — a partial/failed write
            # leaves both untouched, and finalize()'s truncate discards
            # exactly the partial bytes.
            self.count += 1
            self._last_good_offset += len(data)
        except Exception:
            self.broken = True
            raise

    def finalize(self) -> None:
        """Truncate any torn tail, close, and atomically rename to *final_path*.

        Idempotent on success — a second call after a successful finalize is
        a no-op. Deliberately does NOT special-case zero events: renaming an
        empty ``.part`` to *final_path* produces a byte-identical empty file
        to ``write_jsonl([], dest)``; discarding an empty recording is
        wrapper policy (``RecordingSink``), not this class's concern.

        Raises OSError and leaves ``.part`` in place if any step fails.
        """
        if self._finalized:
            return
        if self.broken:
            self._f.truncate(self._last_good_offset)
        self._f.close()
        self.part_path.replace(self.final_path)
        self._finalized = True

    def discard(self) -> bool:
        """Close (best-effort) and remove the ``.part`` file.

        Never raises — this is a last-resort cleanup path with no useful
        recovery on failure. Returns True when the ``.part`` file is gone
        afterwards, False when it could not be removed — Windows refuses to
        unlink a file whose handle is still open, so a discard following a
        *failed* ``close()`` leaves an orphan behind, and an orphaned
        ``.part`` blocks the next exclusive-create at that path until
        something sweeps it. Callers that have somewhere to log (see
        ``RecordingSink``) surface that rather than losing it silently.
        """
        with contextlib.suppress(OSError):
            self._f.close()
        try:
            self.part_path.unlink(missing_ok=True)
        except OSError:
            return False
        return True
