"""Pure helpers for the V8 precise-coverage (live heat) channel of ADR-0022.

``Profiler.startPreciseCoverage({callCount: true, detailed: true})`` records *exact*
per-function call counts with negligible overhead. Polling ``takePreciseCoverage``
every ~250 ms yields a cheap, real-time heat signal — the **live** channel,
delivered over ``--stream`` (mid-execution Timeline + heat-map).

Coverage reports cumulative call counts keyed by *offset* into each script, not by
line. These pure helpers turn poll-over-poll snapshots into coarse ``call``
``TraceEvent``s:

- :class:`OffsetLineMap` maps a script offset → 1-based line, so offset-based
  coverage reaches the same ``(path, line) → node_id`` index the sampling channel
  uses.
- :func:`iter_coverage_deltas` is the **production** path the launcher polls with:
  one O(functions) pass over a ``takePreciseCoverage`` result + the prior baseline,
  returning the positive per-function deltas and the next lean ``{key: count}``
  baseline. :func:`normalize_precise_coverage` + :func:`diff_coverage` are the
  simpler **reference** decomposition it is unit-tested against for equivalence
  (``normalize`` flattens one result into ``{(scriptId, startOffset): count}`` via
  ``ranges[0]``; ``diff`` subtracts the previous snapshot).
- :func:`coverage_event` builds **one coarse** ``call`` event per active function
  per poll — ``frame_depth: 0``, ``metadata: {live: true, count: delta}``. One
  event per active function per tick (not one per call) keeps the live stream
  bounded for hot/recursive functions.

**Heat precision (important).** Because the existing heat-map / aggregation
consumers count *events* (one hit per event) and do not read ``metadata.count``,
the rendered live heat is **activity-coarse**: a function lights up once per poll
in which it was active, not in proportion to its call count. The exact per-poll
call delta rides in ``metadata.count`` for any consumer that opts to weight by it;
magnitude-faithful heat (and ``grackle diff`` input) comes from the sampling
channel (``trace()`` → ``--connect`` / ``-o``), which emits real per-call frames.

All functions are pure and Node-free → fixture-driven unit tests.
"""

from __future__ import annotations

import bisect
from typing import TYPE_CHECKING, Any, TypedDict

from grackle.adapters.base import new_trace_event

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

    from grackle.adapters.base import TraceEvent


class CoverageEntry(TypedDict):
    url: str
    function_name: str
    start_offset: int
    count: int


class CoverageDelta(TypedDict):
    script_id: str
    url: str
    function_name: str
    start_offset: int
    delta: int


# (script_id, start_offset) — unique per function even when names collide.
CoverageKey = tuple[str, int]

# Lean per-function cumulative-count snapshot used as the poll-over-poll baseline.
# Holds only the count (all diffing reads), avoiding a full CoverageEntry rebuild
# every poll. Keyed identically to normalize_precise_coverage's output.
CoverageCounts = dict[CoverageKey, int]


class OffsetLineMap:
    """Maps a source offset to its 1-based line number.

    Built from the on-disk ``.ts`` source (read raw, BOM-stripped, newlines
    preserved — see ``launcher._line_map_for_url``). Type-stripping preserves line
    boundaries, so the on-disk lines match the positions V8 reports; reading from
    disk avoids enabling the Debugger domain, which closes the inspector when the
    script finishes. Offsets are interpreted as code-point offsets into the source
    string, which matches V8's offsets for the ASCII/BMP TypeScript this targets.
    (An astral-plane character could shift the mapping by one UTF-16 unit — a
    documented edge of 8.5; the resolver's name/file fallback covers a miss.)
    """

    def __init__(self, source: str) -> None:
        # Offset at which each line starts. Line 1 starts at offset 0.
        starts = [0]
        for i, ch in enumerate(source):
            if ch == "\n":
                starts.append(i + 1)
        self._line_starts = starts

    def line_of(self, offset: int) -> int:
        """Return the 1-based line containing *offset* (clamped to >= 1)."""
        # bisect_right gives the count of line-starts <= offset == the line number.
        return max(1, bisect.bisect_right(self._line_starts, offset))


