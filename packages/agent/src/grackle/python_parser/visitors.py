"""AST visitors for the Python static parser.

GraphBuilder accumulates nodes and edges for one file. FileVisitor is the
top-level entry point; it delegates to ClassVisitor, FunctionVisitor, and
ImportVisitor for their respective domains.

Node ID scheme (ADR-0005):
  file     → <posix-relative-path>            e.g. services/auth.py
  class    → <file-id>:<qualname>             e.g. services/auth.py:AuthService
  function → <file-id>:<func-name>            e.g. utils.py:hash_password
  method   → <file-id>:<class-qualname>.<name> e.g. services/auth.py:AuthService.login
  closure  → <file-id>:<parent-qualname>.<name>.<lineno>

This scheme is name-based, so two distinct ``def``s/``class``es can compute
the same base ID (a ``@property`` getter/setter pair, ``@overload`` stubs,
a conditionally-redefined symbol). ``GraphBuilder.add_node`` disambiguates
on collision by suffixing ``.<lineno>``, mirroring the closure scheme above
— see its docstring for the full rule (including the @overload special case).
"""

from __future__ import annotations

import ast
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from grackle.adapters.base import GraphEdge, GraphNode


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _attr_to_str(node: ast.Attribute) -> str:
    """Convert an Attribute chain to a dotted string, e.g. app.route."""
    parts: list[str] = []
    cur: ast.expr = node
    while isinstance(cur, ast.Attribute):
        parts.append(cur.attr)
        cur = cur.value
    parts.append(cur.id if isinstance(cur, ast.Name) else "<expr>")
    return ".".join(reversed(parts))


