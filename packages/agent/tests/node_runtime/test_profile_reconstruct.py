"""Tests for the pure V8 CPU-profile → call/return reconstruction (ADR-0022).

Synthetic profiles mimic the V8 `.cpuprofile` shape (`nodes`/`samples`/`timeDeltas`).
The injected `resolve` maps frames with a ``proj:`` URL to a node id (the
``functionName``) and filters everything else — modelling pseudo-frames and
non-project code dropping out.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from grackle.node_runtime.profile_reconstruct import reconstruct

if TYPE_CHECKING:
    from collections.abc import Mapping

    from grackle.adapters.base import TraceEvent


def _frame(name: str, url: str = "") -> dict[str, Any]:
    return {"functionName": name, "url": url, "lineNumber": 0}


def _node(node_id: int, name: str, children: list[int], *, project: bool = True) -> dict[str, Any]:
    url = f"proj:{name}" if project else ""
    return {"id": node_id, "callFrame": _frame(name, url), "children": children}


def _resolve(call_frame: Mapping[str, Any]) -> str | None:
    """Project frames (proj: URL) resolve to their functionName; others filter out."""
    url = call_frame.get("url", "")
    return call_frame["functionName"] if url.startswith("proj:") else None


def _kinds(events: list[TraceEvent]) -> list[tuple[str, str, int]]:
    return [(e["event"], e["node_id"], e["frame_depth"]) for e in events]


def test_empty_samples_yields_nothing() -> None:
    profile = {"nodes": [], "samples": [], "timeDeltas": [], "startTime": 0, "endTime": 0}
    assert reconstruct(profile, _resolve) == []


def test_single_stack_opens_then_closes_at_end() -> None:
    # root(1) → A(2) → B(3); both samples land on B.
    profile = {
        "nodes": [
            _node(1, "(root)", [2], project=False),
            _node(2, "A", [3]),
            _node(3, "B", []),
        ],
        "samples": [3, 3],
        "timeDeltas": [10, 5],
        "startTime": 100,
        "endTime": 120,
    }
    events = reconstruct(profile, _resolve)
    assert _kinds(events) == [
        ("call", "A", 0),
        ("call", "B", 1),
        ("return", "B", 1),
        ("return", "A", 0),
    ]
    # Opens are stamped at the START of the first sample's interval (prev_cum),
    # which for sample 0 is startTime itself → 100 µs → 100_000 ns.
    assert events[0]["ts_ns"] == 100_000
    # Closing happens at max(endTime, last cum) = max(120, 115) = 120 → 120_000.
    assert events[-1]["ts_ns"] == 120_000
    assert all(e["thread_id"] == 0 for e in events)


def test_single_sample_leaf_gets_nonzero_duration() -> None:
    # root→A→B with samples [A, B]: B is the deepest leaf for exactly the second
    # interval and must be credited it (open at the interval start), not collapse
    # to zero with its time mis-folded into A.
    profile = {
        "nodes": [
            _node(1, "(root)", [2], project=False),
            _node(2, "A", [3]),
            _node(3, "B", []),
        ],
        "samples": [2, 3],
        "timeDeltas": [10, 10],
        "startTime": 0,
        "endTime": 20,
    }
    events = reconstruct(profile, _resolve)
    ts = {(e["event"], e["node_id"]): e["ts_ns"] for e in events}
    assert ts[("call", "A")] == 0  # A opens at profile start
    assert ts[("call", "B")] == 10_000  # B opens at the start of its interval
    assert ts[("return", "B")] == 20_000
    assert ts[("return", "A")] == 20_000
    # → A total 20µs (self 10µs), B total 10µs — B is not zero-duration.


def test_call_and_matching_return_share_depth() -> None:
    profile = {
        "nodes": [_node(1, "(root)", [2], project=False), _node(2, "A", [])],
        "samples": [2],
        "timeDeltas": [1],
        "startTime": 0,
        "endTime": 1,
    }
    events = reconstruct(profile, _resolve)
    call = next(e for e in events if e["event"] == "call")
    ret = next(e for e in events if e["event"] == "return")
    assert call["frame_depth"] == ret["frame_depth"] == 0


def test_pop_then_push_diffs_stacks() -> None:
    # root → A → B  and  root → A → C are distinct tree nodes sharing prefix [root, A].
    profile = {
        "nodes": [
            _node(1, "(root)", [2], project=False),
            _node(2, "A", [3, 4]),
            _node(3, "B", []),
            _node(4, "C", []),
        ],
        "samples": [3, 4],
        "timeDeltas": [1, 1],
        "startTime": 0,
        "endTime": 2,
    }
    events = reconstruct(profile, _resolve)
    assert _kinds(events) == [
        ("call", "A", 0),
        ("call", "B", 1),
        ("return", "B", 1),  # B closes, A stays open (common prefix)
        ("call", "C", 1),
        ("return", "C", 1),  # end-of-profile close
        ("return", "A", 0),
    ]


def test_recursion_is_distinct_frames() -> None:
    # root → A(2) → A(3) → A(4): recursion as separate tree nodes, depth grows.
    profile = {
        "nodes": [
            _node(1, "(root)", [2], project=False),
            _node(2, "A", [3]),
            _node(3, "A", [4]),
            _node(4, "A", []),
        ],
        "samples": [4],
        "timeDeltas": [1],
        "startTime": 0,
        "endTime": 1,
    }
    events = reconstruct(profile, _resolve)
    calls = [e for e in events if e["event"] == "call"]
    assert [c["frame_depth"] for c in calls] == [0, 1, 2]
    assert all(c["node_id"] == "A" for c in calls)


def test_non_project_leaf_collapses_no_spurious_events() -> None:
    # root → A → internal(leaf). Two samples differ only in a non-project leaf →
    # filtered stacks are both [A] → no open/close churn.
    profile = {
        "nodes": [
            _node(1, "(root)", [2], project=False),
            _node(2, "A", [3, 4]),
            _node(3, "internalX", [], project=False),
            _node(4, "internalY", [], project=False),
        ],
        "samples": [3, 4],
        "timeDeltas": [1, 1],
        "startTime": 0,
        "endTime": 2,
    }
    events = reconstruct(profile, _resolve)
    # Only A opens (once) and closes (once); the internal leaves never surface.
    assert _kinds(events) == [("call", "A", 0), ("return", "A", 0)]


def test_idle_sample_closes_and_reopens() -> None:
    # A on-CPU, then (idle) (filtered → empty stack), then A again.
    profile = {
        "nodes": [
            _node(1, "(root)", [2, 3], project=False),
            _node(2, "A", []),
            _node(3, "(idle)", [], project=False),
        ],
        "samples": [2, 3, 2],
        "timeDeltas": [1, 1, 1],
        "startTime": 0,
        "endTime": 3,
    }
    events = reconstruct(profile, _resolve)
    assert _kinds(events) == [
        ("call", "A", 0),
        ("return", "A", 0),  # idle → close
        ("call", "A", 0),  # back on-CPU → reopen
        ("return", "A", 0),  # end
    ]


def test_unknown_sample_id_is_skipped() -> None:
    profile = {
        "nodes": [_node(1, "(root)", [2], project=False), _node(2, "A", [])],
        "samples": [2, 999, 2],  # 999 not in nodes → skipped
        "timeDeltas": [1, 1, 1],
        "startTime": 0,
        "endTime": 3,
    }
    events = reconstruct(profile, _resolve)
    # A stays open across the skipped sample (no spurious close).
    assert _kinds(events) == [("call", "A", 0), ("return", "A", 0)]


def test_malformed_nodes_are_tolerated() -> None:
    # A node missing callFrame and one missing id must not crash reconstruction
    # (mirrors the module's other defensive guards / ADR-0022 never-crash intent).
    profile = {
        "nodes": [
            _node(1, "(root)", [2], project=False),
            {"id": 2, "children": []},  # no callFrame
            {"children": []},  # no id
        ],
        "samples": [2],
        "timeDeltas": [1],
        "startTime": 0,
        "endTime": 1,
    }
    events = reconstruct(profile, _resolve)
    assert events == []  # node 2 has no callFrame → dropped → no project frames


def test_cumulative_timestamps() -> None:
    profile = {
        "nodes": [_node(1, "(root)", [2], project=False), _node(2, "A", [])],
        "samples": [2, 2, 2],
        "timeDeltas": [5, 10, 20],
        "startTime": 1000,
        "endTime": 2000,
    }
    events = reconstruct(profile, _resolve)
    # Only the opening call (first sample) and the final close are emitted.
    call = events[0]
    assert call["event"] == "call"
    # Stamped at the start of sample 0's interval = startTime = 1000 µs.
    assert call["ts_ns"] == 1000 * 1000
