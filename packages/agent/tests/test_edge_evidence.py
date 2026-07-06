"""Edge evidence (Phase 10.4, ADR-0026).

Every static edge kind carries a 1-based ``metadata.line`` pointing at the
justifying source construct, across all four adapters, and that line survives
graph resolution. Cross-language edges carry the call-site line and degrade
cleanly when a hint has none.
"""

from __future__ import annotations

import ast
import textwrap
from typing import TYPE_CHECKING, Any

from grackle.cross_language import resolve_cross_language_edges
from grackle.go_parser.visitors import GoFileVisitor
from grackle.python_parser.resolver import resolve_graph as resolve_python
from grackle.python_parser.visitors import FileVisitor, GraphBuilder
from grackle.rust_parser.visitors import RustFileVisitor
from grackle.tree_sitter_runtime import _reset_for_testing, get_parser
from grackle.typescript_parser.resolver import resolve_graph as resolve_ts
from grackle.typescript_parser.visitors import TSFileVisitor

if TYPE_CHECKING:
    from pathlib import Path

    from grackle.adapters.base import GraphEdge, StaticGraph


def setup_function() -> None:
    _reset_for_testing()


# ---------------------------------------------------------------------------
# Per-language parse helpers → the raw (pre-resolution) edge list
# ---------------------------------------------------------------------------


def _py_edges(source: str, file_id: str = "test.py") -> list[GraphEdge]:
    builder = GraphBuilder()
    FileVisitor(file_id, builder).visit(ast.parse(textwrap.dedent(source)))
    return builder.edges


def _ts_edges(source: str, file_id: str = "test.ts") -> list[GraphEdge]:
    builder = GraphBuilder()
    tree = get_parser("typescript").parse(source.encode())
    TSFileVisitor(file_id, builder).visit(tree)
    return builder.edges


def _go_edges(source: str, file_id: str = "test.go") -> list[GraphEdge]:
    builder = GraphBuilder()
    tree = get_parser("go").parse(source.encode())
    GoFileVisitor(file_id, builder).visit(tree)
    return builder.edges


def _rs_edges(source: str, file_id: str = "src/lib.rs") -> list[GraphEdge]:
    builder = GraphBuilder()
    tree = get_parser("rust").parse(source.encode())
    RustFileVisitor(file_id, builder).visit(tree)
    return builder.edges


def _of_kind(edges: list[GraphEdge], kind: str) -> list[GraphEdge]:
    return [e for e in edges if e["kind"] == kind]


def _line(edge: GraphEdge) -> Any:
    return edge.get("metadata", {}).get("line")


# ---------------------------------------------------------------------------
# Python emission
# ---------------------------------------------------------------------------


def test_python_import_edge_line() -> None:
    edges = _py_edges("import os\n\nfrom sys import argv\n")
    imports = _of_kind(edges, "import")
    by_target = {e["target"]: _line(e) for e in imports}
    assert by_target["os"] == 1
    assert by_target["sys"] == 3


def test_python_call_edge_line() -> None:
    edges = _py_edges(
        """
        def helper():
            pass

        def caller():
            helper()
        """
    )
    call = _of_kind(edges, "call")[0]
    assert _line(call) == 6  # the `helper()` call site, not the def line


def test_python_inherit_edge_line_resolved_and_unresolved() -> None:
    # Resolved (same-file base) and unresolved (dotted base) both carry a line.
    resolved = _of_kind(
        _py_edges("class Base:\n    pass\n\nclass Sub(Base):\n    pass\n"), "inherit"
    )[0]
    assert _line(resolved) == 4
    unresolved = _of_kind(_py_edges("class Sub(models.Base):\n    pass\n"), "inherit")[0]
    assert unresolved["metadata"]["resolved"] is False
    assert _line(unresolved) == 1


def test_python_two_calls_same_target_distinct_lines() -> None:
    edges = _py_edges(
        """
        def helper():
            pass

        def caller():
            helper()
            helper()
        """
    )
    calls = [e for e in _of_kind(edges, "call") if e["target"] == "helper"]
    assert sorted(_line(e) for e in calls) == [6, 7]


# ---------------------------------------------------------------------------
# TypeScript emission
# ---------------------------------------------------------------------------


def test_ts_import_edge_line() -> None:
    edge = _of_kind(_ts_edges("\nimport { foo } from './a';\n"), "import")[0]
    assert _line(edge) == 2


