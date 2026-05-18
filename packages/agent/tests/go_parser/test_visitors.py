"""Tests for GoFileVisitor."""

from __future__ import annotations

from typing import Any

from grackle.go_parser.visitors import GoFileVisitor, GraphBuilder
from grackle.tree_sitter_runtime import get_parser


def _parse(src: str, file_id: str = "test.go") -> dict[str, Any]:
    parser = get_parser("go")
    tree = parser.parse(src.encode())
    builder = GraphBuilder()
    GoFileVisitor(file_id, builder).visit(tree)
    return builder.partial()


def test_emits_file_node() -> None:
    result = _parse("package main\n", "pkg/test.go")
    ids = {n["id"] for n in result["nodes"]}
    assert "pkg/test.go" in ids
    file_node = next(n for n in result["nodes"] if n["id"] == "pkg/test.go")
    assert file_node["kind"] == "file"


def test_emits_function_node() -> None:
    result = _parse('package main\n\nfunc Greet() string { return "hi" }\n')
    kinds = {n["kind"] for n in result["nodes"]}
    assert "function" in kinds
    func_ids = {n["id"] for n in result["nodes"] if n["kind"] == "function"}
    assert "test.go:Greet" in func_ids


def test_emits_struct_node() -> None:
    result = _parse("package models\n\ntype User struct {\n\tName string\n}\n")
    struct_ids = {n["id"] for n in result["nodes"] if n["kind"] == "struct"}
    assert "test.go:User" in struct_ids


def test_emits_interface_node() -> None:
    result = _parse("package models\n\ntype Printer interface {\n\tPrint() string\n}\n")
    iface_ids = {n["id"] for n in result["nodes"] if n["kind"] == "interface"}
    assert "test.go:Printer" in iface_ids


def test_interface_stores_method_names() -> None:
    result = _parse("package models\n\ntype Printer interface {\n\tPrint() string\n\tClose()\n}\n")
    iface = next(n for n in result["nodes"] if n["kind"] == "interface")
    methods = iface.get("metadata", {}).get("methods", [])
    assert "Print" in methods
    assert "Close" in methods


def test_emits_type_alias_node() -> None:
    result = _parse("package models\n\ntype UserID = int\n")
    alias_ids = {n["id"] for n in result["nodes"] if n["kind"] == "type_alias"}
    assert "test.go:UserID" in alias_ids


def test_emits_method_node() -> None:
    result = _parse(
        "package models\n\ntype User struct { Name string }\n"
        "func (u *User) Print() string { return u.Name }\n"
    )
    method_ids = {n["id"] for n in result["nodes"] if n["kind"] == "method"}
    assert "test.go:User.Print" in method_ids


def test_method_stores_receiver() -> None:
    result = _parse("package models\n\nfunc (u *User) Print() string { return u.Name }\n")
    methods = [n for n in result["nodes"] if n["kind"] == "method"]
    assert len(methods) == 1
    assert methods[0].get("metadata", {}).get("receiver") == "User"


def test_emits_import_edge() -> None:
    result = _parse('package main\n\nimport "fmt"\n\nfunc main() {}\n')
    import_edges = [e for e in result["edges"] if e["kind"] == "import"]
    assert any(e["target"] == "fmt" for e in import_edges)


def test_emits_import_with_alias() -> None:
    result = _parse('package main\n\nimport f "fmt"\n\nfunc main() {}\n')
    import_edges = [e for e in result["edges"] if e["kind"] == "import"]
    assert any(
        e["target"] == "fmt" and e.get("metadata", {}).get("alias") == "f" for e in import_edges
    )


def test_emits_struct_embedding_inherit_edge() -> None:
    result = _parse(
        "package models\n\ntype Base struct { ID int }\n"
        "type User struct {\n\tBase\n\tName string\n}\n"
    )
    inherit_edges = [e for e in result["edges"] if e["kind"] == "inherit"]
    assert any(e["source"] == "test.go:User" and e["target"] == "Base" for e in inherit_edges)


def test_emits_call_edges_from_function_body() -> None:
    result = _parse('package main\n\nimport "fmt"\n\nfunc main() { fmt.Println("hi") }\n')
    call_edges = [e for e in result["edges"] if e["kind"] == "call"]
    assert any(e["target"] == "fmt.Println" for e in call_edges)


def test_blank_import_emitted_without_alias() -> None:
    result = _parse('package main\n\nimport _ "fmt"\n\nfunc main() {}\n')
    import_edges = [e for e in result["edges"] if e["kind"] == "import"]
    # Blank import: alias should not be stored (it's the _ identifier, we skip it)
    for e in import_edges:
        assert e.get("metadata", {}).get("alias") != "_"
