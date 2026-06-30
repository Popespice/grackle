from __future__ import annotations

import ast
import textwrap
from typing import TYPE_CHECKING

from grackle.python_parser.visitors import FileVisitor, GraphBuilder

if TYPE_CHECKING:
    from grackle.adapters.base import GraphEdge, GraphNode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse(source: str) -> ast.Module:
    return ast.parse(textwrap.dedent(source))


def _visit(source: str, file_id: str = "test.py") -> GraphBuilder:
    tree = _parse(source)
    builder = GraphBuilder()
    FileVisitor(file_id, builder).visit(tree)
    return builder


def _nodes(builder: GraphBuilder, kind: str) -> list[GraphNode]:
    return [n for n in builder.nodes if n["kind"] == kind]


def _edges(builder: GraphBuilder, kind: str) -> list[GraphEdge]:
    return [e for e in builder.edges if e["kind"] == kind]


# ---------------------------------------------------------------------------
# ClassVisitor — class nodes
# ---------------------------------------------------------------------------


def test_class_node_emitted() -> None:
    b = _visit("class Foo:\n    pass\n")
    classes = _nodes(b, "class")
    assert len(classes) == 1
    assert classes[0]["id"] == "test.py:Foo"
    assert classes[0]["name"] == "Foo"
    assert classes[0]["path"] == "test.py"


def test_class_node_has_line() -> None:
    b = _visit("class Foo:\n    pass\n")
    assert _nodes(b, "class")[0]["line"] == 1


def test_nested_class_qualname() -> None:
    b = _visit(
        """
        class Outer:
            class Inner:
                pass
        """
    )
    classes = _nodes(b, "class")
    ids = {c["id"] for c in classes}
    assert "test.py:Outer" in ids
    assert "test.py:Outer.Inner" in ids


# ---------------------------------------------------------------------------
# ClassVisitor — inherit edges
# ---------------------------------------------------------------------------


def test_inherit_edge_same_file_resolved() -> None:
    b = _visit(
        """
        class User:
            pass

        class Admin(User):
            pass
        """
    )
    inherit = _edges(b, "inherit")
    assert len(inherit) == 1
    e = inherit[0]
    assert e["source"] == "test.py:Admin"
    assert e["target"] == "test.py:User"
    assert e.get("metadata", {}).get("resolved") is not False


def test_inherit_edge_cross_file_unresolved() -> None:
    b = _visit("class Admin(models.User):\n    pass\n")
    inherit = _edges(b, "inherit")
    assert len(inherit) == 1
    assert inherit[0]["target"] == "models.User"
    assert inherit[0]["metadata"]["resolved"] is False


def test_class_with_decorator() -> None:
    b = _visit("import dataclasses\n\n@dataclasses.dataclass\nclass Point:\n    pass\n")
    cls = _nodes(b, "class")[0]
    assert "dataclasses.dataclass" in cls["metadata"]["decorators"]


# ---------------------------------------------------------------------------
# FunctionVisitor — function nodes
# ---------------------------------------------------------------------------


def test_top_level_function_node() -> None:
    b = _visit("def foo():\n    pass\n")
    funcs = _nodes(b, "function")
    assert len(funcs) == 1
    assert funcs[0]["id"] == "test.py:foo"
    assert funcs[0]["kind"] == "function"


def test_async_function_flag() -> None:
    b = _visit("async def bar():\n    pass\n")
    funcs = _nodes(b, "function")
    assert funcs[0]["metadata"]["is_async"] is True


def test_method_kind() -> None:
    b = _visit(
        """
        class MyClass:
            def my_method(self):
                pass
        """
    )
    methods = _nodes(b, "method")
    assert len(methods) == 1
    assert methods[0]["id"] == "test.py:MyClass.my_method"
    assert methods[0]["kind"] == "method"


def test_function_records_decorators() -> None:
    b = _visit(
        """
        import functools

        @staticmethod
        @functools.lru_cache(maxsize=None)
        def cached():
            pass
        """
    )
    funcs = _nodes(b, "function")
    assert len(funcs) == 1
    decorators = funcs[0]["metadata"]["decorators"]
    assert "staticmethod" in decorators
    assert "functools.lru_cache" in decorators


def test_decorator_chain_property() -> None:
    b = _visit(
        """
        class Foo:
            @property
            def bar(self):
                return 1
        """
    )
    method = _nodes(b, "method")[0]
    assert "property" in method["metadata"]["decorators"]


def test_property_setter_does_not_duplicate_node() -> None:
    # Getter + setter share a name (and therefore a node ID per ADR-0005).
    # Regression guard for the GraphCanvas crash: graphology's addNode
    # throws on a duplicate ID, so the static graph must emit exactly one
    # node per ID even when Python allows multiple defs with that name.
    b = _visit(
        """
        class Foo:
            @property
            def bar(self):
                return self._bar

            @bar.setter
            def bar(self, value):
                self._bar = value
        """
    )
    methods = [n for n in _nodes(b, "method") if n["name"] == "bar"]
    assert len(methods) == 1
    # First definition (the getter) wins.
    assert "property" in methods[0]["metadata"]["decorators"]


def test_property_setter_call_edges_still_captured() -> None:
    # The setter's body is still visited for call edges, attributed to the
    # shared (deduplicated) node ID, even though it contributes no node.
    b = _visit(
        """
        class Foo:
            @property
            def bar(self):
                return self._bar

            @bar.setter
            def bar(self, value):
                validate(value)
        """
    )
    calls = _edges(b, "call")
    assert any(e["source"] == "test.py:Foo.bar" and e["target"] == "validate" for e in calls)


