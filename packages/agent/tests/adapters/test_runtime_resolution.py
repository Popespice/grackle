"""Tests for the shared runtime-resolver base + trace-event/cap helpers (Phase 8.6)."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from grackle.adapters.base import (
    TraceCapExceeded,
    enforce_event_cap,
    new_trace_event,
)
from grackle.adapters.runtime_resolution import NOT_PROJECT, UNRESOLVED, RuntimeResolver
from grackle.python_runtime.node_resolution import NodeResolver as PyNodeResolver

if TYPE_CHECKING:
    from pathlib import Path

    from grackle.adapters.base import StaticGraph


def _graph(nodes: list[dict[str, object]]) -> StaticGraph:
    return {"version": 1, "language": "python", "nodes": nodes, "edges": []}  # type: ignore[typeddict-item]


# ---------------------------------------------------------------------------
# RuntimeResolver._index_unique — first-writer-wins, ambiguity → None
# ---------------------------------------------------------------------------


def test_index_unique_first_writer_wins_then_marks_ambiguous() -> None:
    index: dict[tuple[str, int], str | None] = {}
    RuntimeResolver._index_unique(index, ("a.py", 1), "n1")
    assert index[("a.py", 1)] == "n1"
    RuntimeResolver._index_unique(index, ("a.py", 1), "n1")  # same id → unchanged
    assert index[("a.py", 1)] == "n1"
    RuntimeResolver._index_unique(index, ("a.py", 1), "n2")  # distinct id → ambiguous
    assert index[("a.py", 1)] is None


def test_python_resolver_builds_name_index_but_resolves_by_line(tmp_path: Path) -> None:
    """The shared base builds a (path, name) index for Python too — harmless, since
    Python's resolve() keys on line only (a wrong line falls to the file node, NOT
    the name match)."""
    nodes: list[dict[str, object]] = [
        {"id": "a.py", "kind": "file", "name": "a.py", "path": "a.py"},
        {"id": "a.py:f", "kind": "function", "name": "f", "path": "a.py", "line": 3},
    ]
    resolver = PyNodeResolver(tmp_path, _graph(nodes))
    # The name index IS built (the dedup base populates it for both languages)...
    assert resolver._name_index[("a.py", "f")] == "a.py:f"
    co_filename = str(tmp_path / "a.py")
    # ...but Python resolution is line-based: right line → the function node.
    assert resolver.resolve(co_filename, 3, "f") == "a.py:f"
    # A wrong line falls to the FILE node, proving the name index is unused here.
    assert resolver.resolve(co_filename, 99, "f") == "a.py"
    # Module frames go straight to the file node.
    assert resolver.resolve(co_filename, 1, "<module>") == "a.py"
    assert resolver.is_project_file(co_filename) is True


def test_resolver_constants() -> None:
    assert NOT_PROJECT == ""
    assert UNRESOLVED == "<unresolved>"


# ---------------------------------------------------------------------------
# new_trace_event factory
# ---------------------------------------------------------------------------


def test_new_trace_event_defaults_metadata_to_fresh_dict() -> None:
    a = new_trace_event("call", "n", 1, 0, 2)
    assert a == {
        "event": "call",
        "node_id": "n",
        "ts_ns": 1,
        "thread_id": 0,
        "frame_depth": 2,
        "metadata": {},
    }
    # Each call gets its OWN metadata dict — not a shared mutable default.
    b = new_trace_event("call", "n", 1, 0, 2)
    a["metadata"]["x"] = 1
    assert b["metadata"] == {}


def test_new_trace_event_passes_metadata_through() -> None:
    ev = new_trace_event("exception", "n", 5, 7, 3, {"exc_type": "ValueError"})
    assert ev["metadata"] == {"exc_type": "ValueError"}
    assert ev["thread_id"] == 7
    assert ev["frame_depth"] == 3


# ---------------------------------------------------------------------------
# enforce_event_cap
# ---------------------------------------------------------------------------


def test_enforce_event_cap_below_and_no_cap_do_not_raise() -> None:
    enforce_event_cap(4, 5)  # below cap
    enforce_event_cap(99, None)  # no cap at all


def test_enforce_event_cap_at_limit_raises() -> None:
    with pytest.raises(TraceCapExceeded, match="trace event cap of 5 reached"):
        enforce_event_cap(5, 5)


def test_enforce_event_cap_appends_hint() -> None:
    with pytest.raises(TraceCapExceeded, match="set --max-events higher"):
        enforce_event_cap(5, 5, hint="set --max-events higher or omit it to disable")
