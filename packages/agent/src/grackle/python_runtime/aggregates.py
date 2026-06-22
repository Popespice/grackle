"""Prefix-sum aggregates over a JSONL trace file.

Design
------
``TraceAggregates.build`` scans the trace file once at startup and builds two
in-memory indexes:

1. **Hit-index** (``_hits: dict[str, list[int]]``): maps each ``node_id`` to a
   sorted list of the event indices at which that node was touched.  Because we
   process events in sequential order the lists are naturally sorted, so no
   explicit sort is required.  ``bisect_right(hits, at_index)`` gives the count
   of events in [0, at_index) in O(log N) per node per query.

2. **First-seen index** (``_first_seen: dict[str, int]``): maps each ``node_id``
   to the index of its first event.  From this we build a sorted list of
   ``(first_seen_index,)`` values (one per distinct node) to answer coverage
   queries in O(log N): ``bisect_right(sorted_first, at_index)`` counts distinct
   nodes whose first event precedes ``at_index``.

Count-weighting
---------------
Events may carry ``metadata.count`` (an integer ≥ 1) indicating that a single
event represents multiple logical invocations — Go coverage events use this to
encode exact per-function call counts in one compact event.  A parallel
``_weight_prefix`` array stores the cumulative weight alongside ``_hits``:
``weight_prefix[i]`` is the sum of ``count`` values for hits 0..i inclusive.
``cumulative_heat`` returns ``weight_prefix[pos - 1]`` instead of ``pos``.

For events without ``metadata.count`` (Python/Node sampling), the default
weight is 1, so ``weight_prefix == [1, 2, 3, …]`` and ``cumulative_heat``
returns the same value as before — behaviour is **byte-identical** for all
existing trace files.

Sparse mode
-----------
When ``sparse_k > 1``, only event indices that are exact multiples of
``sparse_k`` are recorded in the hit-index (i.e. index 0, ``sparse_k``,
``2*sparse_k``, …).  ``cumulative_heat`` rounds ``at_index`` down to the
nearest multiple before the bisect, so results are approximations that are
≤ the true count and differ by at most ``sparse_k - 1`` events.  First-seen
tracking is NOT sparsified: it records the exact first event index regardless
of ``sparse_k`` so coverage queries remain accurate.  When count-weighting is
combined with sparse mode, weights accumulate only over recorded (every-k-th)
hits, so the result is a weighted approximation subject to the same ≤ true-
count guarantee.

Thread safety
-------------
The class is **read-only after construction** — all public methods are safe to
call concurrently from multiple threads or asyncio tasks without locking.
"""

from __future__ import annotations

import bisect
import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from grackle.python_runtime.jsonl_index import JsonlIndex


