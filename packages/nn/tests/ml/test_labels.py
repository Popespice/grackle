"""Tests for grackle_nn.ml.labels (Phase 12.1, block M2)."""

from __future__ import annotations

import math
from pathlib import Path

import pytest

from grackle_nn.ml.labels import _event_weight, heat_from_jsonl, make_targets

_FIXTURES = Path(__file__).parents[4] / "fixtures"

# 8 lines: count=3 (int), count=True (bool -- rejected), count=2.7 (float,
# truncates to 2), missing "count" key (defaults to 1), a blank line, a
# malformed JSON line, an empty node_id (skipped), and a node with no
# "metadata" key at all (defaults to weight 1).
_EIGHT_LINES = "\n".join(
    [
        '{"event": "call", "node_id": "a", "metadata": {"count": 3}}',
        '{"event": "return", "node_id": "a", "metadata": {"count": true}}',
        '{"event": "call", "node_id": "b", "metadata": {"count": 2.7}}',
        '{"event": "return", "node_id": "b", "metadata": {}}',
        "",
        "{not valid json",
        '{"event": "call", "node_id": "", "metadata": {"count": 5}}',
        '{"event": "call", "node_id": "c"}',
    ]
)


def test_heat_from_jsonl_hand_written_eight_lines(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    trace.write_text(_EIGHT_LINES + "\n", encoding="utf-8")
    assert heat_from_jsonl(trace) == {"a": 4, "b": 3, "c": 1}


@pytest.mark.parametrize(
    "fixture",
    ["tiny-python-app", "tiny-node-app", "tiny-go-app", "tiny-rust-app", "value-capture"],
)
def test_heat_from_jsonl_matches_trace_aggregates_cumulative_heat_all(fixture: str) -> None:
    """Pins heat_from_jsonl against the shipped TraceAggregates forever.

    Imports ``grackle`` (allowed in tests only -- dev dep since 11.2);
    ``grackle_nn.ml`` itself never imports it. See ADR-0028/M8.
    """
    from grackle.python_runtime.aggregates import TraceAggregates

    trace_path = _FIXTURES / fixture / "trace.golden.jsonl"
    agg = TraceAggregates.build(trace_path)
    expected = agg.cumulative_heat_all(len(agg))
    assert heat_from_jsonl(trace_path) == expected


def test_make_targets_exact_values() -> None:
    node_ids = ["a", "b", "c"]
    heat = {"a": 3, "b": 1}
    y = make_targets(node_ids, heat)
    expected = [
        math.log1p(3) / math.log1p(3),
        math.log1p(1) / math.log1p(3),
        0.0,  # "c" not in heat -> count 0
    ]
    assert y.tolist() == pytest.approx(expected, abs=1e-12)


def test_make_targets_all_zero_heat_returns_zeros() -> None:
    node_ids = ["a", "b"]
    y = make_targets(node_ids, {})
    assert y.tolist() == [0.0, 0.0]


def test_make_targets_empty_node_ids() -> None:
    y = make_targets([], {})
    assert y.shape == (0,)


def test_non_string_node_id_is_skipped_not_crash(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    lines = [
        '{"event": "call", "node_id": ["a", "b"]}',  # unhashable -- must not crash
        '{"event": "call", "node_id": 42}',  # non-string -- must not silently coerce
        '{"event": "call", "node_id": "real"}',
    ]
    trace.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert heat_from_jsonl(trace) == {"real": 1}


def test_non_object_top_level_json_is_skipped_not_crash(tmp_path: Path) -> None:
    trace = tmp_path / "trace.jsonl"
    lines = ["5", "[1, 2]", "null", '"just a string"', '{"event": "call", "node_id": "real"}']
    trace.write_text("\n".join(lines) + "\n", encoding="utf-8")
    assert heat_from_jsonl(trace) == {"real": 1}


def test_event_weight_floors_non_positive_counts_at_one() -> None:
    assert _event_weight({"metadata": {"count": 0}}) == 1
    assert _event_weight({"metadata": {"count": -5}}) == 1


def test_event_weight_rejects_non_finite_floats() -> None:
    assert _event_weight({"metadata": {"count": float("nan")}}) == 1
    assert _event_weight({"metadata": {"count": float("inf")}}) == 1
    assert _event_weight({"metadata": {"count": float("-inf")}}) == 1
