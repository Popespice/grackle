"""Campaign T8-5 (docs/test-campaigns/phase-12.md): the algebra of ``diff.py``.

Over generated pairs of traces (a node sequence with optional
``metadata.count`` weights, built into real ``TraceAggregates`` from files):

- **Identity.** ``diff(A, A)`` reports every node ``same`` with delta 0 and no
  regression, at any playhead.
- **Inverse.** ``diff(A, B)`` and ``diff(B, A)`` classify the same nodes, with
  ``hotter``/``colder`` and ``new``/``gone`` swapped, counts swapped and every
  delta negated.
- **Oracle.** Every entry's counts equal a naive recount, its status follows
  the documented rule, each node of the documented universe appears exactly
  once, and the output is in severity order with a ``node_id`` tie-break.
- **Metamorphic.** Appending events to B never produces ``gone`` or
  ``colder``; B = A twice over makes every node of A ``hotter``.
- **Static.** ``diff_trace_vs_static`` marks exactly the nodes with a positive
  count ``touched``, and agrees with a trace-vs-trace diff against an empty
  baseline (``touched`` there is ``new`` here).
"""

from __future__ import annotations

import itertools
import json
from typing import TYPE_CHECKING

import pytest
from hypothesis import given
from hypothesis import strategies as st

from grackle.diff import (
    DiffEntry,
    diff_trace_vs_static,
    diff_trace_vs_trace,
    has_regression,
)
from grackle.python_runtime.aggregates import TraceAggregates

if TYPE_CHECKING:
    from pathlib import Path

_NODES = ("a.py:f", "a.py:g", "b.py:<module>", "c/d.py:C.m", "e.py:h", "热.py:f")
_INVERSE = {"hotter": "colder", "colder": "hotter", "new": "gone", "gone": "new", "same": "same"}
_ORDER = ("hotter", "new", "gone", "colder", "same")

# A trace: (node, weight) per event; weight None means no metadata.count.
trace_st = st.lists(
    st.tuples(st.sampled_from(_NODES), st.one_of(st.none(), st.integers(1, 5))), max_size=30
)
extra_ids_st = st.one_of(
    st.none(), st.lists(st.sampled_from((*_NODES, "static/only.py:x", "z.py:y")), max_size=4)
)


class _Files:
    """Writes each trace to a file and builds it. ``build`` reads the whole
    file eagerly, so three rotating paths are enough (and the nightly profile
    does not litter the temp dir with one file per example)."""

    def __init__(self, base: Path) -> None:
        self.base = base
        self.n = itertools.count()

    def build(self, trace: list[tuple[str, int | None]]) -> TraceAggregates:
        path = self.base / f"trace-{next(self.n) % 3}.jsonl"
        lines = []
        for node, weight in trace:
            event: dict[str, object] = {"event": "call", "node_id": node, "ts_ns": 0}
            if weight is not None:
                event["metadata"] = {"count": weight}
            lines.append(json.dumps(event))
        path.write_text("".join(f"{line}\n" for line in lines), encoding="utf-8")
        return TraceAggregates.build(path)


@pytest.fixture(scope="module")
def files(tmp_path_factory: pytest.TempPathFactory) -> _Files:
    return _Files(tmp_path_factory.mktemp("t8_5"))


def _naive(trace: list[tuple[str, int | None]], at: int) -> dict[str, int]:
    out: dict[str, int] = {}
    for node, weight in trace[: max(0, at)]:
        out[node] = out.get(node, 0) + (weight or 1)
    return out


def _by_node(entries: list[DiffEntry]) -> dict[str, DiffEntry]:
    out = {e["node_id"]: e for e in entries}
    assert len(out) == len(entries), "a node appears twice"
    return out


def _status(ca: int, cb: int) -> str:
    if ca == 0 and cb > 0:
        return "new"
    if ca > 0 and cb == 0:
        return "gone"
    return "hotter" if cb > ca else "colder" if cb < ca else "same"


_at = st.one_of(st.none(), st.integers(-2, 33))


# ===========================================================================
# Properties
# ===========================================================================


@given(trace=trace_st, extra=extra_ids_st, at=_at, rebuild=st.booleans())
def test_a_trace_diffed_against_itself_is_all_same(
    files: _Files,
    trace: list[tuple[str, int | None]],
    extra: list[str] | None,
    at: int | None,
    rebuild: bool,
) -> None:
    a = files.build(trace)
    b = files.build(trace) if rebuild else a
    entries = diff_trace_vs_trace(a, b, extra, at, at)
    assert {e["status"] for e in entries} <= {"same"}
    assert all(e["delta"] == 0 and e["count_a"] == e["count_b"] for e in entries)
    assert not has_regression(entries)
    assert set(_by_node(entries)) == set(a.node_ids) | set(extra or ())


