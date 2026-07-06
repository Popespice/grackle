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
            metadata: dict[str, Any] = {"http_path": path, "resolved": True}
            # Edge evidence (ADR-0026): the client call-site line, when the
            # hint carries one (absent on stale-cache hints — degrades cleanly).
            line = hint.get("payload", {}).get("line")
            if isinstance(line, int):
                metadata["line"] = line
            edges.append(
                {
                    "source": hint["node_id"],
                    "target": target_id,
                    "kind": "cross_language_call",
                    "metadata": metadata,
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
                    spawn_metadata: dict[str, Any] = {"command": cmd, "resolved": True}
                    # Edge evidence (ADR-0026): the spawn call-site line, when
                    # present (absent on stale-cache hints — degrades cleanly).
                    spawn_line = hint.get("payload", {}).get("line")
                    if isinstance(spawn_line, int):
                        spawn_metadata["line"] = spawn_line
                    edges.append(
                        {
                            "source": hint["node_id"],
                            "target": file_id,
                            "kind": "cross_language_spawn",
                            "metadata": spawn_metadata,
                        }
                    )
                break

    return edges
