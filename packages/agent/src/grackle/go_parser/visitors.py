"""Tree-sitter visitors for Go.

Node-ID scheme (mirrors python_parser/visitors.py):
  file       → <posix-relative-path>              e.g. models/user.go
  struct     → <file-id>:<TypeName>               e.g. models/user.go:User
  interface  → <file-id>:<InterfaceName>
  function   → <file-id>:<funcName>
  method     → <file-id>:<ReceiverType>.<methodName>
  type_alias → <file-id>:<TypeName>
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tree_sitter import Node, Tree

from grackle.python_parser.visitors import GraphBuilder

__all__ = ["GraphBuilder", "GoFileVisitor"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _t(node: Node) -> str:
    return node.text.decode("utf-8")  # type: ignore[union-attr]


def _string_value(node: Node) -> str:
    """Strip surrounding quotes from a string literal node."""
    text = _t(node)
    if len(text) >= 2 and (
        (text[0] == '"' and text[-1] == '"') or (text[0] == "`" and text[-1] == "`")
    ):
        return text[1:-1]
    return text


def _find(node: Node, *types: str) -> Node | None:
    """Return the first named child whose type is in *types*, or None."""
    for child in node.named_children:
        if child.type in types:
            return child
    return None


def _type_name_from_node(node: Node) -> str | None:
    """Extract a simple name string from type_identifier, pointer_type, or qualified_type."""
    t = node.type
    if t == "type_identifier":
        return _t(node)
    if t == "pointer_type":
        inner = _find(node, "type_identifier")
        return _t(inner) if inner is not None else None
    if t == "qualified_type":
        name = node.child_by_field_name("name")
        return _t(name) if name is not None else None
    return None


# ---------------------------------------------------------------------------
# Call collector
# ---------------------------------------------------------------------------


class _CallCollector:
    """Recursively collect callee name-strings from a block subtree.

    Does not cross function_declaration or method_declaration boundaries —
    those are handled when their own nodes are visited.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def walk(self, node: Node) -> None:
        for child in node.named_children:
            t = child.type
            if t in ("function_declaration", "method_declaration"):
                continue
            if t == "call_expression":
                func = child.child_by_field_name("function")
                if func is not None:
                    name = self._extract_name(func)
                    if name:
                        self.calls.append(name)
                self.walk(child)
            else:
                self.walk(child)

    def _extract_name(self, node: Node) -> str | None:
        t = node.type
        if t == "identifier":
            return _t(node)
        if t == "selector_expression":
            operand = node.child_by_field_name("operand")
            field = node.child_by_field_name("field")
            if operand is not None and field is not None:
                return f"{_t(operand)}.{_t(field)}"
        return None


# ---------------------------------------------------------------------------
# GoFileVisitor
# ---------------------------------------------------------------------------


