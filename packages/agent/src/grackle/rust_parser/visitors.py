"""Tree-sitter visitors for Rust.

Node-ID scheme (mirrors go_parser/visitors.py):
  file       → <posix-relative-path>               e.g. crates/models/src/lib.rs
  struct     → <file-id>:<TypeName>                e.g. crates/models/src/lib.rs:User
  interface  → <file-id>:<TraitName>               (Rust trait rendered as interface)
  function   → <file-id>:<funcName>
  method     → <file-id>:<ImplType>.<methodName>
  type_alias → <file-id>:<TypeName>
  enum       → <file-id>:<EnumName>

Traits are emitted as ``kind="interface"`` with ``metadata.subkind="trait"`` to
reuse the existing visual identity; see ADR-0010.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from tree_sitter import Node, Tree

from grackle.python_parser.visitors import GraphBuilder

__all__ = ["GraphBuilder", "RustFileVisitor"]


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _t(node: Node) -> str:
    return node.text.decode("utf-8")  # type: ignore[union-attr]


def _find_child(node: Node, *types: str) -> Node | None:
    for child in node.named_children:
        if child.type in types:
            return child
    return None


def _extract_type_name(node: Node | None) -> str | None:
    """Return a simple name string from a type node.

    Handles type_identifier, generic_type (returns base name), and
    scoped_type_identifier (returns the last segment).
    """
    if node is None:
        return None
    t = node.type
    if t == "type_identifier":
        return _t(node)
    if t == "generic_type":
        # e.g. Vec<User> — return "Vec"
        name = node.child_by_field_name("type")
        if name is not None:
            return _extract_type_name(name)
        name_node = _find_child(node, "type_identifier")
        return _t(name_node) if name_node else None
    if t == "scoped_type_identifier":
        # e.g. models::User — return "User" (last segment)
        name = node.child_by_field_name("name")
        if name is not None:
            return _t(name)
        return None
    if t == "reference_type":
        # &User or &mut User — unwrap
        inner = node.child_by_field_name("type")
        return _extract_type_name(inner)
    if t == "mutable_specifier":
        return None
    return None


# ---------------------------------------------------------------------------
# Use-path extractor
# ---------------------------------------------------------------------------


def _extract_use_paths(node: Node) -> list[tuple[str, str | None]]:
    """Recursively extract (import_path, alias_or_None) pairs from a use clause."""
    t = node.type
    if t == "identifier":
        return [(_t(node), None)]
    if t == "scoped_identifier":
        return [(_t(node), None)]
    if t == "use_wildcard":
        text = _t(node)
        # Remove trailing "::*" if present for the edge target
        path = text[:-3] if text.endswith("::*") else text
        return [(path, None)]
    if t == "use_as_clause":
        path_node = node.child_by_field_name("path")
        alias_node = node.child_by_field_name("alias")
        alias = _t(alias_node) if alias_node is not None else None
        paths = _extract_use_paths(path_node) if path_node is not None else []
        return [(p, alias) for p, _ in paths]
    if t == "use_list":
        results: list[tuple[str, str | None]] = []
        for child in node.named_children:
            results.extend(_extract_use_paths(child))
        return results
    if t == "scoped_use_list":
        path_node = node.child_by_field_name("path")
        list_node = node.child_by_field_name("list")
        prefix = (_t(path_node) + "::") if path_node is not None else ""
        if list_node is not None:
            suffixes = _extract_use_paths(list_node)
            return [(f"{prefix}{p}", a) for p, a in suffixes]
        return []
    # Fallback: try the raw text
    return [(_t(node), None)]


# ---------------------------------------------------------------------------
# Call collector
# ---------------------------------------------------------------------------


class _CallCollector:
    """Recursively collect callee name-strings from a block subtree.

    Does not cross nested function_item or impl_item boundaries.

    In tree-sitter-rust, both free calls (``foo()``) and method calls
    (``self.method()``) are represented as ``call_expression``.  The
    ``function`` child is ``identifier`` for free calls, ``scoped_identifier``
    for associated-function calls (``Type::new()``), and ``field_expression``
    for method calls (``receiver.method()``).  There is no separate
    ``method_call_expression`` node.
    """

    def __init__(self) -> None:
        self.calls: list[str] = []

    def walk(self, node: Node) -> None:
        for child in node.named_children:
            t = child.type
            if t in ("function_item", "impl_item"):
                continue
            if t == "call_expression":
                func = child.child_by_field_name("function")
                if func is not None:
                    name = self._name_of(func)
                    if name:
                        self.calls.append(name)
                self.walk(child)
            else:
                self.walk(child)

    def _name_of(self, node: Node) -> str | None:
        t = node.type
        if t in ("identifier", "scoped_identifier"):
            return _t(node)
        if t == "field_expression":
            # receiver.method — return the full "receiver.method" text
            return _t(node)
        return None


# ---------------------------------------------------------------------------
# RustFileVisitor
# ---------------------------------------------------------------------------


class RustFileVisitor:
    """Walk one Rust syntax tree and populate a GraphBuilder."""

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
            self._visit_item(child)

    # ------------------------------------------------------------------
    # Dispatch
    # ------------------------------------------------------------------

    def _visit_item(self, node: Node) -> None:
        t = node.type
        if t == "function_item":
            self._visit_function(node, impl_type=None)
        elif t == "struct_item":
            self._visit_struct(node)
        elif t == "enum_item":
            self._visit_enum(node)
        elif t == "trait_item":
            self._visit_trait(node)
        elif t == "impl_item":
            self._visit_impl(node)
        elif t == "type_item":
            self._visit_type_alias(node)
        elif t == "use_declaration":
            self._visit_use(node)
        elif t == "mod_item":
            # inline mod: recurse; external mod (no body) adds no nodes here
            body = node.child_by_field_name("body")
            if body is not None:
                for child in body.named_children:
                    self._visit_item(child)

    # ------------------------------------------------------------------
    # Functions (top-level or impl body)
    # ------------------------------------------------------------------

    def _visit_function(self, node: Node, impl_type: str | None) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        func_name = _t(name_node)
        line = node.start_point[0] + 1

        if impl_type is not None:
            node_id = f"{self._file_id}:{impl_type}.{func_name}"
            meta: dict[str, Any] = {"receiver": impl_type}
            self._builder.add_node(
                {
                    "id": node_id,
                    "kind": "method",
                    "name": func_name,
                    "path": self._file_id,
                    "line": line,
                    "metadata": meta,
                }
            )
        else:
            node_id = f"{self._file_id}:{func_name}"
            self._builder.add_node(
                {
                    "id": node_id,
                    "kind": "function",
                    "name": func_name,
                    "path": self._file_id,
                    "line": line,
                    "metadata": {},
                }
            )

        body = node.child_by_field_name("body")
        if body is not None:
            self._emit_calls(node_id, body)

    # ------------------------------------------------------------------
    # Struct
    # ------------------------------------------------------------------

    def _visit_struct(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        type_name = _t(name_node)
        self._builder.add_node(
            {
                "id": f"{self._file_id}:{type_name}",
                "kind": "struct",
                "name": type_name,
                "path": self._file_id,
                "line": node.start_point[0] + 1,
                "metadata": {},
            }
        )

    # ------------------------------------------------------------------
    # Enum
    # ------------------------------------------------------------------

    def _visit_enum(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        type_name = _t(name_node)
        self._builder.add_node(
            {
                "id": f"{self._file_id}:{type_name}",
                "kind": "enum",
                "name": type_name,
                "path": self._file_id,
                "line": node.start_point[0] + 1,
                "metadata": {},
            }
        )

    # ------------------------------------------------------------------
    # Trait (→ interface kind with subkind=trait)
    # ------------------------------------------------------------------

    def _visit_trait(self, node: Node) -> None:
        name_node = node.child_by_field_name("name")
        if name_node is None:
            return
        trait_name = _t(name_node)
        trait_id = f"{self._file_id}:{trait_name}"
        line = node.start_point[0] + 1

        # Collect supertrait bounds: `trait Foo: Bar + Baz`
        bounds_node = node.child_by_field_name("bounds")
        supertraits: list[str] = []
        if bounds_node is not None:
            for child in bounds_node.named_children:
                sname = _extract_type_name(child)
                if sname:
                    supertraits.append(sname)

        self._builder.add_node(
            {
                "id": trait_id,
                "kind": "interface",
                "name": trait_name,
                "path": self._file_id,
                "line": line,
                "metadata": {"subkind": "trait", "supertraits": supertraits},
            }
        )

        # Emit unresolved inherit edges for each supertrait
        for st in supertraits:
            self._builder.add_edge(
                {
                    "source": trait_id,
                    "target": st,
                    "kind": "inherit",
                    "metadata": {"resolved": False},
                }
            )

        # Recurse into trait body for default methods
        body = node.child_by_field_name("body")
        if body is not None:
            for child in body.named_children:
                if child.type == "function_item":
                    self._visit_function(child, impl_type=trait_name)

    # ------------------------------------------------------------------
    # Impl block
    # ------------------------------------------------------------------

    def _visit_impl(self, node: Node) -> None:
        type_node = node.child_by_field_name("type")
        type_name = _extract_type_name(type_node)
        if type_name is None:
            return

        trait_node = node.child_by_field_name("trait")
        if trait_node is not None:
            # `impl Trait for Type` → unresolved implements edge
            trait_name = _extract_type_name(trait_node) or _t(trait_node)
            if trait_name:
                self._builder.add_edge(
                    {
                        "source": f"{self._file_id}:{type_name}",
                        "target": trait_name,
                        "kind": "implements",
                        "metadata": {"resolved": False},
                    }
                )

        body = node.child_by_field_name("body")
        if body is not None:
            for child in body.named_children:
                if child.type == "function_item":
                    self._visit_function(child, impl_type=type_name)

    # ------------------------------------------------------------------
    # Type alias
    # ------------------------------------------------------------------

    def _visit_type_alias(self, node: Node) -> None:
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
    # Use declarations
    # ------------------------------------------------------------------

    def _visit_use(self, node: Node) -> None:
        # The clause is the first named child (skips 'use' keyword token)
        clause = next((c for c in node.named_children if c.type != "use_declaration"), None)
        if clause is None and node.named_children:
            clause = node.named_children[0]
        if clause is None:
            return

        for path, alias in _extract_use_paths(clause):
            if not path:
                continue
            meta: dict[str, Any] = {}
            if alias is not None:
                meta["alias"] = alias
            self._builder.add_edge(
                {
                    "source": self._file_id,
                    "target": path,
                    "kind": "import",
                    "metadata": meta,
                }
            )

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