@given(
    trace_a=trace_st,
    trace_b=trace_st,
    extra=extra_ids_st,
    at_a=_at,
    at_b=_at,
)
def test_swapping_the_sessions_inverts_every_entry(
    files: _Files,
    trace_a: list[tuple[str, int | None]],
    trace_b: list[tuple[str, int | None]],
    extra: list[str] | None,
    at_a: int | None,
    at_b: int | None,
) -> None:
    a, b = files.build(trace_a), files.build(trace_b)
    forward = _by_node(diff_trace_vs_trace(a, b, extra, at_a, at_b))
    backward = _by_node(diff_trace_vs_trace(b, a, extra, at_b, at_a))
    assert forward.keys() == backward.keys()
    for node, f in forward.items():
        r = backward[node]
        assert r["status"] == _INVERSE[f["status"]], node
        assert (r["count_a"], r["count_b"], r["delta"]) == (f["count_b"], f["count_a"], -f["delta"])
    assert has_regression(list(forward.values())) == any(
        e["status"] == "colder" for e in backward.values()
    )


@given(
    trace_a=trace_st,
    trace_b=trace_st,
    extra=extra_ids_st,
    at_a=_at,
    at_b=_at,
)
def test_every_entry_matches_a_naive_recount_in_severity_order(
    files: _Files,
    trace_a: list[tuple[str, int | None]],
    trace_b: list[tuple[str, int | None]],
    extra: list[str] | None,
    at_a: int | None,
    at_b: int | None,
) -> None:
    a, b = files.build(trace_a), files.build(trace_b)
    entries = diff_trace_vs_trace(a, b, extra, at_a, at_b)
    heat_a = _naive(trace_a, len(trace_a) if at_a is None else at_a)
    heat_b = _naive(trace_b, len(trace_b) if at_b is None else at_b)
    universe = {node for node, _ in trace_a} | {node for node, _ in trace_b} | set(extra or ())
    by_node = _by_node(entries)
    assert set(by_node) == universe
    for node, e in by_node.items():
        ca, cb = heat_a.get(node, 0), heat_b.get(node, 0)
        assert (e["count_a"], e["count_b"], e["delta"]) == (ca, cb, cb - ca), node
        assert e["status"] == _status(ca, cb), node
    keys = [(_ORDER.index(e["status"]), e["node_id"]) for e in entries]
    assert keys == sorted(keys)
    assert has_regression(entries) == any(0 < e["count_a"] < e["count_b"] for e in entries)


@given(trace=trace_st, more=trace_st)
def test_appending_events_never_makes_a_node_gone_or_colder(
    files: _Files, trace: list[tuple[str, int | None]], more: list[tuple[str, int | None]]
) -> None:
    a, b = files.build(trace), files.build(trace + more)
    statuses = {e["status"] for e in diff_trace_vs_trace(a, b)}
    assert statuses <= {"same", "hotter", "new"}


@given(trace=trace_st.filter(bool))
def test_running_a_trace_twice_makes_every_node_hotter(
    files: _Files, trace: list[tuple[str, int | None]]
) -> None:
    a, b = files.build(trace), files.build(trace + trace)
    entries = diff_trace_vs_trace(a, b)
    assert {e["status"] for e in entries} == {"hotter"}
    assert all(e["count_b"] == 2 * e["count_a"] for e in entries)
    assert has_regression(entries)


@given(
    trace=trace_st,
    ids=st.lists(st.sampled_from((*_NODES, "static/only.py:x")), max_size=8),
    at=_at,
)
def test_static_diff_touched_is_positive_count_and_agrees_with_trace_diff(
    files: _Files, trace: list[tuple[str, int | None]], ids: list[str], at: int | None
) -> None:
    agg = files.build(trace)
    entries = diff_trace_vs_static(ids, agg, at)
    heat = _naive(trace, len(trace) if at is None else at)
    assert sorted(e["node_id"] for e in entries) == sorted(ids)
    for e in entries:
        count = heat.get(e["node_id"], 0)
        assert e["count_a"] == count
        assert (e["count_b"], e["delta"]) == (0, 0)
        assert e["status"] == ("touched" if count > 0 else "cold")
    keys = [(e["status"] != "cold", e["node_id"]) for e in entries]
    assert keys == sorted(keys)
    # Against an empty baseline, trace-vs-trace says "new" exactly where
    # trace-vs-static says "touched".
    vs_empty = _by_node(diff_trace_vs_trace(files.build([]), agg, ids, None, at))
    for e in entries:
        assert (vs_empty[e["node_id"]]["status"] == "new") == (e["status"] == "touched")
