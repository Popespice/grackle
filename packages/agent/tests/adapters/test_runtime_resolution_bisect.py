"""Direct unit tests for RuntimeResolver._resolve_by_decl_line."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

import pytest

from grackle.adapters.runtime_resolution import RuntimeResolver

if TYPE_CHECKING:
    from pathlib import Path


class _DummyResolver(RuntimeResolver):
    """Minimal concrete subclass — _normalize returns None for everything."""

    def _normalize(self, identifier: str) -> str | None:
        return None


def _make_resolver(nodes: list[dict[str, Any]], root: Path) -> _DummyResolver:
    graph: dict[str, Any] = {
        "version": 1,
        "language": "test",
        "nodes": nodes,
        "edges": [],
    }
    return _DummyResolver(root, graph)  # type: ignore[arg-type]


@pytest.fixture()
def root(tmp_path: Path) -> Path:
    return tmp_path


# ---------------------------------------------------------------------------
# _resolve_by_decl_line
# ---------------------------------------------------------------------------


def test_empty_file(root: Path) -> None:
    r = _make_resolver([], root)
    assert r._resolve_by_decl_line("a.go", 5) is None


def test_single_func_exact_line(root: Path) -> None:
    nodes = [{"id": "a.go:foo", "kind": "function", "name": "foo", "path": "a.go", "line": 10}]
    r = _make_resolver(nodes, root)
    assert r._resolve_by_decl_line("a.go", 10) == "a.go:foo"


def test_single_func_inside_body(root: Path) -> None:
    nodes = [{"id": "a.go:foo", "kind": "function", "name": "foo", "path": "a.go", "line": 10}]
    r = _make_resolver(nodes, root)
    assert r._resolve_by_decl_line("a.go", 15) == "a.go:foo"


def test_line_before_all_decls(root: Path) -> None:
    nodes = [{"id": "a.go:foo", "kind": "function", "name": "foo", "path": "a.go", "line": 10}]
    r = _make_resolver(nodes, root)
    assert r._resolve_by_decl_line("a.go", 5) is None


def test_multiple_funcs_picks_nearest(root: Path) -> None:
    nodes = [
        {"id": "a.go:foo", "kind": "function", "name": "foo", "path": "a.go", "line": 5},
        {"id": "a.go:bar", "kind": "function", "name": "bar", "path": "a.go", "line": 15},
        {"id": "a.go:baz", "kind": "function", "name": "baz", "path": "a.go", "line": 25},
    ]
    r = _make_resolver(nodes, root)
    assert r._resolve_by_decl_line("a.go", 6) == "a.go:foo"
    assert r._resolve_by_decl_line("a.go", 15) == "a.go:bar"
    assert r._resolve_by_decl_line("a.go", 20) == "a.go:bar"
    assert r._resolve_by_decl_line("a.go", 25) == "a.go:baz"
    assert r._resolve_by_decl_line("a.go", 99) == "a.go:baz"


def test_unknown_file_returns_none(root: Path) -> None:
    nodes = [{"id": "a.go:foo", "kind": "function", "name": "foo", "path": "a.go", "line": 10}]
    r = _make_resolver(nodes, root)
    assert r._resolve_by_decl_line("b.go", 10) is None


def test_file_nodes_not_indexed(root: Path) -> None:
    nodes = [{"id": "a.go", "kind": "file", "name": "a.go", "path": "a.go"}]
    r = _make_resolver(nodes, root)
    assert r._resolve_by_decl_line("a.go", 5) is None
