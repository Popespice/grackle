"""Tests for grackle.python_runtime.aggregates (Phase 8.3).

Design notes:
- All fixtures use tmp_path so tests are hermetic.
- The public API (build / __len__ / cumulative_heat / coverage_count / top_k)
  is tested as a black box; internals (_hits, _first_seen) are not accessed.
- Sparse-k tests verify the approximation contract: result ≤ true count and
  differs by ≤ sparse_k.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from grackle.python_runtime.aggregates import TraceAggregates, build_seekable

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(node_id: str, index: int) -> dict[str, object]:
    return {
        "event": "call",
        "node_id": node_id,
        "ts_ns": index * 1_000_000,
        "thread_id": 1,
        "frame_depth": 0,
    }


def _write_jsonl(path: Path, events: list[dict[str, object]]) -> None:
    lines = [json.dumps(ev) for ev in events]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# test_build_empty
# ---------------------------------------------------------------------------


def test_build_empty(tmp_path: Path) -> None:
    """Empty file → len=0, all queries return 0 / empty."""
    f = tmp_path / "empty.jsonl"
    f.write_text("", encoding="utf-8")
    agg = TraceAggregates.build(f)
    assert len(agg) == 0
    assert agg.cumulative_heat("a", 0) == 0
    assert agg.cumulative_heat("a", 100) == 0
    assert agg.coverage_count(0) == 0
    assert agg.coverage_count(100) == 0
    assert agg.top_k(10, 100) == []


# ---------------------------------------------------------------------------
# test_cumulative_heat_simple
# ---------------------------------------------------------------------------


def test_cumulative_heat_simple(tmp_path: Path) -> None:
    """3 events for node A, 2 for node B; verify counts at various at_index values."""
    # Event order: A(0), B(1), A(2), A(3), B(4)
    events = [
        _make_event("A", 0),
        _make_event("B", 1),
        _make_event("A", 2),
        _make_event("A", 3),
        _make_event("B", 4),
    ]
    f = tmp_path / "simple.jsonl"
    _write_jsonl(f, events)
    agg = TraceAggregates.build(f)

    assert len(agg) == 5

    # at_index=0 → [0, 0) → nothing
    assert agg.cumulative_heat("A", 0) == 0
    assert agg.cumulative_heat("B", 0) == 0

    # at_index=1 → [0, 1) → event 0 (A)
    assert agg.cumulative_heat("A", 1) == 1
    assert agg.cumulative_heat("B", 1) == 0

    # at_index=2 → [0, 2) → events 0,1
    assert agg.cumulative_heat("A", 2) == 1
    assert agg.cumulative_heat("B", 2) == 1

    # at_index=5 → [0, 5) → all events
    assert agg.cumulative_heat("A", 5) == 3
    assert agg.cumulative_heat("B", 5) == 2

    # Unknown node
    assert agg.cumulative_heat("C", 5) == 0


# ---------------------------------------------------------------------------
# test_coverage_count
# ---------------------------------------------------------------------------


def test_coverage_count(tmp_path: Path) -> None:
    """Coverage grows as at_index increases."""
    # A first seen at 0, B first seen at 2, C first seen at 4
    events = [
        _make_event("A", 0),
        _make_event("A", 1),
        _make_event("B", 2),
        _make_event("A", 3),
        _make_event("C", 4),
    ]
    f = tmp_path / "cov.jsonl"
    _write_jsonl(f, events)
    agg = TraceAggregates.build(f)

    assert agg.coverage_count(0) == 0  # no events in [0, 0)
    assert agg.coverage_count(1) == 1  # A first seen at 0
    assert agg.coverage_count(2) == 1  # B not yet (first seen at 2)
    assert agg.coverage_count(3) == 2  # A (0) + B (2) both < 3
    assert agg.coverage_count(5) == 3  # A, B, C all seen


# ---------------------------------------------------------------------------
# test_top_k
# ---------------------------------------------------------------------------


def test_top_k(tmp_path: Path) -> None:
    """Verify ordering and k capping."""
    # A appears 4 times, B appears 2 times, C appears 1 time
    events = (
        [_make_event("A", i) for i in range(4)]
        + [_make_event("B", i) for i in range(4, 6)]
        + [_make_event("C", 6)]
    )
    f = tmp_path / "topk.jsonl"
    _write_jsonl(f, events)
    agg = TraceAggregates.build(f)

    result = agg.top_k(3, 7)
    assert len(result) == 3
    assert result[0] == ("A", 4)
    assert result[1] == ("B", 2)
    assert result[2] == ("C", 1)

    # k capping
    result2 = agg.top_k(2, 7)
    assert len(result2) == 2
    assert result2[0][0] == "A"
    assert result2[1][0] == "B"

    # at_index=0 → empty
    assert agg.top_k(10, 0) == []

    # k=0 → empty
    assert agg.top_k(0, 7) == []


def test_top_k_breaks_count_ties_by_node_id_not_trace_order(tmp_path: Path) -> None:
    """Equal counts are ordered by node_id, as documented — not by first-hit order.

    "z" is hit first, so a sort on count alone (stable) would keep it ahead of
    "a"; and with k=1 the tie-break decides which node survives the cut at all.
    (Campaign T4-2: test_top_k's counts are all distinct, so it cannot tell.)
    """
    events = [_make_event("z", 0), _make_event("m", 1), _make_event("a", 2)]
    f = tmp_path / "ties.jsonl"
    _write_jsonl(f, events)
    agg = TraceAggregates.build(f)

    assert agg.top_k(3, 3) == [("a", 1), ("m", 1), ("z", 1)]
    assert agg.top_k(1, 3) == [("a", 1)]


# ---------------------------------------------------------------------------
# test_sparse_k
# ---------------------------------------------------------------------------


def test_sparse_k(tmp_path: Path) -> None:
    """With sparse_k=2, approx counts are ≤ actual and bounded by the rounded window.

    The sparse approximation counts only hits at multiples-of-sparse_k indices
    and rounds at_index down to the nearest multiple.  For a fully dense trace
    (one hit per event index), the worst-case undercounting is at_index % sparse_k
    (tail events in the skipped window) plus up to sparse_k-1 boundary hits.
    The practical bound is: approx ≤ true, and the non-recorded fraction is
    ≤ true * (sparse_k - 1) / sparse_k in the densest case.  We test the strict
    monotonicity and the upper bound only.
    """
    sparse_k = 2
    # 10 events all for node A (indices 0..9)
    events = [_make_event("A", i) for i in range(10)]
    f = tmp_path / "sparse.jsonl"
    _write_jsonl(f, events)

    agg_full = TraceAggregates.build(f, sparse_k=1)
    agg_sparse = TraceAggregates.build(f, sparse_k=sparse_k)

    for at in range(11):
        true_count = agg_full.cumulative_heat("A", at)
        approx_count = agg_sparse.cumulative_heat("A", at)
        # Approximation must never exceed the true count
        assert approx_count <= true_count, f"at={at}: approx {approx_count} > true {true_count}"

    # Spot-check: at sparse boundaries the count is exact
    # at_index=4 → rounded=4, counts hits < 4 (indices 0,2) → 2
    # (true count at 4 is also 4; sparse misses 1 and 3)
    # The key guarantee: approx is monotonically non-decreasing
    prev = 0
    for at in range(11):
        approx = agg_sparse.cumulative_heat("A", at)
        assert approx >= prev, f"not monotone at at={at}: {approx} < {prev}"
        prev = approx


def test_sparse_k_answers_as_of_the_bucket_start(tmp_path: Path) -> None:
    """Pins the documented rounding: with sparse_k > 1, cumulative_heat(at) is
    the recorded-hit count as of floor(at / k) * k — constant across each
    aligned bucket [m*k, (m+1)*k).

    The round-down is not made redundant by recording only multiples of k:
    without it a mid-bucket query (at=5, k=2) would count the hit recorded at
    index 4. test_sparse_k checks only "≤ true" and monotonicity, which an
    unrounded query satisfies too, so it cannot see the rounding at all
    (campaign T4-2). Changing this is a contract change — update the
    module/method docstrings with it.
    """
    events = [_make_event("A", i) for i in range(10)]
    f = tmp_path / "sparse_exact.jsonl"
    _write_jsonl(f, events)
    agg = TraceAggregates.build(f, sparse_k=2)

    # Recorded hits: indices 0, 2, 4, 6, 8.
    heat = [agg.cumulative_heat("A", at) for at in range(11)]
    assert heat == [0, 0, 1, 1, 2, 2, 3, 3, 4, 4, 5]


@pytest.mark.parametrize("sparse_k", [1, 2, 3, 5])
def test_sparse_k_parametrized(tmp_path: Path, sparse_k: int) -> None:
    """Coverage uses full-resolution first-seen regardless of sparse_k."""
    events = [_make_event(f"n{i}", i) for i in range(10)]
    f = tmp_path / "sp_cov.jsonl"
    _write_jsonl(f, events)

    agg = TraceAggregates.build(f, sparse_k=sparse_k)
    # Coverage should always be exact (based on full-resolution first_seen)
    assert agg.coverage_count(5) == 5  # n0..n4 each first seen before index 5
    assert agg.coverage_count(10) == 10


# ---------------------------------------------------------------------------
# cumulative_heat_all
# ---------------------------------------------------------------------------


def test_cumulative_heat_all(tmp_path: Path) -> None:
    """cumulative_heat_all returns {node_id: count} for nodes with count > 0."""
    events = [
        _make_event("A", 0),
        _make_event("B", 1),
        _make_event("A", 2),
        _make_event("A", 3),
        _make_event("B", 4),
    ]
    f = tmp_path / "all.jsonl"
    _write_jsonl(f, events)
    agg = TraceAggregates.build(f)

    # at_index=0 → no events yet → empty (no zero-count entries)
    assert agg.cumulative_heat_all(0) == {}

    # Full trace → matches per-node cumulative_heat, zero counts excluded
    assert agg.cumulative_heat_all(5) == {"A": 3, "B": 2}

    # Partial window agrees with cumulative_heat for every node
    for at in range(6):
        expected = {
            nid: agg.cumulative_heat(nid, at)
            for nid in ("A", "B")
            if agg.cumulative_heat(nid, at) > 0
        }
        assert agg.cumulative_heat_all(at) == expected


# ---------------------------------------------------------------------------
# build_seekable — single-pass index + aggregates
# ---------------------------------------------------------------------------


def test_build_seekable_matches_separate_builds(tmp_path: Path) -> None:
    """build_seekable yields an index + aggregates equivalent to building each alone."""
    events = [
        _make_event("A", 0),
        _make_event("B", 1),
        _make_event("A", 2),
        _make_event("C", 3),
        _make_event("A", 4),
    ]
    f = tmp_path / "seekable.jsonl"
    _write_jsonl(f, events)

    idx, agg = build_seekable(f)
    agg_alone = TraceAggregates.build(f)

    # Same total event count from both structures and the standalone aggregates.
    assert len(idx) == 5
    assert len(agg) == 5
    assert len(agg) == len(agg_alone)

    # Aggregate queries agree with the standalone build.
    assert agg.cumulative_heat_all(5) == agg_alone.cumulative_heat_all(5)
    assert agg.coverage_count(5) == agg_alone.coverage_count(5)

    # The index reads back the same events at the same absolute positions, so
    # event index i (used by aggregates) lines up with offset slot i.
    window = idx.read_window(0, 5)
    assert [e["node_id"] for e in window] == ["A", "B", "A", "C", "A"]


def test_build_seekable_malformed_line_keeps_index_and_aggregates_aligned(
    tmp_path: Path,
) -> None:
    """A line that fails to parse still consumes an event slot in BOTH structures.

    The index gives it an offset slot, so the aggregates must advance their
    event index too — otherwise every later event's aggregate index is one
    short of the slot read_window serves it from (campaign T4-2).
    """
    f = tmp_path / "malformed.jsonl"
    f.write_text(
        json.dumps(_make_event("A", 0))
        + "\n"
        + "{not json\n"
        + json.dumps(_make_event("B", 2))
        + "\n",
        encoding="utf-8",
    )

    idx, agg = build_seekable(f)

    assert len(idx) == len(agg) == 3
    assert [e["node_id"] for e in idx.read_window(2, 1)] == ["B"]
    # B lives in slot 2, so it has not fired before at_index=3.
    assert agg.cumulative_heat("B", 2) == 0
    assert agg.cumulative_heat("B", 3) == 1
    assert agg.coverage_count(2) == 1
    assert agg.coverage_count(3) == 2
    # The standalone build numbers events the same way.
    agg_alone = TraceAggregates.build(f)
    assert len(agg_alone) == 3
    assert agg_alone.cumulative_heat("B", 2) == 0


def test_build_seekable_blank_lines_advance_offsets_but_take_no_slot(tmp_path: Path) -> None:
    """Blank (and whitespace-only) lines take no event slot, but their bytes
    still count toward the offset of every later line — the same contract
    JsonlIndex.build keeps. test_build_seekable_matches_separate_builds has no
    blank lines, so it cannot see a skipped offset advance (campaign T4-2).
    """
    f = tmp_path / "blanks.jsonl"
    f.write_text(
        json.dumps(_make_event("A", 0))
        + "\n\n\n"
        + json.dumps(_make_event("B", 1))
        + "\n   \n"
        + json.dumps(_make_event("C", 2))
        + "\n",
        encoding="utf-8",
    )

    idx, agg = build_seekable(f)

    assert len(idx) == len(agg) == 3
    assert [e["node_id"] for e in idx.read_window(0, 3)] == ["A", "B", "C"]
    assert agg.cumulative_heat_all(3) == {"A": 1, "B": 1, "C": 1}


def test_build_seekable_missing_file(tmp_path: Path) -> None:
    """build_seekable on a missing file returns empty (no raise)."""
    idx, agg = build_seekable(tmp_path / "nope.jsonl")
    assert len(idx) == 0
    assert len(agg) == 0
    assert agg.cumulative_heat_all(100) == {}
    assert idx.read_window(0, 10) == []


# ---------------------------------------------------------------------------
# Test campaign T5-8 (docs/test-campaigns/phase-12.md): a valid-JSON line
# that is not an object
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    raises=AttributeError,
    reason=(
        "T5-8: a JSON line that parses but is not an object raises "
        "AttributeError out of the aggregate builders instead of being skipped "
        "like an unparsable line (docs/test-campaigns/phase-12.md)"
    ),
)
@pytest.mark.parametrize("line", ["[1, 2]", "42", '"str"', "null"])
def test_non_object_json_line_is_skipped_like_a_malformed_one(tmp_path: Path, line: str) -> None:
    """Both builders already skip a line that fails to parse, keeping its slot
    so offsets and aggregate indices stay aligned. A line that parses to a
    non-object is no more an event than an unparsable one — but `.get` on it
    raises, so one such line fails `grackle diff`, `grackle learn`,
    `serve --trace-source` and session load for the whole file."""
    event = {
        "event": "call",
        "node_id": "a.py:f",
        "ts_ns": 0,
        "thread_id": 1,
        "frame_depth": 0,
        "metadata": {},
    }
    trace = tmp_path / "trace.jsonl"
    trace.write_text(f"{json.dumps(event)}\n{line}\n{json.dumps(event)}\n", encoding="utf-8")

    assert TraceAggregates.build(trace).cumulative_heat_all(3) == {"a.py:f": 2}
    _, agg = build_seekable(trace)
    assert agg.cumulative_heat_all(3) == {"a.py:f": 2}