def test_ts_call_edge_line() -> None:
    edge = _of_kind(_ts_edges("function run() {\n  helper();\n}\n"), "call")[0]
    assert _line(edge) == 2


def test_ts_inherit_and_implements_lines() -> None:
    src = "interface I {}\nclass B {}\nclass A extends B implements I {}\n"
    edges = _ts_edges(src)
    assert _line(_of_kind(edges, "inherit")[0]) == 3
    assert _line(_of_kind(edges, "implements")[0]) == 3


# ---------------------------------------------------------------------------
# Go emission
# ---------------------------------------------------------------------------


def test_go_import_edge_line() -> None:
    edge = _of_kind(_go_edges('package main\n\nimport "fmt"\n'), "import")[0]
    assert _line(edge) == 3


def test_go_call_edge_line() -> None:
    src = "package main\n\nfunc caller() {\n\thelper()\n}\n"
    edge = _of_kind(_go_edges(src), "call")[0]
    assert _line(edge) == 4


def test_go_embedded_inherit_line() -> None:
    src = "package main\n\ntype Base struct{}\n\ntype Sub struct {\n\tBase\n}\n"
    edge = _of_kind(_go_edges(src), "inherit")[0]
    assert _line(edge) == 6


# ---------------------------------------------------------------------------
# Rust emission
# ---------------------------------------------------------------------------


def test_rust_use_import_line() -> None:
    edge = _of_kind(_rs_edges("\nuse std::fmt;\n"), "import")[0]
    assert _line(edge) == 2


def test_rust_call_edge_line() -> None:
    edge = _of_kind(_rs_edges("fn run() {\n    helper();\n}\n"), "call")[0]
    assert _line(edge) == 2


def test_rust_supertrait_and_impl_lines() -> None:
    inherit = _of_kind(_rs_edges("trait Base {}\ntrait Sub: Base {}\n"), "inherit")[0]
    assert _line(inherit) == 2
    impl = _of_kind(_rs_edges("struct S;\ntrait T {}\nimpl T for S {}\n"), "implements")[0]
    assert _line(impl) == 3


# ---------------------------------------------------------------------------
# Resolver preservation — line survives resolution, resolved marker dropped
# ---------------------------------------------------------------------------


def _graph(lang: str, nodes: list[Any], edges: list[Any]) -> StaticGraph:
    return {"version": 1, "language": lang, "nodes": nodes, "edges": edges}


def test_python_resolver_preserves_line_on_resolved_call() -> None:
    nodes = [
        {"id": "a.py", "kind": "file", "name": "a.py", "path": "a.py"},
        {"id": "a.py:helper", "kind": "function", "name": "helper", "path": "a.py"},
        {"id": "a.py:caller", "kind": "function", "name": "caller", "path": "a.py"},
    ]
    edges = [
        {
            "source": "a.py:caller",
            "target": "helper",
            "kind": "call",
            "metadata": {"resolved": False, "line": 9},
        }
    ]
    resolved = _of_kind(resolve_python(_graph("python", nodes, edges))["edges"], "call")[0]
    assert resolved["target"] == "a.py:helper"  # actually upgraded
    assert resolved["metadata"]["line"] == 9  # evidence survived
    assert "resolved" not in resolved["metadata"]  # marker dropped on resolved edge


def test_ts_resolver_preserves_line_on_resolved_call() -> None:
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
            "metadata": {"names": ["helper"], "type_only": False, "line": 1},
        },
        {
            "source": "a.ts:run",
            "target": "helper",
            "kind": "call",
            "metadata": {"resolved": False, "line": 4},
        },
    ]
    resolved = _of_kind(resolve_ts(_graph("typescript", nodes, edges))["edges"], "call")[0]
    assert resolved["target"] == "b.ts:helper"
    assert resolved["metadata"]["line"] == 4
    assert "resolved" not in resolved["metadata"]


def test_import_edge_line_survives_python_resolution() -> None:
    # Import edges pass through resolve_graph untouched — line must remain.
    nodes = [{"id": "a.py", "kind": "file", "name": "a.py", "path": "a.py"}]
    edges = [
        {
            "source": "a.py",
            "target": "os",
            "kind": "import",
            "metadata": {"relative": False, "line": 1},
        }
    ]
    imp = _of_kind(resolve_python(_graph("python", nodes, edges))["edges"], "import")[0]
    assert imp["metadata"]["line"] == 1


