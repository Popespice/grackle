"""Tests for the TypeScript NodeResolver (ADR-0022).

Fixture-driven and Node-free: each test builds a fake TypeScript static graph and
asserts how a V8 callFrame (url + 1-based line + functionName) resolves.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from grackle.node_runtime.node_resolution import UNRESOLVED, NodeResolver

if TYPE_CHECKING:
    from pathlib import Path

    from grackle.adapters.base import GraphNode, StaticGraph


def _graph(*nodes: GraphNode) -> StaticGraph:
    return {"version": 1, "language": "typescript", "nodes": list(nodes), "edges": []}


def _file(path: str) -> GraphNode:
    return {"id": path, "kind": "file", "name": path.rsplit("/", 1)[-1], "path": path}


def _fn(path: str, name: str, line: int) -> GraphNode:
    return {"id": f"{path}:{name}", "kind": "function", "name": name, "path": path, "line": line}


def _method(path: str, cls: str, name: str, line: int) -> GraphNode:
    return {
        "id": f"{path}:{cls}.{name}",
        "kind": "method",
        "name": name,
        "path": path,
        "line": line,
    }


def _url(root: Path, rel: str) -> str:
    """file:// URL for *rel* under *root*, the way V8 reports project sources."""
    return (root / rel).as_uri()


def test_resolves_function_by_exact_line(tmp_path: Path) -> None:
    graph = _graph(_file("src/app.ts"), _fn("src/app.ts", "handle", 12))
    resolver = NodeResolver(tmp_path, graph)
    # V8 lineNumber is 0-based (11) → caller passes line=12.
    assert resolver.resolve_frame(_url(tmp_path, "src/app.ts"), 12, "handle") == "src/app.ts:handle"


def test_resolves_method_by_exact_line(tmp_path: Path) -> None:
    graph = _graph(_file("src/m.ts"), _method("src/m.ts", "User", "save", 8))
    resolver = NodeResolver(tmp_path, graph)
    assert resolver.resolve_frame(_url(tmp_path, "src/m.ts"), 8, "save") == "src/m.ts:User.save"


def test_name_fallback_when_line_misses(tmp_path: Path) -> None:
    graph = _graph(_file("src/app.ts"), _fn("src/app.ts", "handle", 12))
    resolver = NodeResolver(tmp_path, graph)
    # Wrong line (decorated/multiline sig drift), but functionName matches uniquely.
    assert resolver.resolve_frame(_url(tmp_path, "src/app.ts"), 99, "handle") == "src/app.ts:handle"


def test_line_takes_precedence_over_name(tmp_path: Path) -> None:
    graph = _graph(
        _file("src/app.ts"),
        _fn("src/app.ts", "alpha", 3),
        _fn("src/app.ts", "beta", 7),
    )
    resolver = NodeResolver(tmp_path, graph)
    # Line 7 matches beta even though the (stale) functionName says alpha.
    assert resolver.resolve_frame(_url(tmp_path, "src/app.ts"), 7, "alpha") == "src/app.ts:beta"


def test_method_qualified_name_tail_fallback(tmp_path: Path) -> None:
    graph = _graph(_file("src/m.ts"), _method("src/m.ts", "User", "save", 8))
    resolver = NodeResolver(tmp_path, graph)
    # V8 sometimes reports "Class.method"; the tail ("save") still resolves.
    result = resolver.resolve_frame(_url(tmp_path, "src/m.ts"), None, "User.save")
    assert result == "src/m.ts:User.save"


def test_ambiguous_name_declines_to_file_node(tmp_path: Path) -> None:
    # A function and a method share the name "save" → distinct node ids → the
    # name fallback is ambiguous and declines rather than guessing.
    graph = _graph(
        _file("src/m.ts"),
        _fn("src/m.ts", "save", 3),
        _method("src/m.ts", "User", "save", 9),
    )
    resolver = NodeResolver(tmp_path, graph)
    # No line match, ambiguous name → falls back to the file node.
    assert resolver.resolve_frame(_url(tmp_path, "src/m.ts"), 50, "save") == "src/m.ts"


