"""Symbol resolver: upgrades unresolved inherit/call edges after the AST walk.

Resolution is best-effort; dynamic Python constructs (getattr, metaclasses,
dependency injection) are marked ``unresolved`` with a ``reason`` metadata key.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, Any, Literal

if TYPE_CHECKING:
    from grackle.adapters.base import GraphEdge, GraphNode, StaticGraph


@dataclass(frozen=True, slots=True)
class Resolution:
    """Outcome of a name-resolution attempt."""

    source: Literal["local", "import", "method", "unresolved"]
    target_id: str | None
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class FileScope:
    """Per-file name-resolution context built from nodes and import edges."""

    file_id: str
    # bare name → node_id for same-file definitions
    local_defs: dict[str, str] = field(default_factory=dict)
    # simple_name → (module_path, original_name | None)
    # "User" from "from .models import User" → (".models", "User")
    # "json" from "import json" → ("json", None)
    import_map: dict[str, tuple[str, str | None]] = field(default_factory=dict)


@dataclass
class ProjectScope:
    """Cross-file resolution context built from the aggregated graph."""

    all_node_ids: set[str] = field(default_factory=set)
    # (file_id, bare_name) → node_id for class/function nodes
    exports: dict[tuple[str, str], str] = field(default_factory=dict)
    # dotted module name → file_id  (e.g. "services.auth" → "services/auth.py")
    module_to_file: dict[str, str] = field(default_factory=dict)


_BUILTINS: frozenset[str] = frozenset(
    {
        "abs",
        "all",
        "any",
        "bool",
        "bytes",
        "callable",
        "chr",
        "classmethod",
        "compile",
        "complex",
        "delattr",
        "dict",
        "dir",
        "divmod",
        "enumerate",
        "eval",
        "exec",
        "filter",
        "float",
        "format",
        "frozenset",
        "getattr",
        "globals",
        "hasattr",
        "hash",
        "help",
        "hex",
        "id",
        "input",
        "int",
        "isinstance",
        "issubclass",
        "iter",
        "len",
        "list",
        "locals",
        "map",
        "max",
        "memoryview",
        "min",
        "next",
        "object",
        "oct",
        "open",
        "ord",
        "pow",
        "print",
        "property",
        "range",
        "repr",
        "reversed",
        "round",
        "set",
        "setattr",
        "slice",
        "sorted",
        "staticmethod",
        "str",
        "sum",
        "super",
        "tuple",
        "type",
        "vars",
        "zip",
        "Exception",
        "ValueError",
        "TypeError",
        "KeyError",
        "IndexError",
        "AttributeError",
        "RuntimeError",
        "StopIteration",
        "OSError",
        "NotImplementedError",
        "OverflowError",
        "ZeroDivisionError",
    }
)


def _file_id_from_node_id(node_id: str) -> str:
    """Extract the file_id prefix from any node ID (split on first ':')."""
    return node_id.split(":", 1)[0]


def _resolve_relative_module(file_id: str, rel_module: str) -> str:
    """Convert a relative import path to an absolute module-like string.

    ``rel_module`` has leading dots, e.g. ``.models`` or ``..utils``.
    Returns a dotted absolute module path, or the raw string on failure.
    """
    dots = len(rel_module) - len(rel_module.lstrip("."))
    suffix = rel_module.lstrip(".")
    parts = file_id.replace("\\", "/").split("/")
    pkg_parts = parts[:-1]  # drop the filename
    up = dots - 1
    if up >= len(pkg_parts):
        pkg_parts = []
    elif up > 0:
        pkg_parts = pkg_parts[:-up]
    base = ".".join(pkg_parts)
    if suffix:
        return f"{base}.{suffix}" if base else suffix
    return base or rel_module


def _module_to_file_candidates(module: str) -> list[str]:
    """Return candidate file IDs for a dotted module name."""
    base = module.replace(".", "/")
    return [f"{base}.py", f"{base}/__init__.py"]


def build_file_scope(
    file_id: str,
    nodes: list[GraphNode],
    import_edges: list[GraphEdge],
) -> FileScope:
    """Build a FileScope for one file from its nodes and import edges."""
    scope = FileScope(file_id=file_id)

    for n in nodes:
        if n["path"] == file_id and n["kind"] in ("class", "function", "method"):
            scope.local_defs[n["name"]] = n["id"]

    for e in import_edges:
        target = e["target"]
        meta = e.get("metadata", {})
        names: list[str] | None = meta.get("names")
        alias: str | None = meta.get("alias")

        if names is not None:
            for name in names:
                key = alias if (alias and len(names) == 1) else name
                scope.import_map[key] = (target, name)
        else:
            bare = target.lstrip(".")
            default_key = alias if alias else (bare.split(".")[-1] if bare else target)
            scope.import_map[default_key] = (target, None)

    return scope


def build_project_scope(nodes: list[GraphNode]) -> ProjectScope:
    """Build a ProjectScope from the full set of collected nodes."""
    scope = ProjectScope()
    for n in nodes:
        scope.all_node_ids.add(n["id"])
        file_id = n["path"]
        kind = n["kind"]
        if kind in ("class", "function") and file_id:
            scope.exports[(file_id, n["name"])] = n["id"]
        if kind == "file":
            fid = n["id"]
            if fid.endswith("/__init__.py"):
                mod = fid[: -len("/__init__.py")].replace("/", ".")
            elif fid.endswith(".py"):
                mod = fid[: -len(".py")].replace("/", ".")
            else:
                continue
            scope.module_to_file[mod] = fid

    return scope


class SymbolResolver:
    """Resolve a name string to a Resolution using file + project context."""

    def __init__(self, file_scope: FileScope, project_scope: ProjectScope) -> None:
        self._fs = file_scope
        self._ps = project_scope

    def resolve_base(self, name: str) -> Resolution:
        """Resolve a base-class expression string."""
        return self._resolve(name)

    def resolve_call(self, callee: str) -> Resolution:
        """Resolve a call-target string."""
        parts = callee.split(".", 1)
        if parts[0] in ("self", "cls") and len(parts) == 2:
            return Resolution(source="method", target_id=None, metadata={"name": parts[1]})
        return self._resolve(callee)

    def _resolve(self, name: str) -> Resolution:
        if not name or name.startswith("<"):
            return Resolution(source="unresolved", target_id=None, metadata={"reason": "dynamic"})

        parts = name.split(".", 1)
        simple = parts[0]
        attr = parts[1] if len(parts) > 1 else None

        if attr is None and simple in _BUILTINS:
            return Resolution(source="unresolved", target_id=None, metadata={"reason": "builtin"})

        if attr is None and simple in self._fs.local_defs:
            return Resolution(source="local", target_id=self._fs.local_defs[simple], metadata={})

        if simple in self._fs.import_map:
            module_path, original = self._fs.import_map[simple]
            lookup = (original if original is not None else simple) if attr is None else attr
            return self._from_module(module_path, lookup)

        if attr is not None:
            return Resolution(
                source="unresolved",
                target_id=None,
                metadata={"reason": "not_imported", "callee": name},
            )

        return Resolution(
            source="unresolved",
            target_id=None,
            metadata={"reason": "not_found", "name": name},
        )

    def _from_module(self, module_path: str, name: str) -> Resolution:
        abs_module = (
            _resolve_relative_module(self._fs.file_id, module_path)
            if module_path.startswith(".")
            else module_path
        )
        file_id = self._ps.module_to_file.get(abs_module)
        if file_id:
            node_id = self._ps.exports.get((file_id, name))
            if node_id:
                return Resolution(source="import", target_id=node_id, metadata={})
        for candidate in _module_to_file_candidates(abs_module):
            node_id = self._ps.exports.get((candidate, name))
            if node_id:
                return Resolution(source="import", target_id=node_id, metadata={})
        return Resolution(
            source="unresolved",
            target_id=None,
            metadata={"reason": "not_found", "module": module_path, "name": name},
        )


def resolve_graph(graph: StaticGraph) -> StaticGraph:
    """Upgrade unresolved inherit and call edges using project-wide context."""
    all_nodes: list[GraphNode] = graph["nodes"]
    all_edges: list[GraphEdge] = graph["edges"]

    project_scope = build_project_scope(all_nodes)

    nodes_by_file: dict[str, list[GraphNode]] = {}
    import_edges_by_file: dict[str, list[GraphEdge]] = {}
    for n in all_nodes:
        fid = n["path"]
        if fid:
            nodes_by_file.setdefault(fid, []).append(n)
    for e in all_edges:
        if e["kind"] == "import":
            import_edges_by_file.setdefault(e["source"], []).append(e)

    resolver_cache: dict[str, SymbolResolver] = {}

    resolved_edges: list[GraphEdge] = []
    for e in all_edges:
        if e["kind"] not in ("inherit", "call"):
            resolved_edges.append(e)
            continue
        meta = e.get("metadata", {})
        if meta.get("resolved") is not False:
            resolved_edges.append(e)
            continue

        fid = _file_id_from_node_id(e["source"])
        if fid not in resolver_cache:
            fs = build_file_scope(
                fid,
                nodes_by_file.get(fid, []),
                import_edges_by_file.get(fid, []),
            )
            resolver_cache[fid] = SymbolResolver(fs, project_scope)
        resolver = resolver_cache[fid]

        target_name: str = e["target"]
        resolution = (
            resolver.resolve_call(target_name)
            if e["kind"] == "call"
            else resolver.resolve_base(target_name)
        )

        if resolution.source != "unresolved" and resolution.target_id is not None:
            resolved_edges.append(
                {
                    "source": e["source"],
                    "target": resolution.target_id,
                    "kind": e["kind"],
                    "metadata": resolution.metadata,
                }
            )
        else:
            resolved_edges.append(
                {
                    "source": e["source"],
                    "target": e["target"],
                    "kind": e["kind"],
                    "metadata": {**meta, **resolution.metadata},
                }
            )

    result: StaticGraph = {
        "version": graph["version"],
        "language": graph["language"],
        "nodes": graph["nodes"],
        "edges": resolved_edges,
    }
    if "metadata" in graph:
        result["metadata"] = graph["metadata"]
    return result
