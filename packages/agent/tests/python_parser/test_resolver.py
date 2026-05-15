from __future__ import annotations

import ast
import textwrap
from typing import TYPE_CHECKING

from grackle.python_parser.resolver import (
    FileScope,
    ProjectScope,
    SymbolResolver,
    build_file_scope,
    build_project_scope,
    resolve_graph,
)
from grackle.python_parser.visitors import FileVisitor, GraphBuilder

if TYPE_CHECKING:
    from grackle.adapters.base import GraphEdge, GraphNode, StaticGraph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _visit(source: str, file_id: str = "test.py") -> GraphBuilder:
    tree = ast.parse(textwrap.dedent(source))
    builder = GraphBuilder()
    FileVisitor(file_id, builder).visit(tree)
    return builder


def _make_graph(sources: dict[str, str]) -> StaticGraph:
    nodes: list[GraphNode] = []
    edges: list[GraphEdge] = []
    for file_id, source in sources.items():
        b = _visit(source, file_id)
        nodes.extend(b.nodes)
        edges.extend(b.edges)
    return {"version": 1, "language": "python", "nodes": nodes, "edges": edges}


def _call_edges(graph: StaticGraph) -> list[GraphEdge]:
    return [e for e in graph["edges"] if e["kind"] == "call"]


def _inherit_edges(graph: StaticGraph) -> list[GraphEdge]:
    return [e for e in graph["edges"] if e["kind"] == "inherit"]


# ---------------------------------------------------------------------------
# call edges emitted by _CallVisitor
# ---------------------------------------------------------------------------


def test_call_edges_emitted_for_function() -> None:
    b = _visit(
        """
        def helper():
            pass

        def caller():
            helper()
        """
    )
    raw_calls = [e for e in b.edges if e["kind"] == "call"]
    assert any(e["target"] == "helper" for e in raw_calls)
    assert raw_calls[0]["metadata"]["resolved"] is False


def test_call_edges_nested_calls() -> None:
    # foo(bar()) should emit edges for both foo and bar
    b = _visit(
        """
        def work():
            foo(bar())
        """
    )
    raw_calls = [e for e in b.edges if e["kind"] == "call"]
    targets = {e["target"] for e in raw_calls}
    assert "foo" in targets
    assert "bar" in targets


def test_call_edges_method_call() -> None:
    b = _visit(
        """
        class A:
            def run(self):
                self.helper()
        """
    )
    raw_calls = [e for e in b.edges if e["kind"] == "call"]
    assert any(e["target"] == "self.helper" for e in raw_calls)
    assert all(e["source"] == "test.py:A.run" for e in raw_calls)


def test_call_edges_not_from_nested_function() -> None:
    # Calls inside a nested function belong to that nested function's node, not the outer.
    b = _visit(
        """
        def outer():
            def inner():
                helper()
        """
    )
    raw_calls = [e for e in b.edges if e["kind"] == "call"]
    inner_calls = [e for e in raw_calls if "inner" in e["source"]]
    outer_calls = [e for e in raw_calls if e["source"] == "test.py:outer"]
    assert len(inner_calls) == 1
    assert len(outer_calls) == 0


# ---------------------------------------------------------------------------
# SymbolResolver — unit tests
# ---------------------------------------------------------------------------


def test_symbol_resolver_local_base() -> None:
    fs = FileScope(
        file_id="models.py",
        local_defs={"Base": "models.py:Base"},
    )
    ps = ProjectScope(all_node_ids={"models.py:Base"})
    res = SymbolResolver(fs, ps).resolve_base("Base")
    assert res.source == "local"
    assert res.target_id == "models.py:Base"


def test_symbol_resolver_method_call() -> None:
    fs = FileScope(file_id="a.py")
    ps = ProjectScope()
    res = SymbolResolver(fs, ps).resolve_call("self.login")
    assert res.source == "method"
    assert res.target_id is None
    assert res.metadata["name"] == "login"


def test_symbol_resolver_cls_method_call() -> None:
    fs = FileScope(file_id="a.py")
    ps = ProjectScope()
    res = SymbolResolver(fs, ps).resolve_call("cls.create")
    assert res.source == "method"
    assert res.metadata["name"] == "create"


def test_symbol_resolver_builtin_unresolved() -> None:
    fs = FileScope(file_id="a.py")
    ps = ProjectScope()
    res = SymbolResolver(fs, ps).resolve_call("print")
    assert res.source == "unresolved"
    assert res.metadata["reason"] == "builtin"


def test_symbol_resolver_not_found_unresolved() -> None:
    fs = FileScope(file_id="a.py")
    ps = ProjectScope()
    res = SymbolResolver(fs, ps).resolve_call("unknown_func")
    assert res.source == "unresolved"
    assert "reason" in res.metadata


def test_symbol_resolver_import_resolution() -> None:
    fs = FileScope(
        file_id="caller.py",
        import_map={"User": ("models", "User")},
    )
    ps = ProjectScope(
        exports={("models.py", "User"): "models.py:User"},
        module_to_file={"models": "models.py"},
    )
    res = SymbolResolver(fs, ps).resolve_call("User")
    assert res.source == "import"
    assert res.target_id == "models.py:User"


