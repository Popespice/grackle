"""Tree-sitter visitors for TypeScript/TSX.

Node-ID scheme (mirrors python_parser/visitors.py):
  file       → <posix-relative-path>               e.g. src/models.ts
  class      → <file-id>:<ClassName>               e.g. src/models.ts:User
  interface  → <file-id>:<InterfaceName>
  function   → <file-id>:<funcName>
  method     → <file-id>:<ClassName>.<methodName>
  type_alias → <file-id>:<TypeName>
  enum       → <file-id>:<EnumName>
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tree_sitter import Node, Tree

from grackle.python_parser.visitors import GraphBuilder

__all__ = ["GraphBuilder", "TSFileVisitor"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _t(node: Node) -> str:
    return node.text.decode("utf-8")  # type: ignore[union-attr]


def _string_value(node: Node) -> str:
    """Extract string content without surrounding quotes."""
    for child in node.named_children:
        if child.type == "string_fragment":
            return _t(child)
    raw = _t(node)
    return raw[1:-1] if len(raw) >= 2 else raw


def _find(node: Node, *types: str) -> Node | None:
    """Return the first named child whose type is in *types*, or None."""
    for child in node.named_children:
        if child.type in types:
            return child
    return None


# ---------------------------------------------------------------------------
# Call collector
# ---------------------------------------------------------------------------


class _CallCollector:
    """Recursively collect callee name-strings from a subtree.

    Does not cross function_declaration or method_definition boundaries —
    those are handled when their own nodes are visited.
    """

    def __init__(self) -> None:
        # (callee name, 1-based source line of the call site)
        self.calls: list[tuple[str, int]] = []

    def walk(self, node: Node) -> None:
        for child in node.named_children:
            t = child.type
            if t in ("function_declaration", "method_definition"):
                continue
            if t == "call_expression":
                fn = child.child_by_field_name("function")
                if fn is None and child.named_children:
                    fn = child.named_children[0]
                if fn is not None and fn.type in (
                    "identifier",
                    "member_expression",
                    "property_identifier",
                    "type_identifier",
                ):
                    self.calls.append((_t(fn), fn.start_point[0] + 1))
                self.walk(child)
            elif t == "new_expression":
                ctor = child.child_by_field_name("constructor")
                if ctor is None and child.named_children:
                    ctor = child.named_children[0]
                if ctor is not None and ctor.type in ("identifier", "type_identifier"):
                    self.calls.append((_t(ctor), ctor.start_point[0] + 1))
                self.walk(child)
            else:
                self.walk(child)


# ---------------------------------------------------------------------------
# TSFileVisitor
# ---------------------------------------------------------------------------


class TSFileVisitor:
    """Walk one TypeScript/TSX syntax tree and populate a GraphBuilder."""

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
            self._visit_stmt(child)

    def _visit_stmt(self, node: Node) -> None:
        if node.type == "import_statement":
            self._visit_import(node)
        elif node.type == "export_statement":
            for child in node.named_children:
                self._visit_decl(child)
        else:
            self._visit_decl(node)

    def _visit_decl(self, node: Node) -> None:
        t = node.type
        if t == "class_declaration":
            self._visit_class(node)
        elif t == "interface_declaration":
            self._visit_interface(node)
        elif t == "function_declaration":
            self._visit_function(node)
        elif t == "type_alias_declaration":
            self._visit_type_alias(node)
        elif t == "enum_declaration":
            self._visit_enum(node)
        elif t in ("lexical_declaration", "variable_declaration"):
            self._visit_variable_decl(node)

    # ------------------------------------------------------------------
    # Imports
    # ------------------------------------------------------------------

    def _visit_import(self, node: Node) -> None:
        src_node = _find(node, "string")
        if src_node is None:
            return
        module = _string_value(src_node)

        type_only = any(not c.is_named and c.type == "type" for c in node.children)

        meta: dict[str, Any] = {"type_only": type_only, "line": node.start_point[0] + 1}

        clause = _find(node, "import_clause")
        if clause is not None:
            names: list[str] = []
            for child in clause.named_children:
                if child.type == "identifier":
                    meta["default"] = _t(child)
                elif child.type == "named_imports":
                    for spec in child.named_children:
                        if spec.type == "import_specifier":
                            name_node = spec.child_by_field_name("name")
                            if name_node is None:
                                name_node = _find(spec, "identifier")
                            if name_node is not None:
                                original = _t(name_node)
                                names.append(original)
                                alias_node = spec.child_by_field_name("alias")
                                if alias_node is not None:
                                    aliases: dict[str, str] = meta.setdefault("aliases", {})
                                    aliases[original] = _t(alias_node)
                elif child.type == "namespace_import":
                    meta["namespace"] = True
            if names:
                meta["names"] = names

        self._builder.add_edge(
            {"source": self._file_id, "target": module, "kind": "import", "metadata": meta}
        )

    # ------------------------------------------------------------------
    # Classes
    # ------------------------------------------------------------------

    def _visit_class(self, node: Node) -> None:
        name_node = node.child_by_field_name("name") or _find(node, "type_identifier")
        if name_node is None:
            return
        class_name = _t(name_node)
        class_id = f"{self._file_id}:{class_name}"
        line = node.start_point[0] + 1

        self._builder.add_node(
            {
                "id": class_id,
                "kind": "class",
                "name": class_name,
                "path": self._file_id,
                "line": line,
                "metadata": {},
            }
        )

        heritage = _find(node, "class_heritage")
        if heritage is not None:
            extends = _find(heritage, "extends_clause")
            if extends is not None:
                for c in extends.named_children:
                    base_name = self._type_name(c)
                    if base_name:
                        self._emit_inherit(class_id, base_name, c.start_point[0] + 1)

            implements = _find(heritage, "implements_clause")
            if implements is not None:
                for c in implements.named_children:
                    iface_name = self._type_name(c)
                    if iface_name:
                        self._emit_implements(class_id, iface_name, c.start_point[0] + 1)

        body = node.child_by_field_name("body")
        if body is not None:
            for child in body.named_children:
                if child.type == "method_definition":
                    self._visit_method(child, class_id, class_name)

    def _type_name(self, node: Node) -> str | None:
        """Extract a simple name from identifier, type_identifier, or generic_type."""
        if node.type in ("identifier", "type_identifier"):
            return _t(node)
        if node.type == "generic_type":
            name_node = node.child_by_field_name("name") or _find(
                node, "type_identifier", "identifier"
            )
            return _t(name_node) if name_node is not None else None
        return None

    def _emit_inherit(self, class_id: str, base_name: str, line: int) -> None:
        local_id = self._find_local(base_name)
        self._builder.add_edge(
            {
                "source": class_id,
                "target": local_id if local_id else base_name,
                "kind": "inherit",
                "metadata": {"line": line} if local_id else {"resolved": False, "line": line},
            }
        )

    def _emit_implements(self, class_id: str, iface_name: str, line: int) -> None:
        local_id = self._find_local(iface_name)
        self._builder.add_edge(
            {
                "source": class_id,
                "target": local_id if local_id else iface_name,
                "kind": "implements",
                "metadata": {"line": line} if local_id else {"resolved": False, "line": line},
            }
        )

    def _find_local(self, name: str) -> str | None:
        for n in self._builder.nodes:
            if n.get("name") == name and n.get("path") == self._file_id:
                return n["id"]
        return None

    # ------------------------------------------------------------------
    # Methods
    # ------------------------------------------------------------------

    def _visit_method(self, node: Node, class_id: str, class_name: str) -> None:
        name_node = node.child_by_field_name("name") or _find(node, "property_identifier")
        if name_node is None:
            return
        method_name = _t(name_node)
        method_id = f"{class_id}.{method_name}"
        line = node.start_point[0] + 1

        self._builder.add_node(
            {
                "id": method_id,
                "kind": "method",
                "name": method_name,
                "path": self._file_id,
                "line": line,
                "metadata": {"class": class_name},
            }
        )

        body = node.child_by_field_name("body")
        if body is not None:
            self._emit_calls(method_id, body)

    # ------------------------------------------------------------------
    # Interfaces, functions, type aliases, enums
    # ------------------------------------------------------------------

    def _visit_interface(self, node: Node) -> None:
        name_node = node.child_by_field_name("name") or _find(node, "type_identifier")
        if name_node is None:
            return
        self._builder.add_node(
            {
                "id": f"{self._file_id}:{_t(name_node)}",
                "kind": "interface",
                "name": _t(name_node),
                "path": self._file_id,
                "line": node.start_point[0] + 1,
                "metadata": {},
            }
        )

    def _visit_function(self, node: Node) -> None:
        name_node = node.child_by_field_name("name") or _find(node, "identifier")
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

    def _visit_type_alias(self, node: Node) -> None:
        name_node = node.child_by_field_name("name") or _find(node, "type_identifier")
        if name_node is None:
            return
        self._builder.add_node(
            {
                "id": f"{self._file_id}:{_t(name_node)}",
                "kind": "type_alias",
                "name": _t(name_node),
                "path": self._file_id,
                "line": node.start_point[0] + 1,
                "metadata": {},
            }
        )

    def _visit_enum(self, node: Node) -> None:
        name_node = node.child_by_field_name("name") or _find(node, "identifier")
        if name_node is None:
            return
        self._builder.add_node(
            {
                "id": f"{self._file_id}:{_t(name_node)}",
                "kind": "enum",
                "name": _t(name_node),
                "path": self._file_id,
                "line": node.start_point[0] + 1,
                "metadata": {},
            }
        )

    def _visit_variable_decl(self, node: Node) -> None:
        """Emit function nodes for const/let arrow-function declarations."""
        for child in node.named_children:
            if child.type != "variable_declarator":
                continue
            name_node = child.child_by_field_name("name")
            value_node = child.child_by_field_name("value")
            if name_node is None or value_node is None:
                continue
            if value_node.type not in ("arrow_function", "function_expression"):
                continue
            func_name = _t(name_node)
            func_id = f"{self._file_id}:{func_name}"
            self._builder.add_node(
                {
                    "id": func_id,
                    "kind": "function",
                    "name": func_name,
                    "path": self._file_id,
                    "line": child.start_point[0] + 1,
                    "metadata": {"arrow": True},
                }
            )
            body = value_node.child_by_field_name("body")
            if body is not None:
                self._emit_calls(func_id, body)

    # ------------------------------------------------------------------
    # Call edges
    # ------------------------------------------------------------------

    def _emit_calls(self, caller_id: str, body: Node) -> None:
        collector = _CallCollector()
        collector.walk(body)
        for callee, line in collector.calls:
            self._builder.add_edge(
                {
                    "source": caller_id,
                    "target": callee,
                    "kind": "call",
                    "metadata": {"resolved": False, "line": line},
                }
            )
