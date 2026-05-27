"""Byte-offset index for fast random-access reads from a JSONL trace file.

``JsonlIndex`` scans a JSONL file once at construction and records the byte
offset of each non-blank line.  ``read_window`` then seeks directly to any
event by index in O(1), reading only the requested count rather than the whole
file.

Memory overhead: 8 bytes per event (one 64-bit int offset).  At 10 M events
that is ~80 MiB — acceptable for the Phase 7.3 MVP.  A sparse index (offset
every K lines + intra-block scan) is a Phase 8 option if profiling shows the
memory is a problem in practice.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from grackle.adapters.base import TraceEvent


class JsonlIndex:
    """Byte-offset random-access index for a JSONL trace file.

    Build once with ``JsonlIndex.build(path)``; then ``read_window`` gives O(1)
    seek + O(count) read for any slice of the file.
    """

    def __init__(self, path: Path, offsets: list[int]) -> None:
        self._path = path
        self._offsets = offsets

    @classmethod
    def build(cls, path: Path) -> JsonlIndex:
        """One-pass scan: record the byte offset of every non-blank line.

        Args:
            path: Path to the JSONL trace file.

        Returns:
            A ``JsonlIndex`` ready for ``read_window`` queries.
        """
        offsets: list[int] = []
        with path.open("rb") as f:
            offset = 0
            for raw_line in f:
                stripped = raw_line.strip()
                if stripped:
                    offsets.append(offset)
                offset += len(raw_line)
        return cls(path, offsets)

    def __len__(self) -> int:
        """Total number of events indexed (including blank-line-skipped events)."""
        return len(self._offsets)

    def read_window(self, start: int, count: int) -> list[TraceEvent]:
        """Read *count* events starting at absolute index *start*.

        Indices are clamped: ``start`` is clamped to ``[0, len(self)]`` and
        the effective end is clamped to ``len(self)``.  Out-of-range requests
        return a partial result rather than raising.

        Args:
            start: Zero-based index of the first event to read.
            count: Maximum number of events to return.

        Returns:
            List of ``TraceEvent`` dicts.  May be shorter than *count* if the
            request extends past the end of the file.
        """
        total = len(self._offsets)
        start = max(0, min(start, total))
        end = min(start + count, total)
        if start >= end:
            return []
        events: list[TraceEvent] = []
        with self._path.open("rb") as f:
            for i in range(start, end):
                f.seek(self._offsets[i])
                raw_line = f.readline()
                try:
                    events.append(json.loads(raw_line.decode("utf-8")))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    # Skip lines that cannot be parsed — the file may have been
                    # mutated or truncated after the index was built at startup.
                    continue
        return events
