"""Cross-language edge resolution.

Receives ``CrossLanguageHint`` objects accumulated by per-language adapters
during parsing and resolves them into ``cross_language_call`` /
``cross_language_spawn`` edges via path normalisation.

See ADR-0012 for design rationale, framework coverage, and known limitations.
"""

from __future__ import annotations

import re
from typing import Any

# Collapse common URL path parameter styles to a canonical token:
#   {id}  :id  <id>  → {param}
_PARAM_RE = re.compile(r"\{[^}]+\}|:[a-zA-Z_][a-zA-Z0-9_]*|<[a-zA-Z_][a-zA-Z0-9_]*>")


def normalize_http_path(path: str) -> str:
    """Normalise a URL path for client↔server matching.

    Rules:
    - Strip surrounding whitespace
    - Lowercase
    - Trim trailing slash (treat /users/ == /users)
    - Collapse parameter patterns ({id}, :id, <id>) to {param}
    """
    p = path.strip().lower().rstrip("/")
    return _PARAM_RE.sub("{param}", p)


def _edge_metadata(hint: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    """Attach the hint's payload ``line`` (edge evidence, ADR-0026) to *base* when
    present. A stale-cache hint lacking a line degrades cleanly (no ``line`` key)."""
    line = hint.get("payload", {}).get("line")
    if isinstance(line, int):
        base["line"] = line
    return base


def resolve_cross_language_edges(
    hints: list[dict[str, Any]],
    graph_nodes: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Match HTTP client↔server hints and subprocess↔file hints.

    Only emits edges where both sides resolve to known graph node IDs and the
    HTTP path has ≥2 segments (to suppress trivial ``/`` or ``/health`` noise).

    Returns a list of GraphEdge dicts with kind ``cross_language_call`` or
    ``cross_language_spawn``.
    """
    edges: list[dict[str, Any]] = []

    clients = [h for h in hints if h.get("kind") == "http_client"]
    servers = [h for h in hints if h.get("kind") == "http_server"]
    spawns = [h for h in hints if h.get("kind") == "subprocess"]

    # Index file nodes by their POSIX path ID
    file_nodes = {n["id"] for n in graph_nodes if n.get("kind") == "file"}

    # Build normalized-path → server node_id mapping.
    # Only include paths with ≥2 non-empty segments to reduce noise.
    server_map: dict[str, str] = {}
    for hint in servers:
        path = hint.get("payload", {}).get("path", "")
        if not path:
            continue
        norm = normalize_http_path(path)
        segments = [s for s in norm.split("/") if s]
        if len(segments) >= 2:  # noqa: PLR2004
            server_map[norm] = hint["node_id"]

    for hint in clients:
        path = hint.get("payload", {}).get("path", "")
        if not path:
            continue
        norm = normalize_http_path(path)
        target_id = server_map.get(norm)
        if target_id and hint["node_id"] != target_id:
            # Edge evidence (ADR-0026): the client call-site line rides in
            # metadata when the hint carries one.
            edges.append(
                {
                    "source": hint["node_id"],
                    "target": target_id,
                    "kind": "cross_language_call",
                    "metadata": _edge_metadata(hint, {"http_path": path, "resolved": True}),
                }
            )

    # Subprocess: match argv[0] against file node IDs by suffix
    for hint in spawns:
        cmd = hint.get("payload", {}).get("command", "")
        if not cmd:
            continue
        # Normalise: strip leading ./ and convert backslashes
        cmd_norm = cmd.lstrip("./").replace("\\", "/")
        for file_id in file_nodes:
            if file_id == cmd_norm or file_id.endswith("/" + cmd_norm):
                if hint["node_id"] != file_id:
                    # Edge evidence (ADR-0026): the spawn call-site line.
                    edges.append(
                        {
                            "source": hint["node_id"],
                            "target": file_id,
                            "kind": "cross_language_spawn",
                            "metadata": _edge_metadata(hint, {"command": cmd, "resolved": True}),
                        }
                    )
                break

    return edges
