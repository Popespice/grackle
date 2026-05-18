"""Tests for the TypeScript Tree-sitter visitors."""

from __future__ import annotations

from typing import TYPE_CHECKING

from grackle.tree_sitter_runtime import _reset_for_testing, get_parser
from grackle.typescript_parser.visitors import GraphBuilder, TSFileVisitor

if TYPE_CHECKING:
    from grackle.adapters.base import GraphEdge, GraphNode


def setup_function() -> None:
    _reset_for_testing()


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _parse(source: str, file_id: str = "test.ts") -> GraphBuilder:
    parser = get_parser("typescript")
    tree = parser.parse(source.encode())
    builder = GraphBuilder()
    TSFileVisitor(file_id, builder).visit(tree)
    return builder


def _nodes(builder: GraphBuilder, kind: str) -> list[GraphNode]:
    return [n for n in builder.nodes if n["kind"] == kind]


def _edges(builder: GraphBuilder, kind: str) -> list[GraphEdge]:
    return [e for e in builder.edges if e["kind"] == kind]


# ---------------------------------------------------------------------------
# File node
# ---------------------------------------------------------------------------


def test_file_node_emitted() -> None:
    b = _parse("", "src/foo.ts")
    files = _nodes(b, "file")
    assert len(files) == 1
    assert files[0]["id"] == "src/foo.ts"
    assert files[0]["name"] == "foo.ts"
    assert files[0]["path"] == "src/foo.ts"


# ---------------------------------------------------------------------------
# Class nodes
# ---------------------------------------------------------------------------


def test_class_node_emitted() -> None:
    b = _parse("class Foo {}")
    classes = _nodes(b, "class")
    assert len(classes) == 1
    assert classes[0]["id"] == "test.ts:Foo"
    assert classes[0]["name"] == "Foo"
    assert classes[0]["path"] == "test.ts"


def test_class_node_has_line() -> None:
    b = _parse("class Foo {}")
    assert _nodes(b, "class")[0]["line"] == 1


def test_exported_class_emitted() -> None:
    b = _parse("export class Bar {}")
    classes = _nodes(b, "class")
    assert len(classes) == 1
    assert classes[0]["id"] == "test.ts:Bar"


def test_export_default_class_emitted() -> None:
    b = _parse("export default class Admin {}")
    classes = _nodes(b, "class")
    assert len(classes) == 1
    assert classes[0]["name"] == "Admin"


# ---------------------------------------------------------------------------
# Interface nodes
# ---------------------------------------------------------------------------


def test_interface_node_emitted() -> None:
    b = _parse("export interface Serializable { serialize(): string; }")
    ifaces = _nodes(b, "interface")
    assert len(ifaces) == 1
    assert ifaces[0]["id"] == "test.ts:Serializable"
    assert ifaces[0]["name"] == "Serializable"


# ---------------------------------------------------------------------------
# Function nodes
# ---------------------------------------------------------------------------


def test_function_node_emitted() -> None:
    b = _parse("export function greet(): void {}")
    funcs = _nodes(b, "function")
    assert len(funcs) == 1
    assert funcs[0]["id"] == "test.ts:greet"
    assert funcs[0]["name"] == "greet"


def test_arrow_function_node_emitted() -> None:
    b = _parse("export const add = (a: number, b: number): number => a + b;")
    funcs = _nodes(b, "function")
    assert len(funcs) == 1
    assert funcs[0]["name"] == "add"
    assert funcs[0]["metadata"].get("arrow") is True


# ---------------------------------------------------------------------------
# Method nodes
# ---------------------------------------------------------------------------


def test_method_node_emitted() -> None:
    b = _parse("class Svc { run(): void {} }")
    methods = _nodes(b, "method")
    assert len(methods) == 1
    assert methods[0]["id"] == "test.ts:Svc.run"
    assert methods[0]["name"] == "run"
    assert methods[0]["metadata"]["class"] == "Svc"


def test_constructor_emitted_as_method() -> None:
    b = _parse("class Foo { constructor(x: number) {} }")
    methods = _nodes(b, "method")
    assert any(m["name"] == "constructor" for m in methods)


def test_static_method_emitted() -> None:
    b = _parse("class Foo { static create(): Foo { return new Foo(); } }")
    methods = _nodes(b, "method")
    assert any(m["name"] == "create" for m in methods)


# ---------------------------------------------------------------------------
# Type alias nodes
# ---------------------------------------------------------------------------


