"""Tests for RustFileVisitor."""

from __future__ import annotations

from typing import Any

from grackle.python_parser.visitors import GraphBuilder
from grackle.rust_parser.visitors import RustFileVisitor
from grackle.tree_sitter_runtime import get_parser


def _parse(source: str, file_id: str = "src/lib.rs") -> tuple[dict[str, Any], dict[str, Any]]:
    parser = get_parser("rust")
    content = source.encode("utf-8")
    tree = parser.parse(content)
    builder = GraphBuilder()
    RustFileVisitor(file_id, builder).visit(tree)
    partial = builder.partial()
    nodes_by_id = {n["id"]: n for n in partial["nodes"]}
    return nodes_by_id, partial


def test_file_node_emitted() -> None:
    nodes, _ = _parse("", "src/lib.rs")
    assert "src/lib.rs" in nodes
    assert nodes["src/lib.rs"]["kind"] == "file"


def test_struct_node() -> None:
    nodes, _ = _parse("pub struct User { pub id: u64 }")
    assert "src/lib.rs:User" in nodes
    assert nodes["src/lib.rs:User"]["kind"] == "struct"


def test_enum_node() -> None:
    nodes, _ = _parse("pub enum Status { Active, Inactive }")
    assert "src/lib.rs:Status" in nodes
    assert nodes["src/lib.rs:Status"]["kind"] == "enum"


def test_trait_emitted_as_interface() -> None:
    nodes, _ = _parse("pub trait Store { fn count(&self) -> usize; }")
    assert "src/lib.rs:Store" in nodes
    n = nodes["src/lib.rs:Store"]
    assert n["kind"] == "interface"
    assert n["metadata"]["subkind"] == "trait"


def test_trait_supertrait_inherit_edge() -> None:
    _, partial = _parse("pub trait UserStore: Store { fn list(&self); }")
    inherit_edges = [e for e in partial["edges"] if e["kind"] == "inherit"]
    assert len(inherit_edges) == 1
    assert inherit_edges[0]["source"] == "src/lib.rs:UserStore"
    assert inherit_edges[0]["target"] == "Store"
    assert inherit_edges[0]["metadata"]["resolved"] is False


def test_type_alias_node() -> None:
    nodes, _ = _parse("pub type UserId = u64;")
    assert "src/lib.rs:UserId" in nodes
    assert nodes["src/lib.rs:UserId"]["kind"] == "type_alias"


def test_function_node() -> None:
    nodes, _ = _parse("pub fn new_user() -> User { todo!() }")
    assert "src/lib.rs:new_user" in nodes
    assert nodes["src/lib.rs:new_user"]["kind"] == "function"


def test_impl_methods_are_method_nodes() -> None:
    src = """
struct User { id: u64 }
impl User {
    pub fn new(id: u64) -> Self { User { id } }
    pub fn is_valid(&self) -> bool { true }
}
"""
    nodes, _ = _parse(src)
    assert "src/lib.rs:User.new" in nodes
    assert nodes["src/lib.rs:User.new"]["kind"] == "method"
    assert nodes["src/lib.rs:User.new"]["metadata"]["receiver"] == "User"
    assert "src/lib.rs:User.is_valid" in nodes


def test_impl_trait_emits_implements_edge() -> None:
    src = """
struct User {}
trait Store {}
impl Store for User {}
"""
    _, partial = _parse(src)
    impls = [e for e in partial["edges"] if e["kind"] == "implements"]
    assert len(impls) == 1
    assert impls[0]["source"] == "src/lib.rs:User"
    assert impls[0]["target"] == "Store"
    assert impls[0]["metadata"]["resolved"] is False


def test_use_import_edge() -> None:
    _, partial = _parse("use std::collections::HashMap;")
    imports = [e for e in partial["edges"] if e["kind"] == "import"]
    assert any(e["target"] == "std::collections::HashMap" for e in imports)


def test_use_grouped_imports() -> None:
    _, partial = _parse("use models::{User, Store};")
    targets = {e["target"] for e in partial["edges"] if e["kind"] == "import"}
    assert "models::User" in targets
    assert "models::Store" in targets


def test_call_edges_emitted() -> None:
    src = """
fn helper() {}
fn caller() { helper(); }
"""
    _, partial = _parse(src)
    call_edges = [e for e in partial["edges"] if e["kind"] == "call"]
    assert any(e["target"] == "helper" for e in call_edges)


def test_method_call_edge() -> None:
    # tree-sitter-rust models `self.create()` as call_expression with a
    # field_expression function node, not as method_call_expression.
    src = "struct R {} impl R { fn create(&self) -> u64 { 0 } fn run(&self) { self.create(); } }"
    _, partial = _parse(src)
    call_edges = [e for e in partial["edges"] if e["kind"] == "call"]
    assert any("create" in e["target"] for e in call_edges)
