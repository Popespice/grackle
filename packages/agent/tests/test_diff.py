"""Tests for grackle.diff — differential analysis module."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from grackle.diff import DiffEntry, diff_trace_vs_static, diff_trace_vs_trace, has_regression
from grackle.python_runtime.aggregates import TraceAggregates

if TYPE_CHECKING:
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def make_aggregates(hits: dict[str, list[int]], total: int = 100) -> TraceAggregates:
    """Construct a TraceAggregates directly from hit lists (no file I/O)."""
    first_seen = {nid: lst[0] for nid, lst in hits.items() if lst}
    return TraceAggregates(hits, first_seen, total, 1)


def write_jsonl(path: Path, events: list[dict[str, object]]) -> None:
    path.write_text("\n".join(json.dumps(e) for e in events), encoding="utf-8")


# ---------------------------------------------------------------------------
# diff_trace_vs_static
# ---------------------------------------------------------------------------


class TestDiffTraceVsStatic:
    def test_all_cold_when_no_hits(self) -> None:
        agg = make_aggregates({})
        entries = diff_trace_vs_static(["a", "b", "c"], agg)
        assert len(entries) == 3
        assert all(e["status"] == "cold" for e in entries)

    def test_all_touched(self) -> None:
        agg = make_aggregates({"a": [0], "b": [1], "c": [2]})
        entries = diff_trace_vs_static(["a", "b", "c"], agg)
        assert all(e["status"] == "touched" for e in entries)

    def test_mixed_touched_cold(self) -> None:
        agg = make_aggregates({"a": [0], "b": [1]})
        entries = diff_trace_vs_static(["a", "b", "c"], agg)
        by_id = {e["node_id"]: e for e in entries}
        assert by_id["a"]["status"] == "touched"
        assert by_id["b"]["status"] == "touched"
        assert by_id["c"]["status"] == "cold"

    def test_cold_nodes_sorted_first(self) -> None:
        agg = make_aggregates({"a": [0]})
        entries = diff_trace_vs_static(["a", "b", "c"], agg)
        # cold nodes (b, c) should come before touched (a)
        statuses = [e["status"] for e in entries]
        assert statuses.index("cold") < statuses.index("touched")

    def test_count_a_populated_count_b_zero(self) -> None:
        agg = make_aggregates({"a": [0, 1, 2]})
        entries = diff_trace_vs_static(["a"], agg)
        e = entries[0]
        assert e["count_a"] == 3
        assert e["count_b"] == 0
        assert e["delta"] == 0

    def test_at_index_respected(self) -> None:
        # hits at 0, 5, 10 — querying at_index=3 should see only index 0
        agg = make_aggregates({"x": [0, 5, 10]})
        entries = diff_trace_vs_static(["x"], agg, at_index=3)
        assert entries[0]["count_a"] == 1

    def test_empty_node_ids(self) -> None:
        agg = make_aggregates({"a": [0]})
        entries = diff_trace_vs_static([], agg)
        assert entries == []


# ---------------------------------------------------------------------------
# diff_trace_vs_trace
# ---------------------------------------------------------------------------


class TestDiffTraceVsTrace:
    def test_hotter_when_b_has_more_hits(self) -> None:
        agg_a = make_aggregates({"x": [0, 1]})
        agg_b = make_aggregates({"x": [0, 1, 2, 3]})
        entries = diff_trace_vs_trace(agg_a, agg_b)
        assert entries[0]["node_id"] == "x"
        assert entries[0]["status"] == "hotter"
        assert entries[0]["delta"] > 0

    def test_colder_when_b_has_fewer_hits(self) -> None:
        agg_a = make_aggregates({"x": [0, 1, 2, 3]})
        agg_b = make_aggregates({"x": [0, 1]})
        entries = diff_trace_vs_trace(agg_a, agg_b)
        assert entries[0]["status"] == "colder"
        assert entries[0]["delta"] < 0

    def test_new_when_only_in_b(self) -> None:
        agg_a = make_aggregates({})
        agg_b = make_aggregates({"y": [0]})
        entries = diff_trace_vs_trace(agg_a, agg_b)
        assert entries[0]["status"] == "new"
        assert entries[0]["count_a"] == 0
        assert entries[0]["count_b"] > 0

    def test_gone_when_only_in_a(self) -> None:
        agg_a = make_aggregates({"y": [0]})
        agg_b = make_aggregates({})
        entries = diff_trace_vs_trace(agg_a, agg_b)
        assert entries[0]["status"] == "gone"
        assert entries[0]["count_b"] == 0

    def test_same_when_equal_counts(self) -> None:
        agg_a = make_aggregates({"z": [0, 1]})
        agg_b = make_aggregates({"z": [0, 1]})
        entries = diff_trace_vs_trace(agg_a, agg_b)
        assert entries[0]["status"] == "same"
        assert entries[0]["delta"] == 0

    def test_same_when_both_zero(self) -> None:
        # Node is in node_ids but has no hits in either session
        agg_a = make_aggregates({})
        agg_b = make_aggregates({})
        entries = diff_trace_vs_trace(agg_a, agg_b, node_ids=["phantom"])
        assert len(entries) == 1
        assert entries[0]["status"] == "same"

    def test_explicit_node_ids_expands_universe(self) -> None:
        agg_a = make_aggregates({"x": [0]})
        agg_b = make_aggregates({"x": [0]})
        entries = diff_trace_vs_trace(agg_a, agg_b, node_ids=["x", "extra"])
        ids = {e["node_id"] for e in entries}
        assert "x" in ids
        assert "extra" in ids  # extra has same (both zero)

    def test_severity_sort_order(self) -> None:
        # hotter should come before new, which comes before gone, etc.
        agg_a = make_aggregates({"hot_node": [0], "gone_node": [0]})
        agg_b = make_aggregates({"hot_node": [0, 1, 2, 3], "new_node": [0]})
        entries = diff_trace_vs_trace(agg_a, agg_b)
        statuses = [e["status"] for e in entries]
        # hotter comes before new
        assert statuses.index("hotter") < statuses.index("new")
        # gone comes after new
        assert statuses.index("new") < statuses.index("gone")

    def test_empty_both_sessions(self) -> None:
        agg_a = make_aggregates({})
        agg_b = make_aggregates({})
        assert diff_trace_vs_trace(agg_a, agg_b) == []

    def test_at_index_args_respected(self) -> None:
        # node "x" has hits at indices 0..9 in both.
        # With at_index_a=5 and at_index_b=10 the counts differ.
        hits = list(range(10))
        agg_a = make_aggregates({"x": hits}, total=10)
        agg_b = make_aggregates({"x": hits}, total=10)
        # a query at 5 vs 10: a sees 5 hits, b sees 10 → hotter
        entries = diff_trace_vs_trace(agg_a, agg_b, at_index_a=5, at_index_b=10)
        assert entries[0]["status"] == "hotter"


# ---------------------------------------------------------------------------
# has_regression
# ---------------------------------------------------------------------------


class TestHasRegression:
    def test_true_when_hotter_entry(self) -> None:
        entries: list[DiffEntry] = [
            DiffEntry(node_id="x", status="hotter", count_a=1, count_b=5, delta=4)
        ]
        assert has_regression(entries) is True

    def test_false_when_no_hotter(self) -> None:
        entries: list[DiffEntry] = [
            DiffEntry(node_id="x", status="same", count_a=1, count_b=1, delta=0),
            DiffEntry(node_id="y", status="colder", count_a=5, count_b=2, delta=-3),
        ]
        assert has_regression(entries) is False

    def test_false_on_empty(self) -> None:
        assert has_regression([]) is False


# ---------------------------------------------------------------------------
# grackle diff CLI  (integration)
# ---------------------------------------------------------------------------


class TestDiffCli:
    def test_exits_zero_when_no_regression(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from grackle.cli import diff as diff_cmd

        a = tmp_path / "a.jsonl"
        b = tmp_path / "b.jsonl"
        events = [{"event": "call", "node_id": "x", "ts_ns": 0, "thread_id": 1, "frame_depth": 0}]
        write_jsonl(a, events)
        write_jsonl(b, events)

        runner = CliRunner()
        result = runner.invoke(diff_cmd, [str(a), str(b)])
        assert result.exit_code == 0

    def test_exits_one_when_regression(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from grackle.cli import diff as diff_cmd

        a = tmp_path / "a.jsonl"
        b = tmp_path / "b.jsonl"
        ev = {"event": "call", "node_id": "x", "ts_ns": 0, "thread_id": 1, "frame_depth": 0}
        write_jsonl(a, [ev])
        write_jsonl(b, [ev, ev, ev])  # 3× more calls → hotter

        runner = CliRunner()
        result = runner.invoke(diff_cmd, [str(a), str(b)])
        assert result.exit_code == 1

    def test_json_format_output(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from grackle.cli import diff as diff_cmd

        a = tmp_path / "a.jsonl"
        b = tmp_path / "b.jsonl"
        ev = {"event": "call", "node_id": "x", "ts_ns": 0, "thread_id": 1, "frame_depth": 0}
        write_jsonl(a, [ev])
        write_jsonl(b, [ev, ev])

        runner = CliRunner()
        result = runner.invoke(diff_cmd, [str(a), str(b), "--format", "json"])
        # exit 1 (regression) but output is valid JSON
        data = json.loads(result.output)
        assert isinstance(data, list)
        assert data[0]["node_id"] == "x"
        assert data[0]["status"] == "hotter"

    def test_only_flag_filters_output(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from grackle.cli import diff as diff_cmd

        a = tmp_path / "a.jsonl"
        b = tmp_path / "b.jsonl"
        ev_x = {"event": "call", "node_id": "x", "ts_ns": 0, "thread_id": 1, "frame_depth": 0}
        ev_y = {"event": "call", "node_id": "y", "ts_ns": 0, "thread_id": 1, "frame_depth": 0}
        write_jsonl(a, [ev_x])
        write_jsonl(b, [ev_x, ev_x, ev_y])  # x hotter, y new

        runner = CliRunner()
        result = runner.invoke(diff_cmd, [str(a), str(b), "--only", "new", "--format", "json"])
        data = json.loads(result.output)
        assert all(e["status"] == "new" for e in data)
        assert any(e["node_id"] == "y" for e in data)

    def test_empty_files_exit_zero(self, tmp_path: Path) -> None:
        from click.testing import CliRunner

        from grackle.cli import diff as diff_cmd

        a = tmp_path / "a.jsonl"
        b = tmp_path / "b.jsonl"
        a.write_text("")
        b.write_text("")

        runner = CliRunner()
        result = runner.invoke(diff_cmd, [str(a), str(b)])
        assert result.exit_code == 0

    def test_only_filter_notes_hidden_regression(self, tmp_path: Path) -> None:
        # --only colder hides the hotter rows; the exit code is still 1, so the
        # text output must explain why (otherwise the non-zero exit is a mystery).
        from click.testing import CliRunner

        from grackle.cli import diff as diff_cmd

        a = tmp_path / "a.jsonl"
        b = tmp_path / "b.jsonl"
        ev_x = {"event": "call", "node_id": "x", "ts_ns": 0, "thread_id": 1, "frame_depth": 0}
        ev_y = {"event": "call", "node_id": "y", "ts_ns": 0, "thread_id": 1, "frame_depth": 0}
        # x: 2 hits in A, 1 in B -> colder.  y: 1 in A, 3 in B -> hotter.
        write_jsonl(a, [ev_x, ev_x, ev_y])
        write_jsonl(b, [ev_x, ev_y, ev_y, ev_y])

        runner = CliRunner()
        result = runner.invoke(diff_cmd, [str(a), str(b), "--only", "colder"])
        assert result.exit_code == 1  # regression still drives the exit code
        assert "hotter node(s) not shown" in result.output
        assert "--only colder" in result.output

    def test_only_no_matches_reports_empty(self, tmp_path: Path) -> None:
        # --only gone when there are no gone nodes -> a clear "(no nodes...)" line.
        from click.testing import CliRunner

        from grackle.cli import diff as diff_cmd

        a = tmp_path / "a.jsonl"
        b = tmp_path / "b.jsonl"
        ev = {"event": "call", "node_id": "x", "ts_ns": 0, "thread_id": 1, "frame_depth": 0}
        write_jsonl(a, [ev])
        write_jsonl(b, [ev])  # x is "same"; no gone nodes

        runner = CliRunner()
        result = runner.invoke(diff_cmd, [str(a), str(b), "--only", "gone"])
        assert result.exit_code == 0
        assert "no nodes with status 'gone'" in result.output


# ---------------------------------------------------------------------------
# TraceAggregates.node_ids (new property tested here)
# ---------------------------------------------------------------------------


def test_node_ids_property() -> None:
    agg = make_aggregates({"a": [0], "b": [1, 2]})
    assert agg.node_ids == frozenset({"a", "b"})


def test_node_ids_empty() -> None:
    agg = make_aggregates({})
    assert agg.node_ids == frozenset()