class TraceAggregates:
    """In-memory aggregate indexes built from a single pass over a JSONL trace.

    Use ``TraceAggregates.build(path)`` to construct; then query with
    ``cumulative_heat``, ``coverage_count``, and ``top_k``.
    """

    def __init__(
        self,
        hits: dict[str, list[int]],
        first_seen: dict[str, int],
        total: int,
        sparse_k: int,
        weight_prefix: dict[str, list[int]] | None = None,
    ) -> None:
        self._hits = hits
        self._first_seen = first_seen
        self._total = total
        self._sparse_k = sparse_k
        # Per-node cumulative weight array parallel to _hits.
        # weight_prefix[node_id][i] = sum of metadata.count for hits 0..i.
        # Defaults to None (treated as all-weight-1 in cumulative_heat).
        self._weight_prefix = weight_prefix
        # Pre-build sorted list of first-seen indices for O(log N) coverage.
        self._sorted_first: list[int] = sorted(first_seen.values())

    # ------------------------------------------------------------------
    # Construction
    # ------------------------------------------------------------------

    @classmethod
    def build(cls, path: Path, *, sparse_k: int = 1) -> TraceAggregates:
        """One-pass scan of the JSONL trace file at *path*.

        Args:
            path:     Path to the JSONL trace file.
            sparse_k: When > 1, only record event indices that are multiples
                      of *sparse_k* in the hit index.  Values < 1 are treated
                      as 1 (no sparsification).

        Returns:
            A ``TraceAggregates`` instance ready for querying.
        """
        if sparse_k < 1:
            sparse_k = 1

        hits: dict[str, list[int]] = {}
        weight_prefix: dict[str, list[int]] = {}
        first_seen: dict[str, int] = {}
        index = 0

        try:
            with path.open("rb") as f:
                for raw_line in f:
                    stripped = raw_line.strip()
                    if not stripped:
                        continue
                    try:
                        event = json.loads(stripped.decode("utf-8"))
                    except (json.JSONDecodeError, UnicodeDecodeError):
                        index += 1
                        continue

                    node_id: str = event.get("node_id", "")
                    if node_id:
                        # Track first-seen at full resolution
                        if node_id not in first_seen:
                            first_seen[node_id] = index

                        # Record in hit index (sparse or full)
                        if sparse_k == 1 or index % sparse_k == 0:
                            raw_count = event.get("metadata", {}).get("count", 1)
                            count = (
                                max(1, int(raw_count)) if isinstance(raw_count, (int, float)) else 1
                            )
                            node_hits = hits.setdefault(node_id, [])
                            node_hits.append(index)
                            wp = weight_prefix.setdefault(node_id, [])
                            wp.append((wp[-1] if wp else 0) + count)

                    index += 1
        except OSError:
            # Empty or missing file — return empty aggregates
            pass

        return cls(hits, first_seen, index, sparse_k, weight_prefix)

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    def __len__(self) -> int:
        """Total number of events scanned (including events with no node_id)."""
        return self._total

    def cumulative_heat(self, node_id: str, at_index: int) -> int:
        """Return the weighted hit count for *node_id* in events [0, at_index).

        Each event contributes ``metadata.count`` (default 1) to the total.
        For traces without count metadata the result equals the raw event count
        (byte-identical to pre-9.1 behaviour).

        When built with ``sparse_k > 1``, ``at_index`` is rounded down to the
        nearest multiple of ``sparse_k`` before the lookup, so the result is
        an approximation (≤ true count, differs by ≤ sparse_k - 1).
        """
        hits = self._hits.get(node_id)
        if hits is None:
            return 0
        if self._sparse_k > 1:
            # Round down to nearest multiple
            at_index = (at_index // self._sparse_k) * self._sparse_k
        pos = bisect.bisect_right(hits, at_index - 1)
        if pos == 0:
            return 0
        if self._weight_prefix is not None:
            wp = self._weight_prefix.get(node_id)
            if wp is not None:
                return wp[pos - 1]
        return pos

    def coverage_count(self, at_index: int) -> int:
        """Return number of distinct node_ids with at least one event in [0, at_index).

        Always uses full-resolution first-seen data regardless of ``sparse_k``.
        """
        if not self._sorted_first:
            return 0
        return bisect.bisect_right(self._sorted_first, at_index - 1)

    def top_k(self, k: int, at_index: int) -> list[tuple[str, int]]:
        """Return top-k (node_id, count) pairs by cumulative count at *at_index*.

        Pairs are sorted descending by count.  Ties are broken by node_id
        lexicographically to make output deterministic.

        Args:
            k:        Maximum number of results to return.
            at_index: Upper-bound index (exclusive) for cumulative count.

        Returns:
            List of at most *k* ``(node_id, count)`` tuples, sorted descending.
        """
        if k <= 0:
            return []
        entries: list[tuple[str, int]] = []
        for node_id in self._hits:
            count = self.cumulative_heat(node_id, at_index)
            if count > 0:
                entries.append((node_id, count))
        # Sort descending by count, then ascending by node_id for stable ordering
        entries.sort(key=lambda x: (-x[1], x[0]))
        return entries[:k]

    @property
    def node_ids(self) -> frozenset[str]:
        """All node IDs that have at least one event recorded in this trace."""
        return frozenset(self._hits.keys())

    def cumulative_heat_all(self, at_index: int) -> dict[str, int]:
        """Return ``{node_id: count}`` for every node with count > 0 at *at_index*.

        Equivalent to calling :meth:`cumulative_heat` for every recorded node
        and dropping zero counts.  Honours ``sparse_k`` rounding identically.
        """
        return {
            node_id: count
            for node_id in self._hits
            if (count := self.cumulative_heat(node_id, at_index)) > 0
        }


def build_seekable(path: Path, *, sparse_k: int = 1) -> tuple[JsonlIndex, TraceAggregates]:
    """Build a ``JsonlIndex`` and ``TraceAggregates`` from a single file scan.

    Both structures need the same forward pass over the JSONL file — the index
    records byte offsets of non-blank lines, the aggregates parse those lines
    for per-node hit counts.  Building them together avoids the double-scan of
    calling ``JsonlIndex.build`` and ``TraceAggregates.build`` separately.

    The event index assigned by the aggregates increments once per non-blank
    line (parse failures included), which is exactly the offset list position
    recorded by the index, so ``index[i]`` and aggregate event-index ``i`` refer
    to the same line.

    Args:
        path:     Path to the JSONL trace file.
        sparse_k: See :meth:`TraceAggregates.build`.

    Returns:
        ``(JsonlIndex, TraceAggregates)`` over one pass.  On ``OSError`` (missing
        or unreadable file) both are empty.
    """
    from grackle.python_runtime.jsonl_index import JsonlIndex as _JsonlIndex

    if sparse_k < 1:
        sparse_k = 1

    offsets: list[int] = []
    hits: dict[str, list[int]] = {}
    weight_prefix: dict[str, list[int]] = {}
    first_seen: dict[str, int] = {}
    index = 0

    try:
        with path.open("rb") as f:
            offset = 0
            for raw_line in f:
                line_len = len(raw_line)
                stripped = raw_line.strip()
                if not stripped:
                    offset += line_len
                    continue
                # Non-blank line: its offset-list position == its aggregate index.
                offsets.append(offset)
                offset += line_len
                try:
                    event = json.loads(stripped.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    index += 1
                    continue
                node_id: str = event.get("node_id", "")
                if node_id:
                    if node_id not in first_seen:
                        first_seen[node_id] = index
                    if sparse_k == 1 or index % sparse_k == 0:
                        raw_count = event.get("metadata", {}).get("count", 1)
                        count = max(1, int(raw_count)) if isinstance(raw_count, (int, float)) else 1
                        hits.setdefault(node_id, []).append(index)
                        wp = weight_prefix.setdefault(node_id, [])
                        wp.append((wp[-1] if wp else 0) + count)
                index += 1
    except OSError:
        pass

    return (
        _JsonlIndex(path, offsets),
        TraceAggregates(hits, first_seen, index, sparse_k, weight_prefix),
    )
