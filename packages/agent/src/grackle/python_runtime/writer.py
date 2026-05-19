"""JSONL writer for trace events.

Serialises a sequence of ``TraceEvent`` dicts to a file, one JSON object per
line. Writes are atomic: the full output is written to a sibling ``.tmp`` file
and then ``Path.replace()``-d to the destination, preventing a partial file if
the process is interrupted.

See ``grackle.cache._atomic_write`` for the established project pattern.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path

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
    tmp.write_text("\n".join(lines) + ("\n" if lines else ""), encoding="utf-8")
    tmp.replace(dest)
    return len(lines)


def read_jsonl(path: Path) -> list[TraceEvent]:
    """Read a JSONL file and return a list of TraceEvent dicts.

    Blank lines are skipped. Raises ``json.JSONDecodeError`` if a non-blank
    line is not valid JSON.
    """
    events: list[TraceEvent] = []
    for raw_line in path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        events.append(json.loads(line))
    return events