def test_module_frame_resolves_to_file_node(tmp_path: Path) -> None:
    graph = _graph(_file("src/app.ts"), _fn("src/app.ts", "handle", 12))
    resolver = NodeResolver(tmp_path, graph)
    # Top-level module evaluation: empty functionName, no useful line.
    assert resolver.resolve_frame(_url(tmp_path, "src/app.ts"), 1, "") == "src/app.ts"


def test_module_frame_at_line_1_resolves_to_file_node(tmp_path: Path) -> None:
    # A function on line 1 must NOT capture the top-level/module frame, which V8
    # reports as functionName "" at line 1 (mirrors the Python "<module>" guard).
    graph = _graph(_file("src/app.ts"), _fn("src/app.ts", "main", 1))
    resolver = NodeResolver(tmp_path, graph)
    assert resolver.resolve_frame(_url(tmp_path, "src/app.ts"), 1, "") == "src/app.ts"
    # A *named* frame on line 1 still resolves to the function node.
    assert resolver.resolve_frame(_url(tmp_path, "src/app.ts"), 1, "main") == "src/app.ts:main"


def test_module_frame_at_higher_line_resolves_to_file_node(tmp_path: Path) -> None:
    # Finding #3: a top-level/module frame (empty functionName) is not always at
    # line 1 — V8 tracks the first executing statement, which (after imports) can
    # coincide with a function declaration. It must resolve to the FILE node, never
    # to the function sharing that line.
    graph = _graph(_file("src/app.ts"), _fn("src/app.ts", "handle", 5))
    resolver = NodeResolver(tmp_path, graph)
    assert resolver.resolve_frame(_url(tmp_path, "src/app.ts"), 5, "") == "src/app.ts"


def test_same_line_declarations_decline_to_file_node(tmp_path: Path) -> None:
    # Finding #10: two declarations sharing a start line are ambiguous by line; the
    # by-line index must mark them ambiguous rather than last-write-wins, so a frame
    # at that line resolves by name when possible and otherwise falls to the file.
    graph = _graph(
        _file("src/app.ts"),
        _fn("src/app.ts", "alpha", 7),
        _fn("src/app.ts", "beta", 7),
    )
    resolver = NodeResolver(tmp_path, graph)
    # Ambiguous line + a name matching NEITHER declaration → file node, NOT a
    # silently-guessed alpha/beta (the old last-write-wins behaviour).
    assert resolver.resolve_frame(_url(tmp_path, "src/app.ts"), 7, "gamma") == "src/app.ts"
    # A matching name still resolves precisely via the name index.
    assert resolver.resolve_frame(_url(tmp_path, "src/app.ts"), 7, "beta") == "src/app.ts:beta"


def test_in_project_file_without_file_node_is_unresolved(tmp_path: Path) -> None:
    # File frame for a .ts the static graph did not index → visible, not dropped.
    graph = _graph(_file("src/other.ts"))
    resolver = NodeResolver(tmp_path, graph)
    assert resolver.resolve_frame(_url(tmp_path, "src/app.ts"), 5, "x") == UNRESOLVED


def test_pseudo_frames_are_filtered(tmp_path: Path) -> None:
    resolver = NodeResolver(tmp_path, _graph(_file("src/app.ts")))
    for pseudo in ("(root)", "(program)", "(idle)", "(garbage collector)"):
        assert resolver.resolve_frame("", None, pseudo) is None


def test_node_internal_and_other_schemes_filtered(tmp_path: Path) -> None:
    resolver = NodeResolver(tmp_path, _graph(_file("src/app.ts")))
    assert resolver.resolve_frame("node:internal/modules/esm/loader", 10, "load") is None
    assert resolver.resolve_frame("https://cdn.example/x.js", 1, "f") is None
    assert resolver.resolve_frame("", None, "anything") is None


def test_file_outside_root_filtered(tmp_path: Path) -> None:
    resolver = NodeResolver(tmp_path, _graph(_file("src/app.ts")))
    outside = (tmp_path.parent / "elsewhere" / "x.ts").as_uri()
    assert resolver.resolve_frame(outside, 1, "f") is None


def test_source_path_round_trip(tmp_path: Path) -> None:
    resolver = NodeResolver(tmp_path, _graph(_file("src/app.ts")))
    path = resolver.source_path(_url(tmp_path, "src/app.ts"))
    assert path is not None
    assert path == tmp_path / "src" / "app.ts"
    assert resolver.source_path("node:internal/x") is None
