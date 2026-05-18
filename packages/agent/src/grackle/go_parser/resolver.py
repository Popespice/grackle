"""Cross-file symbol resolver for Go.

Mirrors typescript_parser/resolver.py: builds FileScope + ProjectScope from
the collected graph, then upgrades unresolved inherit/call edges and detects
implements relationships via method-set comparison.

Limitations (best-effort):
 - Implements detection only covers methods defined directly on the struct,
   not those promoted from embedded types.
 - Cross-package import resolution requires a go.mod in the project root.
 - Dot-imports (`import . "pkg"`) and blank imports are skipped.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from grackle.adapters.base import GraphEdge, GraphNode, StaticGraph


# ---------------------------------------------------------------------------
# go.mod reader
# ---------------------------------------------------------------------------


def _read_go_mod(root: Path) -> str | None:
    """Return the module path declared in go.mod, or None."""
    gomod = root / "go.mod"
    if not gomod.exists():
        return None
    try:
        for line in gomod.read_text(encoding="utf-8").splitlines():
            stripped = line.strip()
            if stripped.startswith("module "):
                return stripped[len("module ") :].strip()
    except OSError:
        pass
    return None


# ---------------------------------------------------------------------------
# Directory helpers
# ---------------------------------------------------------------------------


def _dir(file_id: str) -> str:
    """Return the directory part of a POSIX file path ('' for root-level files)."""
    if "/" in file_id:
        return file_id.rsplit("/", 1)[0]
    return ""


def _import_to_dir(import_path: str, module_path: str) -> str | None:
    """Convert a module import path to a relative directory, or None for external imports."""
    if module_path and import_path.startswith(module_path + "/"):
        return import_path[len(module_path) + 1 :]
    return None


# ---------------------------------------------------------------------------
# Scope data structures
# ---------------------------------------------------------------------------


@dataclass
class FileScope:
    """Per-file name → node_id table."""

    file_id: str
    # bare name → node_id for same-file + same-package definitions
    local_defs: dict[str, str] = field(default_factory=dict)
    # import alias → relative package directory (e.g. "models" → "models")
    import_map: dict[str, str] = field(default_factory=dict)


@dataclass
class ProjectScope:
    """Cross-file resolution context."""

    # (package_dir, exported_name) → node_id
    exports: dict[tuple[str, str], str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Scope builders
# ---------------------------------------------------------------------------


def build_project_scope(nodes: list[GraphNode]) -> ProjectScope:
    exports: dict[tuple[str, str], str] = {}
    for n in nodes:
        if n["kind"] in ("struct", "interface", "function", "type_alias"):
            file_id = n.get("path", "")
            if file_id:
                pkg_dir = _dir(file_id)
                exports[(pkg_dir, n["name"])] = n["id"]
    return ProjectScope(exports=exports)


def build_file_scope(
    file_id: str,
    nodes: list[GraphNode],
    import_edges: list[GraphEdge],
    module_path: str,
) -> FileScope:
    scope = FileScope(file_id=file_id)
    pkg_dir = _dir(file_id)

    # Same-package definitions (all files in the same directory)
    for n in nodes:
        if n["kind"] in ("struct", "interface", "function", "type_alias"):
            node_file = n.get("path", "")
            if node_file and _dir(node_file) == pkg_dir:
                scope.local_defs.setdefault(n["name"], n["id"])

    # Import map: derive alias from import path
    for e in import_edges:
        if e["kind"] != "import":
            continue
        import_path: str = e["target"]
        alias: str | None = e.get("metadata", {}).get("alias")

        pkg_dir_target = _import_to_dir(import_path, module_path)
        if pkg_dir_target is None:
            continue  # external package

        # Default alias is the last segment of the import path
        effective_alias = alias if alias else import_path.split("/")[-1]
        scope.import_map[effective_alias] = pkg_dir_target

    return scope


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


class SymbolResolver:
    def __init__(self, file_scope: FileScope, project_scope: ProjectScope) -> None:
        self._fs = file_scope
        self._ps = project_scope

    def resolve(self, name: str) -> str | None:
        """Resolve a name (or pkg.Name) to a node_id, or None."""
        if "." in name:
            parts = name.split(".", 1)
            pkg = parts[0]
            symbol = parts[1]
            pkg_dir = self._fs.import_map.get(pkg)
            if pkg_dir is not None:
                return self._ps.exports.get((pkg_dir, symbol))
            return None
        # Within-file or within-package
        return self._fs.local_defs.get(name)


# ---------------------------------------------------------------------------
# Implements detection
# ---------------------------------------------------------------------------


def _detect_implements(nodes: list[GraphNode]) -> list[GraphEdge]:
    """Emit implements edges for structs whose method set covers an interface.

    Resolution is best-effort: only considers methods declared directly on the
    struct (not promoted from embedded types). Interfaces with zero required
    methods are skipped (every type implements the empty interface).
    """
    # (package_dir, type_name) → set of method names provided
    struct_methods: dict[tuple[str, str], set[str]] = {}
    for n in nodes:
        if n["kind"] == "method":
            recv = n.get("metadata", {}).get("receiver", "")
            if recv:
                file_id = n.get("path", "")
                if file_id:
                    struct_methods.setdefault((_dir(file_id), recv), set()).add(n["name"])

    # (package_dir, iface_name) → (node_id, frozenset of required method names)
    iface_requirements: dict[tuple[str, str], tuple[str, frozenset[str]]] = {}
    for n in nodes:
        if n["kind"] == "interface":
            methods = n.get("metadata", {}).get("methods", [])
            if methods:
                file_id = n.get("path", "")
                if file_id:
                    iface_requirements[(_dir(file_id), n["name"])] = (
                        n["id"],
                        frozenset(methods),
                    )

    if not iface_requirements:
        return []

    # (package_dir, type_name) → struct node_id
    struct_ids: dict[tuple[str, str], str] = {}
    for n in nodes:
        if n["kind"] == "struct":
            file_id = n.get("path", "")
            if file_id:
                struct_ids[(_dir(file_id), n["name"])] = n["id"]

    new_edges: list[GraphEdge] = []
    for (pkg_dir, type_name), methods in struct_methods.items():
        struct_id = struct_ids.get((pkg_dir, type_name))
        if struct_id is None:
            continue
        for (_, _iface_name), (iface_id, required) in iface_requirements.items():
            if required and required.issubset(methods):
                new_edges.append(
                    {
                        "source": struct_id,
                        "target": iface_id,
                        "kind": "implements",
                        "metadata": {"via": "method_set"},
                    }
                )

    return new_edges


# ---------------------------------------------------------------------------
# resolve_graph
# ---------------------------------------------------------------------------


def resolve_graph(graph: StaticGraph, root: Path) -> StaticGraph:
    """Upgrade unresolved inherit and call edges, and detect implements edges."""
    all_nodes: list[GraphNode] = graph["nodes"]
    all_edges: list[GraphEdge] = graph["edges"]

    module_path = _read_go_mod(root) or ""
    project_scope = build_project_scope(all_nodes)

    nodes_by_file: dict[str, list[GraphNode]] = {}
    import_edges_by_file: dict[str, list[GraphEdge]] = {}

    for n in all_nodes:
        fid = n.get("path", "")
        if fid:
            nodes_by_file.setdefault(fid, []).append(n)
    for e in all_edges:
        if e["kind"] == "import":
            import_edges_by_file.setdefault(e["source"], []).append(e)

    resolver_cache: dict[str, SymbolResolver] = {}

    def _resolver(fid: str) -> SymbolResolver:
        if fid not in resolver_cache:
            fs = build_file_scope(
                fid,
                all_nodes,
                import_edges_by_file.get(fid, []),
                module_path,
            )
            resolver_cache[fid] = SymbolResolver(fs, project_scope)
        return resolver_cache[fid]

    resolved_edges: list[GraphEdge] = []
    for e in all_edges:
        if e["kind"] not in ("inherit", "call"):
            resolved_edges.append(e)
            continue
        meta = e.get("metadata", {})
        if meta.get("resolved") is not False:
            resolved_edges.append(e)
            continue

        fid = e["source"].split(":", 1)[0]
        node_id = _resolver(fid).resolve(e["target"])

        if node_id is not None:
            resolved_edges.append(
                {"source": e["source"], "target": node_id, "kind": e["kind"], "metadata": {}}
            )
        else:
            resolved_edges.append(
                {
                    "source": e["source"],
                    "target": e["target"],
                    "kind": e["kind"],
                    "metadata": {**meta, "resolved": False},
                }
            )

    # Detect and append implements edges
    resolved_edges.extend(_detect_implements(all_nodes))

    result: StaticGraph = {
        "version": graph["version"],
        "language": graph["language"],
        "nodes": graph["nodes"],
        "edges": resolved_edges,
    }
    if "metadata" in graph:
        result["metadata"] = graph["metadata"]
    return result
