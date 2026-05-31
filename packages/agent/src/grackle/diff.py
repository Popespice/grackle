"""Differential analysis between two trace sessions, or a trace vs. the static graph.

Two diff modes (both pure — no I/O, no side effects):

1. **trace-vs-static** (:func:`diff_trace_vs_static`): given the node IDs in the
   static graph and a single :class:`~grackle.python_runtime.aggregates.TraceAggregates`,
   classify each node as ``"touched"`` (≥1 hit) or ``"cold"`` (0 hits).

2. **trace-vs-trace** (:func:`diff_trace_vs_trace`): compare two
   :class:`~grackle.python_runtime.aggregates.TraceAggregates` instances (baseline A
   vs. comparison B) over a shared universe of node IDs and classify each node as:

   - ``"new"``    — present in B, absent in A (0 hits in A, ≥1 in B)
   - ``"gone"``   — present in A, absent in B (≥1 hit in A, 0 in B)
   - ``"hotter"`` — more hits in B than A (potential regression)
   - ``"colder"`` — fewer hits in B than A
   - ``"same"``   — equal hits in both (including both zero)

Callers control the file-scan lifecycle by building
:class:`~grackle.python_runtime.aggregates.TraceAggregates` objects beforehand via
:func:`~grackle.python_runtime.aggregates.build_seekable` or
:meth:`~grackle.python_runtime.aggregates.TraceAggregates.build`.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal, TypedDict

if TYPE_CHECKING:
    from grackle.python_runtime.aggregates import TraceAggregates

DiffStatus = Literal["touched", "cold", "new", "gone", "hotter", "colder", "same"]

# Sort key for each status — lower = shown first in output
_STATUS_ORDER: dict[str, int] = {
    "hotter": 0,  # most actionable (regression)
    "new": 1,
    "gone": 2,
    "colder": 3,
    "same": 4,
    "cold": 5,  # trace-vs-static cold nodes
    "touched": 6,
}


class DiffEntry(TypedDict):
    """Result of classifying one node in a diff."""

    node_id: str
    #: One of the :data:`DiffStatus` literals — kept ``str`` so JSON round-trips cleanly.
    status: str
    #: Hit count in the baseline session (session A, or current session for vs-static).
    count_a: int
    #: Hit count in the comparison session (session B); 0 for trace-vs-static diffs.
    count_b: int
    #: ``count_b - count_a`` (0 for trace-vs-static diffs).
    delta: int


def diff_trace_vs_static(
    node_ids: list[str],
    aggregates: TraceAggregates,
    at_index: int | None = None,
) -> list[DiffEntry]:
    """Classify every node in *node_ids* as ``"touched"`` or ``"cold"``.

    Args:
        node_ids:   All node IDs from the static graph (the universe to classify).
        aggregates: Trace aggregates for the session under analysis.
        at_index:   Upper-bound event index (exclusive).  Defaults to the full
                    session (``len(aggregates)``).

    Returns:
        One :class:`DiffEntry` per node ID, cold nodes first (most actionable),
        then touched nodes, both groups sorted by ``node_id`` for determinism.
    """
    idx = at_index if at_index is not None else len(aggregates)
    entries: list[DiffEntry] = []
    for nid in node_ids:
        count = aggregates.cumulative_heat(nid, idx)
        status: str = "touched" if count > 0 else "cold"
        entries.append(DiffEntry(node_id=nid, status=status, count_a=count, count_b=0, delta=0))
    entries.sort(key=lambda e: (_STATUS_ORDER.get(e["status"], 9), e["node_id"]))
    return entries


def diff_trace_vs_trace(
    aggregates_a: TraceAggregates,
    aggregates_b: TraceAggregates,
    node_ids: list[str] | None = None,
    at_index_a: int | None = None,
    at_index_b: int | None = None,
) -> list[DiffEntry]:
    """Classify every node across two trace sessions.

    The universe of nodes compared is the union of nodes seen in either session
    (i.e. nodes in ``aggregates_a.node_ids | aggregates_b.node_ids``), plus any
    extra IDs supplied via *node_ids* (e.g. the full static-graph node list).
    Nodes that are in *node_ids* but have 0 hits in both sessions are classified
    as ``"same"`` and appear at the bottom of the output.

    Args:
        aggregates_a:  Aggregates for the baseline session (A).
        aggregates_b:  Aggregates for the comparison session (B).
        node_ids:      Optional explicit universe (e.g. from ``grackle parse``
                       output).  When ``None``, the union of nodes observed in
                       the two sessions is used.
        at_index_a:    Upper-bound index for session A; defaults to full session.
        at_index_b:    Upper-bound index for session B; defaults to full session.

    Returns:
        One :class:`DiffEntry` per node, sorted by severity:
        ``hotter`` → ``new`` → ``gone`` → ``colder`` → ``same``.
    """
    idx_a = at_index_a if at_index_a is not None else len(aggregates_a)
    idx_b = at_index_b if at_index_b is not None else len(aggregates_b)

    all_ids: set[str] = set(aggregates_a.node_ids | aggregates_b.node_ids)
    if node_ids is not None:
        all_ids |= set(node_ids)

    entries: list[DiffEntry] = []
    for nid in all_ids:
        ca = aggregates_a.cumulative_heat(nid, idx_a)
        cb = aggregates_b.cumulative_heat(nid, idx_b)
        delta = cb - ca
        if ca == 0 and cb > 0:
            status = "new"
        elif ca > 0 and cb == 0:
            status = "gone"
        elif delta > 0:
            status = "hotter"
        elif delta < 0:
            status = "colder"
        else:
            status = "same"
        entries.append(DiffEntry(node_id=nid, status=status, count_a=ca, count_b=cb, delta=delta))

    entries.sort(key=lambda e: (_STATUS_ORDER.get(e["status"], 9), e["node_id"]))
    return entries


def has_regression(entries: list[DiffEntry]) -> bool:
    """Return ``True`` if any entry is classified as ``"hotter"`` (regression).

    Intended for CI use via ``grackle diff A.jsonl B.jsonl``; a non-zero exit
    code is appropriate when this returns ``True``.
    """
    return any(e["status"] == "hotter" for e in entries)