# ---------------------------------------------------------------------------
# build_file_scope / build_project_scope
# ---------------------------------------------------------------------------


def test_build_file_scope_local_defs() -> None:
    b = _visit(
        """
        class Local:
            pass
        def helper():
            pass
        """,
        "svc.py",
    )
    import_edges: list[GraphEdge] = [e for e in b.edges if e["kind"] == "import"]
    scope = build_file_scope("svc.py", b.nodes, import_edges)
    assert "Local" in scope.local_defs
    assert scope.local_defs["Local"] == "svc.py:Local"
    assert "helper" in scope.local_defs


def test_build_file_scope_import_map() -> None:
    b = _visit(
        """
        from models import User, Admin
        import json
        """,
        "svc.py",
    )
    import_edges: list[GraphEdge] = [e for e in b.edges if e["kind"] == "import"]
    scope = build_file_scope("svc.py", b.nodes, import_edges)
    assert scope.import_map["User"] == ("models", "User")
    assert scope.import_map["Admin"] == ("models", "Admin")
    assert "json" in scope.import_map


def test_build_project_scope_exports() -> None:
    b = _visit("class User:\n    pass\n", "models.py")
    scope = build_project_scope(b.nodes)
    assert ("models.py", "User") in scope.exports
    assert scope.exports[("models.py", "User")] == "models.py:User"


def test_build_project_scope_module_to_file() -> None:
    b1 = _visit("", "models.py")
    b2 = _visit("", "services/auth.py")
    b3 = _visit("", "pkg/__init__.py")
    nodes: list[GraphNode] = b1.nodes + b2.nodes + b3.nodes
    scope = build_project_scope(nodes)
    assert scope.module_to_file["models"] == "models.py"
    assert scope.module_to_file["services.auth"] == "services/auth.py"
    assert scope.module_to_file["pkg"] == "pkg/__init__.py"


# ---------------------------------------------------------------------------
# resolve_graph — integration
# ---------------------------------------------------------------------------


def test_resolve_graph_local_call() -> None:
    graph = _make_graph(
        {
            "mod.py": """
            def helper():
                pass

            def caller():
                helper()
            """
        }
    )
    resolved = resolve_graph(graph)
    calls = _call_edges(resolved)
    helper_calls = [e for e in calls if e["target"] == "mod.py:helper"]
    assert len(helper_calls) == 1
    assert helper_calls[0]["metadata"].get("resolved") is not False


def test_resolve_graph_import_call() -> None:
    graph = _make_graph(
        {
            "models.py": "class User:\n    pass\n",
            "caller.py": """
            from models import User

            def factory():
                User()
            """,
        }
    )
    resolved = resolve_graph(graph)
    calls = _call_edges(resolved)
    user_calls = [e for e in calls if e["target"] == "models.py:User"]
    assert len(user_calls) == 1


def test_resolve_graph_method_call_stays_method() -> None:
    graph = _make_graph(
        {
            "a.py": """
            class A:
                def run(self):
                    self.other()
            """
        }
    )
    resolved = resolve_graph(graph)
    calls = _call_edges(resolved)
    method_calls = [e for e in calls if e.get("metadata", {}).get("name") == "other"]
    assert len(method_calls) == 1
    assert method_calls[0]["source"] == "a.py:A.run"


def test_resolve_graph_unresolved_call_keeps_reason() -> None:
    graph = _make_graph(
        {
            "mod.py": """
            def do_thing():
                totally_unknown()
            """
        }
    )
    resolved = resolve_graph(graph)
    calls = _call_edges(resolved)
    unresolved = [e for e in calls if e["target"] == "totally_unknown"]
    assert len(unresolved) == 1
    assert unresolved[0]["metadata"]["resolved"] is False
    assert "reason" in unresolved[0]["metadata"]


def test_resolve_graph_inherit_cross_file() -> None:
    graph = _make_graph(
        {
            "base.py": "class Base:\n    pass\n",
            "child.py": """
            from base import Base

            class Child(Base):
                pass
            """,
        }
    )
    resolved = resolve_graph(graph)
    inherits = _inherit_edges(resolved)
    resolved_inherit = [e for e in inherits if e["target"] == "base.py:Base"]
    assert len(resolved_inherit) == 1
    assert resolved_inherit[0]["metadata"].get("resolved") is not False


def test_resolve_graph_inherit_same_file_unchanged() -> None:
    # Same-file inherit is already resolved by ClassVisitor — should stay resolved.
    graph = _make_graph(
        {
            "mod.py": """
            class Base:
                pass
            class Child(Base):
                pass
            """
        }
    )
    resolved = resolve_graph(graph)
    inherits = _inherit_edges(resolved)
    assert len(inherits) == 1
    assert inherits[0]["target"] == "mod.py:Base"
    assert inherits[0]["metadata"].get("resolved") is not False


def test_resolve_graph_relative_import_call() -> None:
    graph = _make_graph(
        {
            "pkg/models.py": "class Item:\n    pass\n",
            "pkg/service.py": """
            from .models import Item

            def create():
                Item()
            """,
        }
    )
    resolved = resolve_graph(graph)
    calls = _call_edges(resolved)
    item_calls = [e for e in calls if e["target"] == "pkg/models.py:Item"]
    assert len(item_calls) == 1