def test_overload_stub_not_emitted() -> None:
    b = _visit(
        """
        from typing import overload

        class Foo:
            @overload
            def get(self, key: str) -> str: ...
            @overload
            def get(self, key: str, default: str) -> str: ...
            def get(self, key, default=None):
                return self._d.get(key, default)
        """
    )
    methods = [n for n in _nodes(b, "method") if n["name"] == "get"]
    assert len(methods) == 1
    # The canonical node is the real implementation, not a stub.
    assert "overload" not in methods[0]["metadata"]["decorators"]


def test_typing_overload_attribute_form_not_emitted() -> None:
    b = _visit(
        """
        import typing

        class Foo:
            @typing.overload
            def get(self, key: str) -> str: ...
            def get(self, key):
                return self._d[key]
        """
    )
    methods = [n for n in _nodes(b, "method") if n["name"] == "get"]
    assert len(methods) == 1


def test_closure_qualname_includes_line() -> None:
    # Nested functions follow the closure scheme: <parent>.<name>.<line>.
    # Regression guard: ensure the line suffix appears exactly once, not duplicated.
    b = _visit(
        """
        def outer():
            def inner():
                pass
        """
    )
    closures = [n for n in _nodes(b, "function") if n["name"] == "inner"]
    assert len(closures) == 1
    # outer is on line 2, inner is on line 3 of the dedented source.
    assert closures[0]["id"] == "test.py:outer.inner.3"


def test_closure_in_method_qualname() -> None:
    # Closure inside a method picks up the class-qualified parent and adds line.
    b = _visit(
        """
        class C:
            def method(self):
                def helper():
                    pass
        """
    )
    helpers = [n for n in b.nodes if n["name"] == "helper"]
    assert len(helpers) == 1
    assert helpers[0]["id"] == "test.py:C.method.helper.4"
    assert helpers[0]["kind"] == "function"


def test_same_name_closures_disambiguated_by_line() -> None:
    b = _visit(
        """
        def outer():
            def inner():
                pass
            def inner():
                pass
        """
    )
    ids = {n["id"] for n in b.nodes if n["name"] == "inner"}
    assert len(ids) == 2  # different line numbers → different IDs


# ---------------------------------------------------------------------------
# ImportVisitor — import edges
# ---------------------------------------------------------------------------


def test_absolute_import() -> None:
    b = _visit("import json\n")
    imports = _edges(b, "import")
    assert len(imports) == 1
    assert imports[0]["target"] == "json"
    assert imports[0]["metadata"]["relative"] is False


def test_absolute_from_import() -> None:
    b = _visit("from os.path import join, exists\n")
    imports = _edges(b, "import")
    assert len(imports) == 1
    assert imports[0]["target"] == "os.path"
    assert imports[0]["metadata"]["relative"] is False
    assert "join" in imports[0]["metadata"]["names"]
    assert "exists" in imports[0]["metadata"]["names"]


def test_relative_import() -> None:
    b = _visit("from .utils import x\n")
    imports = _edges(b, "import")
    assert len(imports) == 1
    assert imports[0]["target"] == ".utils"
    assert imports[0]["metadata"]["relative"] is True


def test_relative_import_multi_dot() -> None:
    b = _visit("from ..foo import bar\n")
    imports = _edges(b, "import")
    assert imports[0]["target"] == "..foo"
    assert imports[0]["metadata"]["relative"] is True


def test_type_checking_import_tagged() -> None:
    b = _visit(
        """
        from __future__ import annotations
        from typing import TYPE_CHECKING

        if TYPE_CHECKING:
            import heavy_module
        """
    )
    imports = _edges(b, "import")
    heavy = next(e for e in imports if e["target"] == "heavy_module")
    assert heavy["metadata"].get("type_checking") is True


def test_typing_type_checking_variant() -> None:
    b = _visit(
        """
        import typing

        if typing.TYPE_CHECKING:
            import other
        """
    )
    imports = _edges(b, "import")
    other = next(e for e in imports if e["target"] == "other")
    assert other["metadata"].get("type_checking") is True


def test_conditional_try_import_tagged() -> None:
    b = _visit(
        """
        try:
            import ujson as json
        except ImportError:
            import json
        """
    )
    imports = _edges(b, "import")
    assert all(e["metadata"].get("conditional") is True for e in imports)


def test_import_inside_class_not_emitted() -> None:
    b = _visit(
        """
        class Foo:
            import os
        """
    )
    # Module-level imports only; class-body imports are ignored.
    import_edges = _edges(b, "import")
    assert all(e["target"] != "os" for e in import_edges)


# ---------------------------------------------------------------------------
# FileVisitor — file node
# ---------------------------------------------------------------------------


def test_file_node_emitted() -> None:
    b = _visit("x = 1\n", file_id="pkg/module.py")
    file_nodes = _nodes(b, "file")
    assert len(file_nodes) == 1
    assert file_nodes[0]["id"] == "pkg/module.py"
    assert file_nodes[0]["name"] == "module.py"
    assert file_nodes[0]["path"] == "pkg/module.py"


def test_empty_file_emits_only_file_node() -> None:
    b = _visit("", file_id="empty.py")
    assert len(b.nodes) == 1
    assert b.nodes[0]["kind"] == "file"
    assert len(b.edges) == 0