# ---------------------------------------------------------------------------
# Cross-language evidence
# ---------------------------------------------------------------------------


def _file_node(node_id: str) -> dict[str, Any]:
    return {"id": node_id, "kind": "file", "name": node_id, "path": node_id}


def test_cross_language_call_carries_line() -> None:
    hints = [
        {
            "kind": "http_client",
            "node_id": "client.py",
            "language": "python",
            "payload": {"path": "/api/users", "line": 12},
        },
        {
            "kind": "http_server",
            "node_id": "server.ts",
            "language": "typescript",
            "payload": {"path": "/api/users", "line": 3},
        },
    ]
    nodes = [_file_node("client.py"), _file_node("server.ts")]
    edge = resolve_cross_language_edges(hints, nodes)[0]
    assert edge["kind"] == "cross_language_call"
    assert edge["metadata"]["line"] == 12  # the CLIENT call-site line


def test_cross_language_spawn_carries_line() -> None:
    hints = [
        {
            "kind": "subprocess",
            "node_id": "main.py",
            "language": "python",
            "payload": {"command": "worker.py", "line": 5},
        }
    ]
    nodes = [_file_node("main.py"), _file_node("worker.py")]
    edge = resolve_cross_language_edges(hints, nodes)[0]
    assert edge["kind"] == "cross_language_spawn"
    assert edge["metadata"]["line"] == 5


def test_cross_language_line_absent_degrades() -> None:
    # A hint with no line (e.g. a stale-cache partial) must not carry a line
    # key and must not crash.
    hints = [
        {
            "kind": "http_client",
            "node_id": "client.py",
            "language": "python",
            "payload": {"path": "/api/users"},
        },
        {
            "kind": "http_server",
            "node_id": "server.ts",
            "language": "typescript",
            "payload": {"path": "/api/users"},
        },
    ]
    nodes = [_file_node("client.py"), _file_node("server.ts")]
    edge = resolve_cross_language_edges(hints, nodes)[0]
    assert "line" not in edge["metadata"]


# ---------------------------------------------------------------------------
# Go / Rust resolver preservation (need a project root)
# ---------------------------------------------------------------------------


def test_go_resolver_preserves_line_on_resolved_call(tmp_path: Path) -> None:
    from grackle.go_parser.resolver import resolve_graph as resolve_go

    (tmp_path / "go.mod").write_text("module example.com/app\n\ngo 1.21\n")
    nodes = [
        {"id": "test.go", "kind": "file", "name": "test.go", "path": "test.go"},
        {"id": "test.go:Helper", "kind": "function", "name": "Helper", "path": "test.go"},
        {"id": "test.go:Caller", "kind": "function", "name": "Caller", "path": "test.go"},
    ]
    edges = [
        {
            "source": "test.go:Caller",
            "target": "Helper",
            "kind": "call",
            "metadata": {"resolved": False, "line": 8},
        }
    ]
    resolved = _of_kind(resolve_go(_graph("go", nodes, edges), tmp_path)["edges"], "call")[0]
    assert resolved["target"] == "test.go:Helper"
    assert resolved["metadata"]["line"] == 8
    assert "resolved" not in resolved["metadata"]


def test_rust_resolver_preserves_line_on_resolved_call(tmp_path: Path) -> None:
    from grackle.rust_parser.resolver import resolve_graph as resolve_rust

    nodes = [
        {"id": "src/lib.rs", "kind": "file", "name": "lib.rs", "path": "src/lib.rs"},
        {"id": "src/lib.rs:helper", "kind": "function", "name": "helper", "path": "src/lib.rs"},
        {"id": "src/lib.rs:caller", "kind": "function", "name": "caller", "path": "src/lib.rs"},
    ]
    edges = [
        {
            "source": "src/lib.rs:caller",
            "target": "helper",
            "kind": "call",
            "metadata": {"resolved": False, "line": 3},
        }
    ]
    resolved = _of_kind(resolve_rust(_graph("rust", nodes, edges), tmp_path)["edges"], "call")[0]
    assert resolved["target"] == "src/lib.rs:helper"
    assert resolved["metadata"]["line"] == 3
    assert "resolved" not in resolved["metadata"]