def _expr_to_name(node: ast.expr) -> str | None:
    """Return a dotted name for a Name or Attribute expression, or None."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        prefix = _expr_to_name(node.value)
        if prefix is not None:
            return f"{prefix}.{node.attr}"
    return None


def _extract_decorators(decorator_list: list[ast.expr]) -> list[str]:
    """Return best-effort decorator names (no semantic resolution)."""
    result: list[str] = []
    for d in decorator_list:
        if isinstance(d, ast.Name):
            result.append(d.id)
        elif isinstance(d, ast.Attribute):
            result.append(_attr_to_str(d))
        elif isinstance(d, ast.Call):
            func = d.func
            if isinstance(func, ast.Name):
                result.append(func.id)
            elif isinstance(func, ast.Attribute):
                result.append(_attr_to_str(func))
            else:
                result.append("<complex>")
        else:
            result.append("<complex>")
    return result


def _is_type_checking_guard(test: ast.expr) -> bool:
    """Return True for ``if TYPE_CHECKING:`` or ``if typing.TYPE_CHECKING:``."""
    if isinstance(test, ast.Name) and test.id == "TYPE_CHECKING":
        return True
    return isinstance(test, ast.Attribute) and test.attr == "TYPE_CHECKING"


def _extract_platform_condition(test: ast.expr) -> str | None:
    """Return the platform string for ``if sys.platform == "<s>":`` or None."""
    if not isinstance(test, ast.Compare):
        return None
    if len(test.ops) != 1 or not isinstance(test.ops[0], ast.Eq):
        return None
    if len(test.comparators) != 1:
        return None
    left = test.left
    if not (
        isinstance(left, ast.Attribute)
        and left.attr == "platform"
        and isinstance(left.value, ast.Name)
        and left.value.id == "sys"
    ):
        return None
    right = test.comparators[0]
    if isinstance(right, ast.Constant) and isinstance(right.value, str):
        return right.value
    return None


# ---------------------------------------------------------------------------
# GraphBuilder
# ---------------------------------------------------------------------------


def _is_overload_node(node: GraphNode) -> bool:
    """Best-effort check for a PEP 484 ``@overload``-decorated definition.

    Used only to prefer a real implementation as the canonical (un-suffixed)
    node when an overload stub and its implementation collide on the same
    base ID — see ``GraphBuilder.add_node``. A name match on "overload" is
    best-effort per ADR-0004, not a guarantee: an unrelated decorator
    literally named ``overload`` would also match, but the worst outcome is
    a less-ideal canonical-node choice, never data loss — every definition
    still gets a node either way.
    """
    decorators = node.get("metadata", {}).get("decorators", [])
    return any(d == "overload" or d.endswith(".overload") for d in decorators)


class GraphBuilder:
    """Accumulates graph nodes and edges for a single file traversal."""

    def __init__(self) -> None:
        self.nodes: list[GraphNode] = []
        self.edges: list[GraphEdge] = []
        self._node_index: dict[str, int] = {}

    def add_node(self, node: GraphNode) -> str:
        """Append *node* and return the ID it was actually stored under.

        Node IDs are name-based (ADR-0005), so two distinct ``def``s can
        legitimately share a base ID — e.g. a ``@property`` getter and its
        ``.setter``/``.deleter``, PEP 484 ``@overload`` stubs and their
        implementation, or a conditionally-redefined function/class. Every
        definition still gets its own node — silently dropping one would
        both hide a real symbol from the graph and (for the property-accessor
        case) break runtime trace attribution, since the ``NodeResolver``
        indexes nodes by ``(path, line)`` and a dropped node's line becomes
        unresolvable.

        On a collision, the new node is suffixed with its own line number,
        mirroring the closure disambiguation scheme already used for nested
        functions (``<parent-qualname>.<name>.<lineno>``) — UNLESS the node
        already occupying the base ID is an ``@overload`` stub and the new
        one isn't, in which case the *stub* is demoted (suffixed) instead,
        so the canonical (un-suffixed) node always points at executable
        code rather than a type-checker-only placeholder.

        Without this, graphology's ``addNode`` throws client-side on the
        duplicate (the static graph contract requires unique node IDs).
        """
        base_id = node["id"]
        existing_idx = self._node_index.get(base_id)
        if existing_idx is None:
            self._node_index[base_id] = len(self.nodes)
            self.nodes.append(node)
            return base_id

        current = self.nodes[existing_idx]
        if _is_overload_node(current) and not _is_overload_node(node):
            demoted_id = f"{base_id}.{current.get('line', 0)}"
            current["id"] = demoted_id
            self._node_index[demoted_id] = existing_idx
            self._node_index[base_id] = len(self.nodes)
            self.nodes.append(node)
            return base_id

        suffixed_id = f"{base_id}.{node.get('line', 0)}"
        node["id"] = suffixed_id
        self._node_index[suffixed_id] = len(self.nodes)
        self.nodes.append(node)
        return suffixed_id

    def add_edge(self, edge: GraphEdge) -> None:
        self.edges.append(edge)

    def partial(self) -> dict[str, Any]:
        """Serialisable snapshot for the content-hash cache."""
        return {"nodes": list(self.nodes), "edges": list(self.edges)}


# ---------------------------------------------------------------------------
# ImportVisitor
# ---------------------------------------------------------------------------


class ImportVisitor(ast.NodeVisitor):
    """Emits import edges for module-level imports (not inside class/function bodies).

    Recognises three special contexts:
    - ``if TYPE_CHECKING:``  → metadata[type_checking] = True
    - ``try/except``         → metadata[conditional] = True
    - ``if sys.platform == "...":`` → metadata[platform] = "<value>"
    """

    def __init__(
        self,
        file_id: str,
        builder: GraphBuilder,
        *,
        in_type_checking: bool = False,
        in_try: bool = False,
        platform: str | None = None,
    ) -> None:
        self._file_id = file_id
        self._builder = builder
        self._in_type_checking = in_type_checking
        self._in_try = in_try
        self._platform = platform

    # Don't descend into class or function bodies — those imports are scoped.
    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        pass

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        pass

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self._emit(
                target=alias.name,
                relative=False,
                alias=alias.asname,
                names=None,
                line=node.lineno,
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        level = node.level
        module = node.module or ""
        target = ("." * level + module) if level > 0 else module
        names = [a.name for a in node.names if a.name != "*"] or None
        self._emit(target=target, relative=level > 0, alias=None, names=names, line=node.lineno)

    def visit_If(self, node: ast.If) -> None:
        if _is_type_checking_guard(node.test):
            old = self._in_type_checking
            self._in_type_checking = True
            for child in node.body:
                self.visit(child)
            self._in_type_checking = old
            for child in node.orelse:
                self.visit(child)
        else:
            platform = _extract_platform_condition(node.test)
            if platform is not None:
                old_p = self._platform
                self._platform = platform
                for child in node.body:
                    self.visit(child)
                self._platform = old_p
                for child in node.orelse:
                    self.visit(child)
            else:
                self.generic_visit(node)

    def visit_Try(self, node: ast.Try) -> None:
        old = self._in_try
        self._in_try = True
        self.generic_visit(node)
        self._in_try = old

    def _emit(
        self,
        target: str,
        relative: bool,
        alias: str | None,
        names: list[str] | None,
        line: int,
    ) -> None:
        metadata: dict[str, Any] = {"relative": relative, "line": line}
        if self._in_type_checking:
            metadata["type_checking"] = True
        if self._in_try:
            metadata["conditional"] = True
        if self._platform is not None:
            metadata["platform"] = self._platform
        if alias is not None:
            metadata["alias"] = alias
        if names is not None:
            metadata["names"] = names
        self._builder.add_edge(
            {
                "source": self._file_id,
                "target": target,
                "kind": "import",
                "metadata": metadata,
            }
        )


# ---------------------------------------------------------------------------
# _CallVisitor
# ---------------------------------------------------------------------------


class _CallVisitor(ast.NodeVisitor):
    """Emits unresolved call edges for direct calls within one function body.

    Does not descend into nested function or class definitions; those emit
    their own call edges when FunctionVisitor processes them separately.
    """

    def __init__(self, file_id: str, caller_id: str, builder: GraphBuilder) -> None:
        self._file_id = file_id
        self._caller_id = caller_id
        self._builder = builder

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        pass

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        pass

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        pass

    def visit_Call(self, node: ast.Call) -> None:
        callee = _expr_to_name(node.func)
        if callee is None and isinstance(node.func, ast.Attribute):
            callee = _attr_to_str(node.func)
        if callee is not None:
            self._builder.add_edge(
                {
                    "source": self._caller_id,
                    "target": callee,
                    "kind": "call",
                    "metadata": {"resolved": False, "line": node.lineno},
                }
            )
        self.generic_visit(node)


# ---------------------------------------------------------------------------
# FunctionVisitor
# ---------------------------------------------------------------------------


class FunctionVisitor(ast.NodeVisitor):
    """Emits function and method nodes.

    ``parent_qualname`` is the enclosing class or function qualname.
    ``node_kind`` should be ``"method"`` when called from ClassVisitor,
    ``"function"`` otherwise (top-level or closure).
    """

    def __init__(
        self,
        file_id: str,
        builder: GraphBuilder,
        parent_qualname: str = "",
        node_kind: str = "function",
    ) -> None:
        self._file_id = file_id
        self._builder = builder
        self._parent_qualname = parent_qualname
        self._node_kind = node_kind

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._emit(node, is_async=False)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._emit(node, is_async=True)

    def _emit(self, node: ast.FunctionDef | ast.AsyncFunctionDef, is_async: bool) -> None:
        # Qualname rules:
        # - top-level function  (no parent): "<name>"
        # - method              (parent + node_kind="method"): "<parent>.<name>"
        # - closure / nested fn (parent + node_kind="function"): "<parent>.<name>.<line>"
        if not self._parent_qualname:
            qualname = node.name
        elif self._node_kind == "method":
            qualname = f"{self._parent_qualname}.{node.name}"
        else:
            qualname = f"{self._parent_qualname}.{node.name}.{node.lineno}"
        node_id = f"{self._file_id}:{qualname}"

        # ``line`` must match ``code.co_firstlineno`` at runtime so the
        # ``NodeResolver`` (Phase 6 runtime tracer) can do an exact lookup.
        # CPython sets ``co_firstlineno`` to the first decorator's line when
        # decorators are present; otherwise to the ``def`` keyword line.
        # ``ast.FunctionDef.lineno`` is always the ``def`` line, so we adjust
        # explicitly here. Without this, every decorated function falls back
        # to the file-node ID at trace time.
        first_line = node.decorator_list[0].lineno if node.decorator_list else node.lineno

        actual_id = self._builder.add_node(
            {
                "id": node_id,
                "kind": self._node_kind,
                "name": node.name,
                "path": self._file_id,
                "line": first_line,
                "metadata": {
                    "qualname": qualname,
                    "is_async": is_async,
                    "decorators": _extract_decorators(node.decorator_list),
                },
            }
        )

        # Emit unresolved call edges from this function's body, attributed to
        # whichever ID add_node actually stored this definition under (may
        # differ from node_id on a same-name collision — see add_node).
        call_vis = _CallVisitor(self._file_id, actual_id, self._builder)
        for stmt in node.body:
            call_vis.visit(stmt)

        # Recurse into nested functions (always closures) and nested classes.
        for child in node.body:
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                FunctionVisitor(self._file_id, self._builder, qualname, "function").visit(child)
            elif isinstance(child, ast.ClassDef):
                ClassVisitor(self._file_id, self._builder, qualname).visit(child)


# ---------------------------------------------------------------------------
# ClassVisitor
# ---------------------------------------------------------------------------


class ClassVisitor(ast.NodeVisitor):
    """Emits class nodes, inheritance edges, and delegates methods to FunctionVisitor."""

    def __init__(
        self,
        file_id: str,
        builder: GraphBuilder,
        parent_qualname: str = "",
    ) -> None:
        self._file_id = file_id
        self._builder = builder
        self._parent_qualname = parent_qualname

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        qualname = f"{self._parent_qualname}.{node.name}" if self._parent_qualname else node.name
        class_id = f"{self._file_id}:{qualname}"

        # Mirror the decorator-line adjustment in FunctionVisitor._emit so the
        # static graph's ``line`` matches the class code object's
        # ``co_firstlineno`` at runtime.
        first_line = node.decorator_list[0].lineno if node.decorator_list else node.lineno

        actual_id = self._builder.add_node(
            {
                "id": class_id,
                "kind": "class",
                "name": node.name,
                "path": self._file_id,
                "line": first_line,
                "metadata": {
                    "qualname": qualname,
                    "decorators": _extract_decorators(node.decorator_list),
                },
            }
        )

        for base in node.bases:
            base_name = _expr_to_name(base)
            if base_name is None:
                continue
            target_id = self._resolve_local(base_name)
            if target_id is not None:
                self._builder.add_edge(
                    {
                        "source": actual_id,
                        "target": target_id,
                        "kind": "inherit",
                        "metadata": {"line": base.lineno},
                    }
                )
            else:
                self._builder.add_edge(
                    {
                        "source": actual_id,
                        "target": base_name,
                        "kind": "inherit",
                        "metadata": {"resolved": False, "line": base.lineno},
                    }
                )

        for child in node.body:
            if isinstance(child, ast.ClassDef):
                ClassVisitor(self._file_id, self._builder, qualname).visit(child)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                FunctionVisitor(self._file_id, self._builder, qualname, "method").visit(child)

    def _resolve_local(self, name: str) -> str | None:
        """Return the node ID of a same-file class whose unqualified name matches."""
        for n in self._builder.nodes:
            if n["kind"] == "class" and n["name"] == name:
                return n["id"]
        return None


# ---------------------------------------------------------------------------
# FileVisitor
# ---------------------------------------------------------------------------


class FileVisitor(ast.NodeVisitor):
    """Top-level visitor: creates the file node then delegates to domain visitors."""

    def __init__(self, file_id: str, builder: GraphBuilder) -> None:
        self._file_id = file_id
        self._builder = builder

    def visit_Module(self, node: ast.Module) -> None:
        self._builder.add_node(
            {
                "id": self._file_id,
                "kind": "file",
                "name": self._file_id.rsplit("/", 1)[-1],
                "path": self._file_id,
            }
        )

        import_visitor = ImportVisitor(self._file_id, self._builder)

        for child in node.body:
            # Import edges — ImportVisitor skips class/function bodies.
            import_visitor.visit(child)

            # Class and function nodes.
            if isinstance(child, ast.ClassDef):
                ClassVisitor(self._file_id, self._builder).visit(child)
            elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                FunctionVisitor(self._file_id, self._builder).visit(child)
