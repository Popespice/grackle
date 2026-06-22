"""Tests for metadata.count weighting in TraceAggregates (Phase 9.1)."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any

from grackle.python_runtime.aggregates import TraceAggregates, build_seekable

if TYPE_CHECKING:
    from pathlib import Path


def _write_trace(path: Path, events: list[dict[str, Any]]) -> None:
    with path.open("w") as f:
        for e in events:
            f.write(json.dumps(e) + "\n")


# ---------------------------------------------------------------------------
# build / cumulative_heat with count weighting
# ---------------------------------------------------------------------------


def test_count_weight_applied(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    _write_trace(
        p,
        [
            {
                "event": "call",
                "node_id": "a",
                "ts_ns": 1,
                "thread_id": 1,
                "frame_depth": 0,
                "metadata": {"count": 3},
            },
        ],
    )
    agg = TraceAggregates.build(p)
    assert agg.cumulative_heat("a", 999) == 3


def test_missing_count_defaults_to_one(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    _write_trace(
        p,
        [
            {"event": "call", "node_id": "b", "ts_ns": 1, "thread_id": 1, "frame_depth": 0},
        ],
    )
    agg = TraceAggregates.build(p)
    assert agg.cumulative_heat("b", 999) == 1


def test_count_zero_coerced_to_one(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    _write_trace(
        p,
        [
            {
                "event": "call",
                "node_id": "c",
                "ts_ns": 1,
                "thread_id": 1,
                "frame_depth": 0,
                "metadata": {"count": 0},
            },
        ],
    )
    agg = TraceAggregates.build(p)
    assert agg.cumulative_heat("c", 999) == 1


def test_count_non_int_coerced_to_one(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    _write_trace(
        p,
        [
            {
                "event": "call",
                "node_id": "d",
                "ts_ns": 1,
                "thread_id": 1,
                "frame_depth": 0,
                "metadata": {"count": "lots"},
            },
        ],
    )
    agg = TraceAggregates.build(p)
    assert agg.cumulative_heat("d", 999) == 1


def test_multiple_weighted_events(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    _write_trace(
        p,
        [
            {
                "event": "call",
                "node_id": "n",
                "ts_ns": 1,
                "thread_id": 1,
                "frame_depth": 0,
                "metadata": {"count": 5},
            },
            {
                "event": "call",
                "node_id": "n",
                "ts_ns": 2,
                "thread_id": 1,
                "frame_depth": 0,
                "metadata": {"count": 3},
            },
        ],
    )
    agg = TraceAggregates.build(p)
    # After event 0: weight 5. After event 1: weight 8.
    assert agg.cumulative_heat("n", 1) == 5  # only event 0
    assert agg.cumulative_heat("n", 2) == 8  # both events


def test_unweighted_path_identical(tmp_path: Path) -> None:
    """Default weight=1 produces the same result as pre-9.1 (raw event count)."""
    p = tmp_path / "t.jsonl"
    events: list[dict[str, Any]] = [
        {"event": "call", "node_id": "x", "ts_ns": i, "thread_id": 1, "frame_depth": 0}
        for i in range(5)
    ]
    _write_trace(p, events)
    agg = TraceAggregates.build(p)
    assert agg.cumulative_heat("x", 5) == 5
    assert agg.cumulative_heat("x", 3) == 3
    assert agg.cumulative_heat("x", 0) == 0


# ---------------------------------------------------------------------------
# Defensive parsing — malformed metadata/count must never abort the scan
# ---------------------------------------------------------------------------


def test_metadata_null_tolerated(tmp_path: Path) -> None:
    """A present-but-null metadata must not crash the scan (regression: AttributeError)."""
    p = tmp_path / "t.jsonl"
    # Hand-author the line so metadata is literally null (the dict default in
    # event.get('metadata', {}) does NOT apply when the key is present).
    p.write_text(
        '{"event":"call","node_id":"n","ts_ns":1,"thread_id":1,"frame_depth":0,"metadata":null}\n'
    )
    agg = TraceAggregates.build(p)
    assert agg.cumulative_heat("n", 999) == 1
    _, agg2 = build_seekable(p)
    assert agg2.cumulative_heat("n", 999) == 1


def test_metadata_non_dict_tolerated(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    p.write_text(
        '{"event":"call","node_id":"n","ts_ns":1,"thread_id":1,"frame_depth":0,"metadata":[1,2]}\n'
    )
    agg = TraceAggregates.build(p)
    assert agg.cumulative_heat("n", 999) == 1


def test_count_nan_infinity_tolerated(tmp_path: Path) -> None:
    """json.loads accepts NaN/Infinity; int() on them would crash without the guard."""
    p = tmp_path / "t.jsonl"
    p.write_text(
        '{"event":"call","node_id":"a","ts_ns":1,"thread_id":1,"frame_depth":0,"metadata":{"count":NaN}}\n'
        '{"event":"call","node_id":"b","ts_ns":2,"thread_id":1,"frame_depth":0,"metadata":{"count":Infinity}}\n'
    )
    agg = TraceAggregates.build(p)
    assert agg.cumulative_heat("a", 999) == 1
    assert agg.cumulative_heat("b", 999) == 1


def test_count_bool_tolerated(tmp_path: Path) -> None:
    """bool is an int subclass; count:true must weight as 1, not be treated as numeric."""
    p = tmp_path / "t.jsonl"
    _write_trace(
        p,
        [
            {
                "event": "call",
                "node_id": "n",
                "ts_ns": 1,
                "thread_id": 1,
                "frame_depth": 0,
                "metadata": {"count": True},
            },
        ],
    )
    agg = TraceAggregates.build(p)
    assert agg.cumulative_heat("n", 999) == 1


def test_count_float_truncated(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    _write_trace(
        p,
        [
            {
                "event": "call",
                "node_id": "n",
                "ts_ns": 1,
                "thread_id": 1,
                "frame_depth": 0,
                "metadata": {"count": 2.9},
            },
        ],
    )
    agg = TraceAggregates.build(p)
    assert agg.cumulative_heat("n", 999) == 2


# ---------------------------------------------------------------------------
# build_seekable uses the same weighting
# ---------------------------------------------------------------------------


def test_build_seekable_count_weight(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    _write_trace(
        p,
        [
            {
                "event": "call",
                "node_id": "s",
                "ts_ns": 1,
                "thread_id": 1,
                "frame_depth": 0,
                "metadata": {"count": 7},
            },
        ],
    )
    _, agg = build_seekable(p)
    assert agg.cumulative_heat("s", 999) == 7


# ---------------------------------------------------------------------------
# top_k respects weighting
# ---------------------------------------------------------------------------


def test_top_k_weighted(tmp_path: Path) -> None:
    p = tmp_path / "t.jsonl"
    _write_trace(
        p,
        [
            {
                "event": "call",
                "node_id": "hot",
                "ts_ns": 1,
                "thread_id": 1,
                "frame_depth": 0,
                "metadata": {"count": 100},
            },
            {
                "event": "call",
                "node_id": "cold",
                "ts_ns": 2,
                "thread_id": 1,
                "frame_depth": 0,
                "metadata": {"count": 1},
            },
        ],
    )
    agg = TraceAggregates.build(p)
    top = agg.top_k(2, 99)
    assert top[0] == ("hot", 100)
    assert top[1] == ("cold", 1)