def _as_int(value: Any, default: int = 0) -> int:
    """Coerce an untrusted V8 numeric field to ``int``, else *default*.

    A precise-coverage payload is external input: a missing/null/non-numeric
    ``startOffset`` or ``count`` must not raise. A bare ``int(None)`` is a
    ``TypeError`` — not a ``CDPError`` — so without this it would escape the
    launcher's ``except CDPError`` and abort the entire ``--stream`` session.
    """
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def normalize_precise_coverage(
    result: Iterable[Mapping[str, Any]],
) -> dict[CoverageKey, CoverageEntry]:
    """Flatten one ``takePreciseCoverage`` result into per-function call counts.

    Args:
        result: The ``result`` array from ``Profiler.takePreciseCoverage``: one
            entry per script, each with ``functions[].ranges[]``. The function-level
            range (``ranges[0]``) carries the function's start offset and call
            ``count``.
    """
    out: dict[CoverageKey, CoverageEntry] = {}
    for script in result:
        script_id = str(script.get("scriptId", ""))
        url = str(script.get("url", ""))
        for fn in script.get("functions") or []:
            ranges = fn.get("ranges") or []
            if not ranges:
                continue
            head = ranges[0]
            if not isinstance(head, dict):
                continue  # malformed range entry — skip rather than crash
            start_offset = _as_int(head.get("startOffset"))
            count = _as_int(head.get("count"))
            out[(script_id, start_offset)] = {
                "url": url,
                "function_name": str(fn.get("functionName", "")),
                "start_offset": start_offset,
                "count": count,
            }
    return out


def diff_coverage(
    prev: Mapping[CoverageKey, CoverageEntry],
    curr: Mapping[CoverageKey, CoverageEntry],
) -> list[CoverageDelta]:
    """Per-function call deltas (``curr − prev``) for functions called since *prev*.

    Only positive deltas are returned (functions cannot be un-called); functions
    present in *prev* but absent from *curr* are ignored.
    """
    deltas: list[CoverageDelta] = []
    for (script_id, start_offset), entry in curr.items():
        before = prev.get((script_id, start_offset))
        delta = entry["count"] - (before["count"] if before is not None else 0)
        if delta > 0:
            deltas.append(
                {
                    "script_id": script_id,
                    "url": entry["url"],
                    "function_name": entry["function_name"],
                    "start_offset": start_offset,
                    "delta": delta,
                }
            )
    return deltas


def iter_coverage_deltas(
    result: Iterable[Mapping[str, Any]],
    prev: Mapping[CoverageKey, int],
) -> tuple[list[CoverageDelta], CoverageCounts]:
    """One pass: positive per-function deltas + the next baseline ``{key: count}``.

    Behaviour-equivalent to ``diff_coverage(prev_entries, normalize_precise_coverage(
    result))`` for the deltas, but it (a) scans the raw ``takePreciseCoverage`` payload
    once, (b) allocates a ``CoverageDelta`` only for functions with a *positive* delta,
    and (c) returns a lean ``{key: count}`` baseline instead of rebuilding a full
    ``CoverageEntry`` map. The launcher feeds the returned counts back in as ``prev``.

    Tie-breaking matches :func:`normalize_precise_coverage` exactly: a later function
    sharing a ``(scriptId, startOffset)`` overwrites an earlier one (last-write-wins),
    so the *last* occurrence's url/name/count are reported and carried forward.
    """
    counts: CoverageCounts = {}
    # url/name kept per key so the LAST occurrence wins, mirroring the dict overwrite
    # in normalize_precise_coverage (cheaper than a full CoverageEntry per function).
    meta: dict[CoverageKey, tuple[str, str]] = {}
    for script in result:
        script_id = str(script.get("scriptId", ""))
        url = str(script.get("url", ""))
        for fn in script.get("functions") or []:
            ranges = fn.get("ranges") or []
            if not ranges:
                continue
            head = ranges[0]
            if not isinstance(head, dict):
                continue  # malformed range entry — skip rather than crash
            start_offset = _as_int(head.get("startOffset"))
            count = _as_int(head.get("count"))
            key = (script_id, start_offset)
            counts[key] = count
            meta[key] = (url, str(fn.get("functionName", "")))

    deltas: list[CoverageDelta] = []
    for key, count in counts.items():
        delta = count - prev.get(key, 0)
        if delta > 0:
            script_id, start_offset = key
            url, function_name = meta[key]
            deltas.append(
                {
                    "script_id": script_id,
                    "url": url,
                    "function_name": function_name,
                    "start_offset": start_offset,
                    "delta": delta,
                }
            )
    return deltas, counts


def coverage_event(node_id: str, delta: int, ts_ns: int, *, thread_id: int = 0) -> TraceEvent:
    """One coarse live ``call`` event for an active function (``frame_depth: 0``)."""
    return new_trace_event("call", node_id, ts_ns, thread_id, 0, {"live": True, "count": delta})