class GoFileVisitor:
    """Walk one Go syntax tree and populate a GraphBuilder."""

    def __init__(self, file_id: str, builder: GraphBuilder) -> None:
        self._file_id = file_id
        self._builder = builder

    def visit(self, tree: Tree) -> None:
        self._builder.add_node(
            {
                "id": self._file_id,
                "kind": "file",
                "name": self._file_id.rsplit("/", 1)[-1],
                "path": self._file_id,
            }
        )
        for child in tree.root_node.named_children:
            self._visit_top(child)

    def _visit_top(self, node: Node) -> None:
        t = node.type
        if t == "import_declaration":
            self._visit_imports(node)
        elif t == "function_declaration":
            self._visit_function(node)
        elif t == "method_declaration":
            self._visit_method(node)
        elif t == "type_declaration":
            self._visit_type_decl(node)

    # ------------------------------------------------------------------
    # Imports
    # ------------------------------------------------------------------

    def _visit_imports(self, node: Node) -> None:
        for child in node.named_children:
            if child.type == "import_spec_list":
                for spec in child.named_children:
                    if spec.type == "import_spec":
                        self._emit_import(spec)
            elif child.type == "import_spec":
                self._emit_import(child)

    def _emit_import(self, spec: Node) -> None:
        path_node = spec.child_by_field_name("path")
        if path_node is None:
            return
        import_path = _string_value(path_node)

        name_node = spec.child_by_field_name("name")
        alias: str | None = None
        if name_node is not None:
            raw_alias = _t(name_node)
            if raw_alias not in ("_", "."):
                alias = raw_alias

        meta: dict[str, Any] = {}
        if alias is not None:
            meta["alias"] = alias

        self._builder.add_edge(
            {"source": self._file_id, "target": import_path, "kind": "import", "metadata": meta}
        )

    # ------------------------------------------------------------------
    # Functions
    # ------------------------------------------------------------------

    def _visit_function(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        func_name = _t(name_node)
        func_id = f"{self._file_id}:{func_name}"
        line = node.start_point[0] + 1

        self._builder.add_node(
            {
                "id": func_id,
                "kind": "function",
                "name": func_name,
                "path": self._file_id,
                "line": line,
                "metadata": {},
            }
        )

        body = node.child_by_field_name("body")
        if body is not None:
            self._emit_calls(func_id, body)

    # ------------------------------------------------------------------
    # Methods
    # ------------------------------------------------------------------

    def _visit_method(self, node: Node) -> None:
        receiver = node.child_by_field_name("receiver")
        if receiver is None:
            return
        recv_type = self._extract_receiver_type(receiver)
        if recv_type is None:
            return

        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        method_name = _t(name_node)
        method_id = f"{self._file_id}:{recv_type}.{method_name}"
        line = node.start_point[0] + 1

        self._builder.add_node(
            {
                "id": method_id,
                "kind": "method",
                "name": method_name,
                "path": self._file_id,
                "line": line,
                "metadata": {"receiver": recv_type},
            }
        )

        body = node.child_by_field_name("body")
        if body is not None:
            self._emit_calls(method_id, body)

    def _extract_receiver_type(self, receiver: Node) -> str | None:
        """Extract the base type name from a receiver parameter list."""
        for child in receiver.named_children:
            if child.type == "parameter_declaration":
                type_node = child.child_by_field_name("type")
                if type_node is None:
                    continue
                name = _type_name_from_node(type_node)
                if name:
                    return name
        return None

    # ------------------------------------------------------------------
    # Type declarations
    # ------------------------------------------------------------------

    def _visit_type_decl(self, node: Node) -> None:
        for child in node.named_children:
            if child.type == "type_spec":
                self._visit_type_spec(child)
            elif child.type == "type_alias":
                self._visit_type_alias_decl(child)

    def _visit_type_spec(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        type_name = _t(name_node)
        type_node = node.child_by_field_name("type")
        if type_node is None:
            return
        line = node.start_point[0] + 1

        if type_node.type == "struct_type":
            self._visit_struct(type_name, line, type_node)
        elif type_node.type == "interface_type":
            self._visit_interface(type_name, line, type_node)
        else:
            self._builder.add_node(
                {
                    "id": f"{self._file_id}:{type_name}",
                    "kind": "type_alias",
                    "name": type_name,
                    "path": self._file_id,
                    "line": line,
                    "metadata": {},
                }
            )

    def _visit_type_alias_decl(self, node: Node) -> None:
        """Handle tree-sitter-go's explicit type_alias_declaration node (type X = Y)."""
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        type_name = _t(name_node)
        self._builder.add_node(
            {
                "id": f"{self._file_id}:{type_name}",
                "kind": "type_alias",
                "name": type_name,
                "path": self._file_id,
                "line": node.start_point[0] + 1,
                "metadata": {},
            }
        )

    # ------------------------------------------------------------------
    # Struct
    # ------------------------------------------------------------------

    def _visit_struct(self, type_name: str, line: int, struct_node: Node) -> None:
        struct_id = f"{self._file_id}:{type_name}"
        self._builder.add_node(
            {
                "id": struct_id,
                "kind": "struct",
                "name": type_name,
                "path": self._file_id,
                "line": line,
                "metadata": {},
            }
        )

        body = struct_node.child_by_field_name("body")
        if body is None:
            body = _find(struct_node, "field_declaration_list")
        if body is None:
            return

        for child in body.named_children:
            if child.type != "field_declaration":
                continue
            name_field = child.child_by_field_name("name")
            type_field = child.child_by_field_name("type")
            if name_field is None and type_field is not None:
                embedded_name = _type_name_from_node(type_field)
                if embedded_name:
                    self._builder.add_edge(
                        {
                            "source": struct_id,
                            "target": embedded_name,
                            "kind": "inherit",
                            "metadata": {"resolved": False},
                        }
                    )

    # ------------------------------------------------------------------
    # Interface
    # ------------------------------------------------------------------

    def _visit_interface(self, type_name: str, line: int, iface_node: Node) -> None:
        iface_id = f"{self._file_id}:{type_name}"
        methods = self._collect_interface_methods(iface_node)
        self._builder.add_node(
            {
                "id": iface_id,
                "kind": "interface",
                "name": type_name,
                "path": self._file_id,
                "line": line,
                "metadata": {"methods": methods},
            }
        )

    def _collect_interface_methods(self, node: Node) -> list[str]:
        """Recursively collect method names from an interface_type node."""
        methods: list[str] = []
        self._collect_methods_recursive(node, methods)
        return methods

    def _collect_methods_recursive(self, node: Node, methods: list[str]) -> None:
        for child in node.named_children:
            if child.type in ("method_spec", "method_elem"):
                name_node = child.child_by_field_name("name")
                if name_node is not None:
                    methods.append(_t(name_node))
            elif child.type == "interface_body":
                self._collect_methods_recursive(child, methods)

    # ------------------------------------------------------------------
    # Call edges
    # ------------------------------------------------------------------

    def _emit_calls(self, caller_id: str, body: Node) -> None:
        collector = _CallCollector()
        collector.walk(body)
        for callee in collector.calls:
            self._builder.add_edge(
                {
                    "source": caller_id,
                    "target": callee,
                    "kind": "call",
                    "metadata": {"resolved": False},
                }
            )
