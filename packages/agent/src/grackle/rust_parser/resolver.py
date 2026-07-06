"""Cross-file symbol resolver for Rust.

Mirrors go_parser/resolver.py: builds per-file FileScope and a
WorkspaceScope from the collected graph, then upgrades unresolved
inherit/implements/call edges.

Limitations (best-effort):
 - Resolves `use crate_name::Symbol` for workspace crates only.
 - `use crate::module::Symbol` resolves within the same crate by
   matching the last segment across all same-crate node names.
 - Macro invocations and trait-object dyn dispatch are not resolved.
 - Generic type parameters are stripped when matching type names.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

from grackle.rust_parser.workspace import CrateInfo, get_crates

if TYPE_CHECKING:
    from pathlib import Path

    from grackle.adapters.base import GraphEdge, GraphNode, StaticGraph


# ---------------------------------------------------------------------------
# Scope data structures
# ---------------------------------------------------------------------------


@dataclass
class FileScope:
    """Per-file import resolution context."""

    file_id: str
    # symbol_name → node_id resolved from `use` statements
    import_map: dict[str, str] = field(default_factory=dict)


@dataclass
class CrateScope:
    """All exported names for one crate."""

    name: str
    posix_root: str  # POSIX prefix shared by all this crate's file_ids
    # symbol_name → node_id for top-level definitions
    exports: dict[str, str] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Scope builders
# ---------------------------------------------------------------------------


def _crate_for_file(file_id: str, crates: list[CrateInfo]) -> CrateInfo | None:
    """Return the CrateInfo whose root is a prefix of file_id."""
    for crate in crates:
        if crate.posix_root == "" or file_id.startswith(crate.posix_root + "/"):
            return crate
    # Fallback: use the first crate (handles single-crate non-workspace projects)
    return crates[0] if crates else None


def build_crate_scopes(nodes: list[GraphNode], crates: list[CrateInfo]) -> dict[str, CrateScope]:
    """Return a map from crate_name → CrateScope."""
    scopes: dict[str, CrateScope] = {
        c.name: CrateScope(name=c.name, posix_root=c.posix_root) for c in crates
    }
    for n in nodes:
        if n["kind"] not in ("struct", "interface", "function", "type_alias", "enum"):
            continue
        file_id = n.get("path", "")
        if not file_id:
            continue
        crate = _crate_for_file(file_id, crates)
        if crate is None:
            continue
        scope = scopes.get(crate.name)
        if scope is None:
            continue
        scope.exports.setdefault(n["name"], n["id"])
    return scopes


def build_file_scope(
    file_id: str,
    import_edges: list[GraphEdge],
    crate_scopes: dict[str, CrateScope],
    crates: list[CrateInfo],
    all_nodes: list[GraphNode],
) -> FileScope:
    """Build a FileScope by resolving this file's `use` declarations."""
    scope = FileScope(file_id=file_id)
    current_crate = _crate_for_file(file_id, crates)

    for e in import_edges:
        if e["kind"] != "import":
            continue
        use_path: str = e["target"]
        alias: str | None = e.get("metadata", {}).get("alias")

        segments = use_path.split("::")
        if not segments:
            continue
        first = segments[0]
        symbol = segments[-1] if len(segments) > 1 else first

        # `use crate_name::Symbol` or `use crate_name::module::Symbol`
        if first in crate_scopes:
            node_id = crate_scopes[first].exports.get(symbol)
            if node_id is None:
                # Try searching all nodes in that crate by name
                crate_prefix = crate_scopes[first].posix_root
                for n in all_nodes:
                    nfile = n.get("path", "")
                    if n["name"] == symbol and (
                        crate_prefix == "" or nfile.startswith(crate_prefix + "/")
                    ):
                        node_id = n["id"]
                        break
            if node_id is not None:
                effective = alias if alias else symbol
                scope.import_map[effective] = node_id

        # `use crate::module::Symbol` — same crate
        elif first == "crate" and current_crate is not None:
            crate_prefix = current_crate.posix_root
            for n in all_nodes:
                nfile = n.get("path", "")
                if n["name"] == symbol and (
                    crate_prefix == "" or nfile.startswith(crate_prefix + "/")
                ):
                    effective = alias if alias else symbol
                    scope.import_map[effective] = n["id"]
                    break

        # `use self::Symbol` or `use super::Symbol`
        elif first in ("self", "super"):
            dir_prefix = "/".join(file_id.rsplit("/", 1)[:-1])
            for n in all_nodes:
                nfile = n.get("path", "")
                ndir = "/".join(nfile.rsplit("/", 1)[:-1])
                if n["name"] == symbol and ndir == dir_prefix:
                    effective = alias if alias else symbol
                    scope.import_map[effective] = n["id"]
                    break

    return scope


# ---------------------------------------------------------------------------
# Call resolution
# ---------------------------------------------------------------------------


