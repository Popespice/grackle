"""Tests for python_runtime.tracer — Tracer (sys.monitoring)."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

from grackle.adapters import registry
from grackle.adapters.base import ParseOptions, TraceCapExceeded, TraceOptions
from grackle.python_runtime.node_resolution import NodeResolver
from grackle.python_runtime.tracer import _GRACKLE_TOOL_ID, Tracer

_FIXTURE_ROOT = Path(__file__).parents[4] / "fixtures" / "tiny-python-app"
_SCRIPT = _FIXTURE_ROOT / "main.py"


def _make_tracer(options: TraceOptions | None = None) -> Tracer:
    graph = registry.get_static("python").parse(_FIXTURE_ROOT, ParseOptions())  # type: ignore[union-attr]
    resolver = NodeResolver(_FIXTURE_ROOT, graph)
    return Tracer(resolver, options or TraceOptions())


# ---------------------------------------------------------------------------
# Basic event collection
# ---------------------------------------------------------------------------


def test_run_produces_events() -> None:
    tracer = _make_tracer()
    events = tracer.run(_SCRIPT)
    assert len(events) > 0


def test_run_includes_call_events() -> None:
    tracer = _make_tracer()
    events = tracer.run(_SCRIPT)
    calls = [e for e in events if e["event"] == "call"]
    assert len(calls) > 0


def test_run_includes_return_events() -> None:
    tracer = _make_tracer()
    events = tracer.run(_SCRIPT)
    returns = [e for e in events if e["event"] == "return"]
    assert len(returns) > 0


def test_run_includes_exception_events() -> None:
    """classify(-1) triggers ValueError in is_even — expect exception events."""
    tracer = _make_tracer()
    events = tracer.run(_SCRIPT)
    exceptions = [e for e in events if e["event"] == "exception"]
    assert len(exceptions) >= 1
    exc_types = {e["metadata"].get("exc_type") for e in exceptions}
    assert "ValueError" in exc_types


# ---------------------------------------------------------------------------
# Node IDs are resolved
# ---------------------------------------------------------------------------


def test_node_ids_reference_known_functions() -> None:
    tracer = _make_tracer()
    events = tracer.run(_SCRIPT)
    call_node_ids = {e["node_id"] for e in events if e["event"] == "call"}
    # At minimum, we expect the four named functions to appear.
    assert "main.py:main" in call_node_ids
    assert "main.py:classify" in call_node_ids
    assert "main.py:is_even" in call_node_ids
    assert "main.py:is_odd" in call_node_ids


def test_no_stdlib_node_ids() -> None:
    """Non-project files must not appear in events (filtered by is_project_file)."""
    tracer = _make_tracer()
    events = tracer.run(_SCRIPT)
    for event in events:
        assert not event["node_id"].startswith("/"), (
            f"absolute path leaked into node_id: {event['node_id']!r}"
        )
        assert "site-packages" not in event["node_id"]


# ---------------------------------------------------------------------------
# Required fields present on every event
# ---------------------------------------------------------------------------


def test_events_have_required_fields() -> None:
    tracer = _make_tracer()
    events = tracer.run(_SCRIPT)
    for e in events:
        assert "event" in e
        assert "node_id" in e
        assert "ts_ns" in e
        assert "thread_id" in e
        assert "frame_depth" in e
        assert "metadata" in e


def test_ts_ns_monotonically_non_decreasing() -> None:
    tracer = _make_tracer()
    events = tracer.run(_SCRIPT)
    ts_values = [e["ts_ns"] for e in events]
    assert all(a <= b for a, b in zip(ts_values, ts_values[1:], strict=False))


def test_frame_depth_non_negative() -> None:
    tracer = _make_tracer()
    events = tracer.run(_SCRIPT)
    for e in events:
        assert e["frame_depth"] >= 0


# ---------------------------------------------------------------------------
# Line events (opt-in)
# ---------------------------------------------------------------------------


def test_no_line_events_by_default() -> None:
    tracer = _make_tracer(TraceOptions(include_line_events=False))
    events = tracer.run(_SCRIPT)
    line_events = [e for e in events if e["event"] == "line"]
    assert len(line_events) == 0


def test_line_events_when_enabled() -> None:
    tracer = _make_tracer(TraceOptions(include_line_events=True))
    events = tracer.run(_SCRIPT)
    line_events = [e for e in events if e["event"] == "line"]
    assert len(line_events) > 0
    # Line events carry a 'line' key in metadata
    for le in line_events:
        assert "line" in le["metadata"]


# ---------------------------------------------------------------------------
# Event cap
# ---------------------------------------------------------------------------


def test_max_events_raises_trace_cap_exceeded() -> None:
    tracer = _make_tracer(TraceOptions(max_events=5))
    with pytest.raises(TraceCapExceeded):
        tracer.run(_SCRIPT)


def test_max_events_none_means_unlimited() -> None:
    tracer = _make_tracer(TraceOptions(max_events=None))
    events = tracer.run(_SCRIPT)
    assert len(events) > 5  # well above any tiny cap


# ---------------------------------------------------------------------------
# Teardown — _stop() must fully detach so callbacks do not leak between runs
# ---------------------------------------------------------------------------


def test_stop_fully_releases_tool_id() -> None:
    """After run(), the tool ID must be reusable (i.e. fully freed)."""
    tracer = _make_tracer()
    tracer.run(_SCRIPT)
    # Re-claiming the tool ID should succeed; if _stop() leaked, this raises
    # ValueError("tool id N is already in use").
    sys.monitoring.use_tool_id(_GRACKLE_TOOL_ID, "test-reclaim")
    sys.monitoring.free_tool_id(_GRACKLE_TOOL_ID)


def test_stop_clears_event_subscriptions() -> None:
    """After run(), no events should remain subscribed for our tool ID."""
    tracer = _make_tracer()
    tracer.run(_SCRIPT)
    # set_events with 0 is idempotent; assert the current subscription is empty.
    assert sys.monitoring.get_events(_GRACKLE_TOOL_ID) == 0


def test_consecutive_runs_do_not_leak_callbacks() -> None:
    """Two consecutive run() calls must each produce a full event list — no
    leftover callbacks from the first run that would pollute the second."""
    tracer1 = _make_tracer()
    events1 = tracer1.run(_SCRIPT)
    tracer2 = _make_tracer()
    events2 = tracer2.run(_SCRIPT)
    # Both should observe equivalent event counts (script is deterministic).
    assert len(events1) == len(events2)
    assert len(events1) > 0
