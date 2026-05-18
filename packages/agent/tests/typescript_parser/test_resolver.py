"""Tests for TypeScript cross-file symbol resolver."""

from __future__ import annotations

from grackle.typescript_parser.resolver import (
    FileScope,
    ProjectScope,
    SymbolResolver,
    _resolve_ts_module,
    build_project_scope,
    resolve_graph,
)

# ---------------------------------------------------------------------------
# _resolve_ts_module
# ---------------------------------------------------------------------------


def test_relative_ts_resolves() -> None:
    file_ids = frozenset({"src/models.ts"})
    result = _resolve_ts_module("src/index.ts", "./models", file_ids)
    assert result == "src/models.ts"


def test_relative_tsx_resolves() -> None:
    file_ids = frozenset({"src/App.tsx"})
    result = _resolve_ts_module("src/index.ts", "./App", file_ids)
    assert result == "src/App.tsx"


def test_parent_dir_relative_resolves() -> None:
    file_ids = frozenset({"utils.ts"})
    result = _resolve_ts_module("src/index.ts", "../utils", file_ids)
    assert result == "utils.ts"


def test_external_specifier_returns_none() -> None:
    file_ids = frozenset({"src/react.ts"})
    assert _resolve_ts_module("src/index.ts", "react", file_ids) is None


def test_unresolvable_returns_none() -> None:
    file_ids: frozenset[str] = frozenset()
    assert _resolve_ts_module("src/index.ts", "./missing", file_ids) is None


def test_index_file_resolution() -> None:
    file_ids = frozenset({"src/utils/index.ts"})
    result = _resolve_ts_module("src/main.ts", "./utils", file_ids)
    assert result == "src/utils/index.ts"


# ---------------------------------------------------------------------------
# build_project_scope
# ---------------------------------------------------------------------------


def test_project_scope_exports() -> None:
    nodes = [
        {"id": "src/models.ts", "kind": "file", "name": "models.ts", "path": "src/models.ts"},
        {"id": "src/models.ts:User", "kind": "class", "name": "User", "path": "src/models.ts"},
    ]
    scope = build_project_scope(nodes)  # type: ignore[arg-type]
    assert ("src/models.ts", "User") in scope.exports
    assert scope.exports[("src/models.ts", "User")] == "src/models.ts:User"


def test_project_scope_file_ids() -> None:
    nodes = [
        {"id": "a.ts", "kind": "file", "name": "a.ts", "path": "a.ts"},
        {"id": "b.ts", "kind": "file", "name": "b.ts", "path": "b.ts"},
    ]
    scope = build_project_scope(nodes)  # type: ignore[arg-type]
    assert "a.ts" in scope.all_file_ids
    assert "b.ts" in scope.all_file_ids


# ---------------------------------------------------------------------------
# SymbolResolver
# ---------------------------------------------------------------------------


def test_resolve_local_def() -> None:
    fs = FileScope(file_id="a.ts", local_defs={"Foo": "a.ts:Foo"})
    ps = ProjectScope()
    assert SymbolResolver(fs, ps).resolve("Foo") == "a.ts:Foo"


def test_resolve_imported_name() -> None:
    fs = FileScope(
        file_id="a.ts",
        import_map={"User": ("b.ts", "User")},
    )
    ps = ProjectScope(
        all_file_ids=frozenset({"b.ts"}),
        exports={("b.ts", "User"): "b.ts:User"},
    )
    assert SymbolResolver(fs, ps).resolve("User") == "b.ts:User"


def test_resolve_unknown_returns_none() -> None:
    fs = FileScope(file_id="a.ts")
    ps = ProjectScope()
    assert SymbolResolver(fs, ps).resolve("Unknown") is None


# ---------------------------------------------------------------------------
# resolve_graph
# ---------------------------------------------------------------------------


def _make_graph(nodes: list, edges: list) -> dict:  # type: ignore[type-arg]
    return {"version": 1, "language": "typescript", "nodes": nodes, "edges": edges}


def test_resolve_graph_upgrades_call_edge() -> None:
    nodes = [
        {"id": "a.ts", "kind": "file", "name": "a.ts", "path": "a.ts"},
        {"id": "b.ts", "kind": "file", "name": "b.ts", "path": "b.ts"},
        {"id": "a.ts:run", "kind": "function", "name": "run", "path": "a.ts"},
        {"id": "b.ts:helper", "kind": "function", "name": "helper", "path": "b.ts"},
    ]
    edges = [
        {
            "source": "a.ts",
            "target": "./b",
            "kind": "import",
            "metadata": {"names": ["helper"], "type_only": False},
        },
        {
            "source": "a.ts:run",
            "target": "helper",
            "kind": "call",
            "metadata": {"resolved": False},
        },
    ]
    graph = resolve_graph(_make_graph(nodes, edges))  # type: ignore[arg-type]
    call_edges = [e for e in graph["edges"] if e["kind"] == "call"]
    assert len(call_edges) == 1
    assert call_edges[0]["target"] == "b.ts:helper"


def test_resolve_graph_leaves_unresolvable_unchanged() -> None:
    nodes = [
        {"id": "a.ts", "kind": "file", "name": "a.ts", "path": "a.ts"},
        {"id": "a.ts:run", "kind": "function", "name": "run", "path": "a.ts"},
    ]
    edges = [
        {
            "source": "a.ts:run",
            "target": "externalFn",
            "kind": "call",
            "metadata": {"resolved": False},
        },
    ]
    graph = resolve_graph(_make_graph(nodes, edges))  # type: ignore[arg-type]
    call_edges = [e for e in graph["edges"] if e["kind"] == "call"]
    assert call_edges[0]["target"] == "externalFn"
    assert call_edges[0]["metadata"].get("resolved") is False


def test_resolve_graph_preserves_import_edges() -> None:
    nodes = [{"id": "a.ts", "kind": "file", "name": "a.ts", "path": "a.ts"}]
    edges = [
        {"source": "a.ts", "target": "./b", "kind": "import", "metadata": {"type_only": False}}
    ]
    graph = resolve_graph(_make_graph(nodes, edges))  # type: ignore[arg-type]
    assert len([e for e in graph["edges"] if e["kind"] == "import"]) == 1