def _resolve_call(
    target: str,
    file_scope: FileScope,
    all_nodes: list[GraphNode],
) -> str | None:
    """Resolve a call target name to a node_id.

    Handles:
    - Plain name: ``foo`` → look in import_map then all nodes
    - ``receiver.method``: look for method with matching receiver
    - ``Type::method`` (Rust associated function): resolve Type via import_map,
      then find method node
    - ``scoped::path::fn`` (last segment fallback)
    """
    if "::" in target:
        parts = target.split("::")
        if len(parts) >= 2:
            type_part = parts[-2]
            method_part = parts[-1]
            # Resolve type via import_map
            type_node_id = file_scope.import_map.get(type_part)
            if type_node_id is not None:
                file_prefix = type_node_id.split(":")[0]
                method_id = f"{file_prefix}:{type_part}.{method_part}"
                if any(n["id"] == method_id for n in all_nodes):
                    return method_id
            # Heuristic: search any node with id ending in `:TypePart.methodPart`
            suffix = f":{type_part}.{method_part}"
            for n in all_nodes:
                if n["id"].endswith(suffix):
                    return n["id"]
        # Last segment fallback
        last = parts[-1]
        node_id = file_scope.import_map.get(last)
        if node_id is not None:
            return node_id
        return None

    if "." in target:
        recv, method = target.split(".", 1)
        # Resolve receiver via import_map
        recv_id = file_scope.import_map.get(recv)
        if recv_id is not None:
            file_prefix = recv_id.rsplit(":", 1)[0] if ":" in recv_id else recv_id
            # recv_id might be a type node like "crates/models/src/lib.rs:User"
            method_id = f"{recv_id}.{method}"
            if any(n["id"] == method_id for n in all_nodes):
                return method_id
            # Strip the node kind prefix and try "file:Recv.method"
            suffix = f":{recv}.{method}"
            for n in all_nodes:
                if n["id"].endswith(suffix):
                    return n["id"]
        # Fallback: search by method name
        for n in all_nodes:
            if n["kind"] == "method" and n["name"] == method:
                recv_meta = n.get("metadata", {}).get("receiver", "")
                if recv_meta == recv:
                    return n["id"]
        return None

    # Simple name
    node_id = file_scope.import_map.get(target)
    if node_id is not None:
        return node_id
    # Search directly-exported symbols in same file
    for n in all_nodes:
        if n["name"] == target and n.get("path", "") == file_scope.file_id:
            return n["id"]
    return None


# ---------------------------------------------------------------------------
# Implements / inherit resolution
# ---------------------------------------------------------------------------


def _resolve_name_to_node(
    name: str,
    file_scope: FileScope,
    all_nodes: list[GraphNode],
) -> str | None:
    """Resolve a bare type name to a node_id via import_map or global search."""
    node_id = file_scope.import_map.get(name)
    if node_id is not None:
        return node_id
    # Fallback: first matching node by name (prioritise same-file match)
    same_file: str | None = None
    other: str | None = None
    for n in all_nodes:
        if n["name"] == name:
            if n.get("path", "") == file_scope.file_id:
                same_file = n["id"]
                break
            if other is None:
                other = n["id"]
    return same_file or other


# ---------------------------------------------------------------------------
# resolve_graph
# ---------------------------------------------------------------------------


def resolve_graph(graph: StaticGraph, root: Path) -> StaticGraph:
    """Upgrade unresolved inherit, implements, and call edges."""
    all_nodes: list[GraphNode] = graph["nodes"]
    all_edges: list[GraphEdge] = graph["edges"]

    crates = get_crates(root)
    crate_scopes = build_crate_scopes(all_nodes, crates)

    import_edges_by_file: dict[str, list[GraphEdge]] = {}
    for e in all_edges:
        if e["kind"] == "import":
            import_edges_by_file.setdefault(e["source"], []).append(e)

    resolver_cache: dict[str, FileScope] = {}

    def _scope(fid: str) -> FileScope:
        if fid not in resolver_cache:
            resolver_cache[fid] = build_file_scope(
                fid,
                import_edges_by_file.get(fid, []),
                crate_scopes,
                crates,
                all_nodes,
            )
        return resolver_cache[fid]

    resolved_edges: list[GraphEdge] = []
    for e in all_edges:
        kind = e["kind"]
        if kind not in ("inherit", "implements", "call"):
            resolved_edges.append(e)
            continue
        meta = e.get("metadata", {})
        if meta.get("resolved") is not False:
            resolved_edges.append(e)
            continue

        # Extract file_id from source node (split on first ":")
        source_parts = e["source"].split(":", 1)
        fid = source_parts[0]
        scope = _scope(fid)

        if kind in ("inherit", "implements"):
            node_id = _resolve_name_to_node(e["target"], scope, all_nodes)
        else:
            node_id = _resolve_call(e["target"], scope, all_nodes)

        if node_id is not None:
            # Carry forward edge evidence (e.g. ``line``, ADR-0026); drop the
            # ``resolved`` marker on the now-resolved edge.
            evidence = {k: v for k, v in meta.items() if k != "resolved"}
            resolved_edges.append(
                {
                    "source": e["source"],
                    "target": node_id,
                    "kind": kind,
                    "metadata": evidence,
                }
            )
        else:
            resolved_edges.append(
                {
                    "source": e["source"],
                    "target": e["target"],
                    "kind": kind,
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
