"""HTTP and subprocess hint extraction for TypeScript/TSX source files.

Uses regex on source text. See ADR-0012 for framework coverage.
"""

from __future__ import annotations

import re
from typing import Any

# HTTP clients: fetch, axios
_FETCH_RE = re.compile(r"\bfetch\s*\(\s*['\"]([^'\"]+)['\"]")
_AXIOS_RE = re.compile(
    r"\baxios\s*\.\s*(?:get|post|put|delete|patch|request)\s*\(\s*['\"]([^'\"]+)['\"]"
)

# HTTP servers: Express / Fastify / Hono app.get/post/...  router.get/post/...
_EXPRESS_RE = re.compile(
    r"(?:app|router)\s*\.\s*(?:get|post|put|delete|patch)\s*\(\s*['\"]([^'\"]+)['\"]"
)

# Subprocess: child_process exec/spawn/fork, execa
_CHILD_PROC_RE = re.compile(r"(?:exec|spawn|fork)\s*\(\s*['\"]([^'\"]+)['\"]")
_EXECA_RE = re.compile(r"\bexeca\s*\(\s*['\"]([^'\"]+)['\"]")


def extract_hints(source: str, file_id: str) -> list[dict[str, Any]]:
    """Return hint dicts extracted from *source* attributed to *file_id*."""
    hints: list[dict[str, Any]] = []

    for m in _FETCH_RE.finditer(source):
        hints.append(
            {
                "kind": "http_client",
                "node_id": file_id,
                "language": "typescript",
                "payload": {"path": m.group(1)},
            }
        )

    for m in _AXIOS_RE.finditer(source):
        hints.append(
            {
                "kind": "http_client",
                "node_id": file_id,
                "language": "typescript",
                "payload": {"path": m.group(1)},
            }
        )

    for m in _EXPRESS_RE.finditer(source):
        hints.append(
            {
                "kind": "http_server",
                "node_id": file_id,
                "language": "typescript",
                "payload": {"path": m.group(1)},
            }
        )

    for m in _CHILD_PROC_RE.finditer(source):
        hints.append(
            {
                "kind": "subprocess",
                "node_id": file_id,
                "language": "typescript",
                "payload": {"command": m.group(1)},
            }
        )

    for m in _EXECA_RE.finditer(source):
        hints.append(
            {
                "kind": "subprocess",
                "node_id": file_id,
                "language": "typescript",
                "payload": {"command": m.group(1)},
            }
        )

    return hints
