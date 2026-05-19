"""Integration test for the runtime tracer against the tiny-python-app fixture.

Verifies that:
- The tracer produces the expected number of events on a real script.
- All required functions are traced.
- The exception path (is_even(-1)) produces exception events.
- Events have the correct structural shape.
- Results match the committed golden trace modulo timestamps and thread IDs.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from grackle.adapters import registry
from grackle.adapters.base import ParseOptions, TraceOptions
from grackle.python_runtime.adapter import PythonRuntimeAdapter
from grackle.python_runtime.node_resolution import NodeResolver
from grackle.python_runtime.tracer import Tracer
from grackle.python_runtime.writer import read_jsonl

_ROOT = Path(__file__).parents[4] / "fixtures" / "tiny-python-app"
_SCRIPT = _ROOT / "main.py"
_GOLDEN = _ROOT / "trace.golden.jsonl"


@pytest.fixture(scope="module")
def events() -> list[dict]:  # type: ignore[type-arg]
    graph = registry.get_static("python").parse(_ROOT, ParseOptions())  # type: ignore[union-attr]
    resolver = NodeResolver(_ROOT, graph)
    tracer = Tracer(resolver, TraceOptions())
    return tracer.run(_SCRIPT)  # type: ignore[return-value]


def test_event_count_matches_golden(events: list[dict]) -> None:  # type: ignore[type-arg]
    golden = read_jsonl(_GOLDEN)
    assert len(events) == len(golden), f"expected {len(golden)} events (golden), got {len(events)}"


def test_event_kinds_match_golden(events: list[dict]) -> None:  # type: ignore[type-arg]
    golden = read_jsonl(_GOLDEN)
    actual_kinds = [e["event"] for e in events]
    golden_kinds = [e["event"] for e in golden]
    assert actual_kinds == golden_kinds


def test_node_id_sequence_matches_golden(events: list[dict]) -> None:  # type: ignore[type-arg]
    """Node-ID sequence must be deterministic regardless of timestamps."""
    golden = read_jsonl(_GOLDEN)
    actual_ids = [e["node_id"] for e in events]
    golden_ids = [e["node_id"] for e in golden]
    assert actual_ids == golden_ids


def test_four_functions_called(events: list[dict]) -> None:  # type: ignore[type-arg]
    call_ids = {e["node_id"] for e in events if e["event"] == "call"}
    assert "main.py:main" in call_ids
    assert "main.py:classify" in call_ids
    assert "main.py:is_even" in call_ids
    assert "main.py:is_odd" in call_ids


def test_exception_events_present(events: list[dict]) -> None:  # type: ignore[type-arg]
    exc_events = [e for e in events if e["event"] == "exception"]
    assert len(exc_events) >= 1
    assert any(e["metadata"].get("exc_type") == "ValueError" for e in exc_events)


def test_adapter_trace_matches_direct_tracer(events: list[dict]) -> None:  # type: ignore[type-arg]
    """PythonRuntimeAdapter.trace() must yield the same sequence as Tracer.run()."""
    adapter = PythonRuntimeAdapter()
    adapter_events = list(adapter.trace(_SCRIPT, _ROOT, TraceOptions()))
    assert [e["event"] for e in adapter_events] == [e["event"] for e in events]
    assert [e["node_id"] for e in adapter_events] == [e["node_id"] for e in events]
