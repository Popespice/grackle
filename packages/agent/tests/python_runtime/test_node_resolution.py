"""Tests for python_runtime.node_resolution — NodeResolver."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from grackle.python_runtime.node_resolution import NodeResolver

if TYPE_CHECKING:
    from grackle.adapters.base import GraphNode, StaticGraph

_ROOT = Path("/fake/project")


def _make_graph(nodes: list[GraphNode]) -> StaticGraph:
    return {"version": 1, "language": "python", "nodes": nodes, "edges": []}


def _file_node(path: str) -> GraphNode:
    return {"id": path, "kind": "file", "name": path, "path": path}


def _fn_node(path: str, name: str, line: int) -> GraphNode:
    return {"id": f"{path}:{name}", "kind": "function", "name": name, "path": path, "line": line}


def _method_node(path: str, name: str, line: int) -> GraphNode:
    return {"id": f"{path}:{name}", "kind": "method", "name": name, "path": path, "line": line}


# ---------------------------------------------------------------------------
# Exact function-node resolution
# ---------------------------------------------------------------------------


def test_resolves_function_by_exact_line() -> None:
    graph = _make_graph([_file_node("src/app.py"), _fn_node("src/app.py", "run", 10)])
    resolver = NodeResolver(_ROOT, graph)
    filename = str(_ROOT / "src" / "app.py")
    assert resolver.resolve(filename, 10) == "src/app.py:run"


def test_resolves_method_node() -> None:
    graph = _make_graph([_file_node("src/app.py"), _method_node("src/app.py", "handle", 25)])
    resolver = NodeResolver(_ROOT, graph)
    filename = str(_ROOT / "src" / "app.py")
    assert resolver.resolve(filename, 25) == "src/app.py:handle"


def test_function_takes_priority_over_file() -> None:
    graph = _make_graph([_file_node("app.py"), _fn_node("app.py", "main", 5)])
    resolver = NodeResolver(_ROOT, graph)
    filename = str(_ROOT / "app.py")
    assert resolver.resolve(filename, 5) == "app.py:main"


# ---------------------------------------------------------------------------
# File-level fallback
# ---------------------------------------------------------------------------


def test_falls_back_to_file_node_for_unknown_line() -> None:
    graph = _make_graph([_file_node("app.py"), _fn_node("app.py", "main", 5)])
    resolver = NodeResolver(_ROOT, graph)
    filename = str(_ROOT / "app.py")
    # Line 1 is the module level — no function node → file fallback
    assert resolver.resolve(filename, 1) == "app.py"


def test_falls_back_to_file_when_no_functions() -> None:
    graph = _make_graph([_file_node("utils.py")])
    resolver = NodeResolver(_ROOT, graph)
    filename = str(_ROOT / "utils.py")
    assert resolver.resolve(filename, 99) == "utils.py"


# ---------------------------------------------------------------------------
# Unresolved cases
# ---------------------------------------------------------------------------


def test_returns_unresolved_for_outside_root() -> None:
    graph = _make_graph([_file_node("app.py")])
    resolver = NodeResolver(_ROOT, graph)
    assert resolver.resolve("/other/place/script.py", 10) == "<unresolved>"


def test_returns_unresolved_for_stdlib_sentinel() -> None:
    graph = _make_graph([_file_node("app.py")])
    resolver = NodeResolver(_ROOT, graph)
    assert resolver.resolve("<frozen importlib._bootstrap>", 100) == "<unresolved>"


def test_returns_unresolved_for_empty_filename() -> None:
    graph = _make_graph([_file_node("app.py")])
    resolver = NodeResolver(_ROOT, graph)
    assert resolver.resolve("", 1) == "<unresolved>"


# ---------------------------------------------------------------------------
# is_project_file
# ---------------------------------------------------------------------------


def test_is_project_file_true_for_root_file() -> None:
    graph = _make_graph([_file_node("app.py")])
    resolver = NodeResolver(_ROOT, graph)
    assert resolver.is_project_file(str(_ROOT / "app.py")) is True


def test_is_project_file_false_for_stdlib() -> None:
    graph = _make_graph([])
    resolver = NodeResolver(_ROOT, graph)
    assert resolver.is_project_file("/usr/lib/python3.12/pathlib.py") is False


def test_is_project_file_false_for_sentinel() -> None:
    graph = _make_graph([])
    resolver = NodeResolver(_ROOT, graph)
    assert resolver.is_project_file("<frozen importlib._bootstrap>") is False


# ---------------------------------------------------------------------------
# Multiple functions in the same file
# ---------------------------------------------------------------------------


def test_resolves_correct_function_among_several() -> None:
    graph = _make_graph(
        [
            _file_node("main.py"),
            _fn_node("main.py", "foo", 10),
            _fn_node("main.py", "bar", 20),
            _fn_node("main.py", "baz", 30),
        ]
    )
    resolver = NodeResolver(_ROOT, graph)
    filename = str(_ROOT / "main.py")
    assert resolver.resolve(filename, 10) == "main.py:foo"
    assert resolver.resolve(filename, 20) == "main.py:bar"
    assert resolver.resolve(filename, 30) == "main.py:baz"
    # Line 15 has no exact match — falls back to file
    assert resolver.resolve(filename, 15) == "main.py"
