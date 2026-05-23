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


# ---------------------------------------------------------------------------
# C1 regression — BaseException must not bypass event collection
# ---------------------------------------------------------------------------


def test_systemexit_does_not_bypass_event_collection(tmp_path: Path) -> None:
    """A script that calls ``sys.exit()`` must still produce trace events.

    Before the C1 fix, ``run()`` caught ``Exception`` only — ``SystemExit``
    inherits from ``BaseException`` and so propagated past ``run()``,
    bypassing the ``return self._events`` line entirely. The fix widens the
    catch to ``BaseException``.
    """
    script = tmp_path / "exits.py"
    script.write_text(
        "import sys\ndef helper() -> None:\n    sys.exit(0)\nhelper()\n",
        encoding="utf-8",
    )
    from grackle.adapters import registry  # local import — keep top of file tidy
    from grackle.adapters.base import ParseOptions

    graph = registry.get_static("python").parse(tmp_path, ParseOptions())  # type: ignore[union-attr]
    resolver = NodeResolver(tmp_path, graph)
    tracer = Tracer(resolver, TraceOptions())
    events = tracer.run(script)
    # We must reach this assertion — the SystemExit must NOT propagate past run().
    assert len(events) > 0
    # And the helper that called sys.exit() must appear among the call events
    call_ids = {e["node_id"] for e in events if e["event"] == "call"}
    assert "exits.py:helper" in call_ids


def test_keyboard_interrupt_does_not_bypass(tmp_path: Path) -> None:
    """A script that raises KeyboardInterrupt must also produce events (C1)."""
    script = tmp_path / "ki.py"
    script.write_text(
        "def boom() -> None:\n    raise KeyboardInterrupt('test')\nboom()\n",
        encoding="utf-8",
    )
    from grackle.adapters import registry
    from grackle.adapters.base import ParseOptions

    graph = registry.get_static("python").parse(tmp_path, ParseOptions())  # type: ignore[union-attr]
    resolver = NodeResolver(tmp_path, graph)
    tracer = Tracer(resolver, TraceOptions())
    events = tracer.run(script)
    assert len(events) > 0


# ---------------------------------------------------------------------------
# C2 regression — decorated functions resolve to their function node
# ---------------------------------------------------------------------------


def test_decorated_function_resolves_to_function_node(tmp_path: Path) -> None:
    """Before C2, decorated functions had ``line = def line`` but the runtime
    code object's ``co_firstlineno`` is the first decorator's line, so every
    decorated function fell back to the file node. The fix records the
    decorator line in the static graph so the runtime exact-match succeeds.
    """
    script = tmp_path / "deco.py"
    script.write_text(
        "import functools\n"
        "\n"
        "@functools.lru_cache(maxsize=None)\n"  # decorator on line 3
        "def cached(x: int) -> int:\n"  # def on line 4
        "    return x * 2\n"
        "\n"
        "cached(7)\n"
        "cached(7)\n",
        encoding="utf-8",
    )
    from grackle.adapters import registry
    from grackle.adapters.base import ParseOptions

    graph = registry.get_static("python").parse(tmp_path, ParseOptions())  # type: ignore[union-attr]
    resolver = NodeResolver(tmp_path, graph)
    tracer = Tracer(resolver, TraceOptions())
    events = tracer.run(script)

    call_ids = {e["node_id"] for e in events if e["event"] == "call"}
    # The decorated function must resolve to its function node, NOT the file node.
    assert "deco.py:cached" in call_ids, (
        f"decorated function did not resolve to function node; call_ids={call_ids}"
    )


# ---------------------------------------------------------------------------
# C3 regression — PY_UNWIND must keep frame_depth consistent
# ---------------------------------------------------------------------------


def test_frame_depth_recovers_after_exception(tmp_path: Path) -> None:
    """After an exception propagates through a frame, subsequent events on
    the same thread must report a frame_depth equal to the depth they would
    have had if the frame had returned normally.

    Concretely: caller() calls thrower() which raises; caller catches; then
    caller calls peer(). peer's call event must be at the same depth as
    caller's body — depth 1 above main, not depth 2 (which would imply
    thrower's stack frame leaked).
    """
    script = tmp_path / "unwind.py"
    script.write_text(
        "def peer() -> None:\n"
        "    return None\n"
        "\n"
        "def thrower() -> None:\n"
        "    raise ValueError('boom')\n"
        "\n"
        "def caller() -> None:\n"
        "    try:\n"
        "        thrower()\n"
        "    except ValueError:\n"
        "        pass\n"
        "    peer()\n"
        "\n"
        "caller()\n",
        encoding="utf-8",
    )
    from grackle.adapters import registry
    from grackle.adapters.base import ParseOptions

    graph = registry.get_static("python").parse(tmp_path, ParseOptions())  # type: ignore[union-attr]
    resolver = NodeResolver(tmp_path, graph)
    tracer = Tracer(resolver, TraceOptions())
    events = tracer.run(script)

    # Find the call event for ``peer``; its depth must equal the depth of
    # the call event for ``thrower`` (both are direct children of caller).
    peer_call = next(e for e in events if e["event"] == "call" and e["node_id"] == "unwind.py:peer")
    thrower_call = next(
        e for e in events if e["event"] == "call" and e["node_id"] == "unwind.py:thrower"
    )
    assert peer_call["frame_depth"] == thrower_call["frame_depth"], (
        f"peer (after exception unwind) at depth {peer_call['frame_depth']}; "
        f"thrower (before unwind) at depth {thrower_call['frame_depth']} — "
        f"PY_UNWIND must decrement the per-thread depth counter"
    )


# ---------------------------------------------------------------------------
# C4 — generator frames don't crash the tracer (boundary check)
# ---------------------------------------------------------------------------


def test_generator_does_not_crash_tracer(tmp_path: Path) -> None:
    """We do NOT subscribe to PY_YIELD/PY_RESUME by design, but using a
    generator must still produce a coherent event stream and not crash.

    frame_depth values for code observed inside a generator may drift by one
    until the generator returns (documented in ADR-0013).
    """
    script = tmp_path / "gen.py"
    script.write_text(
        "def squares(n: int):\n"
        "    for i in range(n):\n"
        "        yield i * i\n"
        "\n"
        "def consume() -> int:\n"
        "    total = 0\n"
        "    for v in squares(5):\n"
        "        total += v\n"
        "    return total\n"
        "\n"
        "consume()\n",
        encoding="utf-8",
    )
    from grackle.adapters import registry
    from grackle.adapters.base import ParseOptions

    graph = registry.get_static("python").parse(tmp_path, ParseOptions())  # type: ignore[union-attr]
    resolver = NodeResolver(tmp_path, graph)
    tracer = Tracer(resolver, TraceOptions())
    events = tracer.run(script)
    # Must complete and produce events for both functions.
    call_ids = {e["node_id"] for e in events if e["event"] == "call"}
    assert "gen.py:squares" in call_ids
    assert "gen.py:consume" in call_ids
    # Depths must be non-negative throughout (the C3 fix also covers this).
    for e in events:
        assert e["frame_depth"] >= 0
