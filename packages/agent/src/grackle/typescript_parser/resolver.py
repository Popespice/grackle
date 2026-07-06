"""Cross-file symbol resolver for TypeScript.

Mirrors python_parser/resolver.py: builds FileScope + ProjectScope from the
collected graph, then upgrades unresolved inherit/implements/call edges.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from grackle.adapters.base import GraphEdge, GraphNode, StaticGraph


# ---------------------------------------------------------------------------
# Module-path resolution
# ---------------------------------------------------------------------------

_TS_EXTENSIONS = (".ts", ".tsx", ".mts", ".cts")


def _normalize_posix(path: str) -> str:
    """Collapse . and .. components in a slash-separated path string."""
    parts = path.split("/")
    out: list[str] = []
    for part in parts:
        if part == "..":
            if out and out[-1] != "..":
                out.pop()
            else:
                out.append(part)
        elif part and part != ".":
            out.append(part)
    return "/".join(out)


def _resolve_ts_module(
    importing_file: str, module_path: str, all_file_ids: frozenset[str]
) -> str | None:
    """Convert a relative TS module specifier to an absolute file ID.

    Returns None for external (bare) specifiers like 'react' or 'lodash'.
    """
    if not module_path.startswith("."):
        return None

    base_dir = importing_file.rsplit("/", 1)[0] if "/" in importing_file else ""
    raw = f"{base_dir}/{module_path}" if base_dir else module_path
    normalized = _normalize_posix(raw)

    # Direct file + extension variants
    for ext in _TS_EXTENSIONS:
        cand = f"{normalized}{ext}"
        if cand in all_file_ids:
            return cand

    # Index file variants
    for ext in (".ts", ".tsx"):
        cand = f"{normalized}/index{ext}"
        if cand in all_file_ids:
            return cand

    # .js → .ts remapping (TS 4.7+ bundler mode)
    if normalized.endswith(".js"):
        base = normalized[:-3]
        for ext in (".ts", ".tsx"):
            cand = f"{base}{ext}"
            if cand in all_file_ids:
                return cand

    return None


# ---------------------------------------------------------------------------
# Scope data structures
# ---------------------------------------------------------------------------


@dataclass
class FileScope:
    """Per-file name → node_id table."""

    file_id: str
    local_defs: dict[str, str] = field(default_factory=dict)
    # local_name → (resolved_file_id | None, original_exported_name | None)
    import_map: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)


@dataclass
class ProjectScope:
    """Cross-file: all file IDs and (file_id, name) → node_id exports table."""

    all_file_ids: frozenset[str] = field(default_factory=frozenset)
    exports: dict[tuple[str, str], str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Scope builders
# ---------------------------------------------------------------------------


def build_project_scope(nodes: list[GraphNode]) -> ProjectScope:
    all_file_ids = frozenset(n["id"] for n in nodes if n["kind"] == "file")
    exports: dict[tuple[str, str], str] = {}
    for n in nodes:
        if n["kind"] in ("class", "interface", "function", "type_alias", "enum", "method"):
            exports[(n["path"], n["name"])] = n["id"]
    return ProjectScope(all_file_ids=all_file_ids, exports=exports)


def build_file_scope(
    file_id: str,
    nodes: list[GraphNode],
    import_edges: list[GraphEdge],
    project_scope: ProjectScope,
) -> FileScope:
    scope = FileScope(file_id=file_id)

    for n in nodes:
        if n["path"] == file_id:
            scope.local_defs[n["name"]] = n["id"]

    for e in import_edges:
        if e["kind"] != "import":
            continue
        meta = e.get("metadata", {})
        module_path: str = e["target"]
        resolved_file = _resolve_ts_module(file_id, module_path, project_scope.all_file_ids)

        names: list[str] = meta.get("names", [])
        default_name: str | None = meta.get("default")
        aliases: dict[str, str] = meta.get("aliases", {})

        for name in names:
            local_name = aliases.get(name, name)
            scope.import_map[local_name] = (resolved_file, name)

        if default_name is not None:
            scope.import_map[default_name] = (resolved_file, None)

    return scope


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


class SymbolResolver:
    def __init__(self, file_scope: FileScope, project_scope: ProjectScope) -> None:
        self._fs = file_scope
        self._ps = project_scope

    def resolve(self, name: str) -> str | None:
        """Resolve a bare name to a node_id, or None if not found."""
        simple = name.split(".", 1)[0]

        if simple in self._fs.local_defs:
            return self._fs.local_defs[simple]

        if simple in self._fs.import_map:
            resolved_file, original = self._fs.import_map[simple]
            if resolved_file is None:
                return None
            lookup = original if original is not None else simple
            return self._ps.exports.get((resolved_file, lookup))

        return None


# ---------------------------------------------------------------------------
# resolve_graph
# ---------------------------------------------------------------------------


def resolve_graph(graph: StaticGraph) -> StaticGraph:
    """Upgrade unresolved inherit, implements, and call edges in-place."""
    all_nodes: list[GraphNode] = graph["nodes"]
    all_edges: list[GraphEdge] = graph["edges"]

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
                nodes_by_file.get(fid, []),
                import_edges_by_file.get(fid, []),
                project_scope,
            )
            resolver_cache[fid] = SymbolResolver(fs, project_scope)
        return resolver_cache[fid]

    resolved_edges: list[GraphEdge] = []
    for e in all_edges:
        if e["kind"] not in ("inherit", "implements", "call"):
            resolved_edges.append(e)
            continue
        meta = e.get("metadata", {})
        if meta.get("resolved") is not False:
            resolved_edges.append(e)
            continue

        fid = e["source"].split(":", 1)[0]
        node_id = _resolver(fid).resolve(e["target"])

        if node_id is not None:
            # Carry forward edge evidence (e.g. ``line``, ADR-0026); drop the
            # ``resolved`` marker on the now-resolved edge.
            evidence = {k: v for k, v in meta.items() if k != "resolved"}
            resolved_edges.append(
                {
                    "source": e["source"],
                    "target": node_id,
                    "kind": e["kind"],
                    "metadata": evidence,
                }
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

    result: StaticGraph = {
        "version": graph["version"],
        "language": graph["language"],
        "nodes": graph["nodes"],
        "edges": resolved_edges,
    }
    if "metadata" in graph:
        result["metadata"] = graph["metadata"]
    return result