def test_type_alias_node_emitted() -> None:
    b = _parse("export type UserId = string;")
    aliases = _nodes(b, "type_alias")
    assert len(aliases) == 1
    assert aliases[0]["id"] == "test.ts:UserId"
    assert aliases[0]["name"] == "UserId"


# ---------------------------------------------------------------------------
# Enum nodes
# ---------------------------------------------------------------------------


def test_enum_node_emitted() -> None:
    b = _parse('export enum Status { Active = "ACTIVE" }')
    enums = _nodes(b, "enum")
    assert len(enums) == 1
    assert enums[0]["id"] == "test.ts:Status"
    assert enums[0]["name"] == "Status"


# ---------------------------------------------------------------------------
# Import edges
# ---------------------------------------------------------------------------


def test_import_named_edge_emitted() -> None:
    b = _parse('import { Foo, Bar } from "./models";')
    imports = _edges(b, "import")
    assert len(imports) == 1
    assert imports[0]["source"] == "test.ts"
    assert imports[0]["target"] == "./models"
    assert imports[0]["metadata"]["names"] == ["Foo", "Bar"]


def test_import_default_edge_emitted() -> None:
    b = _parse('import React from "react";')
    imports = _edges(b, "import")
    assert len(imports) == 1
    assert imports[0]["metadata"]["default"] == "React"


def test_import_type_only_flagged() -> None:
    b = _parse('import type { UserId } from "./types";')
    imports = _edges(b, "import")
    assert imports[0]["metadata"]["type_only"] is True


def test_import_namespace_flagged() -> None:
    b = _parse('import * as utils from "./utils";')
    imports = _edges(b, "import")
    assert imports[0]["metadata"].get("namespace") is True


# ---------------------------------------------------------------------------
# Inherit edges
# ---------------------------------------------------------------------------


def test_inherit_edge_cross_file_unresolved() -> None:
    b = _parse("class User extends BaseEntity {}")
    inherits = _edges(b, "inherit")
    assert len(inherits) == 1
    assert inherits[0]["source"] == "test.ts:User"
    assert inherits[0]["target"] == "BaseEntity"
    assert inherits[0]["metadata"].get("resolved") is False


def test_inherit_edge_same_file_resolved() -> None:
    b = _parse("class Base {}\nclass Child extends Base {}")
    inherits = _edges(b, "inherit")
    assert len(inherits) == 1
    assert inherits[0]["target"] == "test.ts:Base"
    assert inherits[0]["metadata"].get("resolved") is not False


# ---------------------------------------------------------------------------
# Implements edges
# ---------------------------------------------------------------------------


def test_implements_edges_emitted() -> None:
    b = _parse("class User extends Base implements Serializable, Printable {}")
    impls = _edges(b, "implements")
    assert len(impls) == 2
    targets = {e["target"] for e in impls}
    assert "Serializable" in targets
    assert "Printable" in targets
    for e in impls:
        assert e["metadata"].get("resolved") is False


def test_implements_same_file_resolved() -> None:
    b = _parse("interface Serializable {}\nclass Foo implements Serializable {}")
    impls = _edges(b, "implements")
    assert len(impls) == 1
    assert impls[0]["target"] == "test.ts:Serializable"
    assert impls[0]["metadata"].get("resolved") is not False


# ---------------------------------------------------------------------------
# Call edges
# ---------------------------------------------------------------------------


def test_call_edge_from_function_body() -> None:
    b = _parse("function run() { foo(); }")
    calls = _edges(b, "call")
    targets = [e["target"] for e in calls]
    assert "foo" in targets


def test_call_edge_from_method_body() -> None:
    b = _parse("class Svc { run() { helper(); } }")
    calls = _edges(b, "call")
    assert any(e["target"] == "helper" for e in calls)


def test_call_edge_new_expression() -> None:
    b = _parse("function make() { return new User(); }")
    calls = _edges(b, "call")
    assert any(e["target"] == "User" for e in calls)


def test_call_edge_method_call_emitted() -> None:
    b = _parse("function run() { obj.method(); }")
    calls = _edges(b, "call")
    assert any("obj.method" in e["target"] for e in calls)


def test_call_edges_marked_unresolved() -> None:
    b = _parse("function run() { foo(); bar(); }")
    for e in _edges(b, "call"):
        assert e["metadata"].get("resolved") is False


def test_calls_not_emitted_for_nested_function() -> None:
    b = _parse(
        """
        function outer() {}
        function inner() { outer(); }
        """
    )
    calls = _edges(b, "call")
    # Call to outer() should come from inner's body, not from file scope
    assert all(e["source"] == "test.ts:inner" for e in calls)
