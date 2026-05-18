"""HTTP and subprocess hint extraction for Go source files.

Uses regex on source text. See ADR-0012 for framework coverage.
"""

from __future__ import annotations

import re
from typing import Any

# HTTP clients: stdlib http.Get/Post/..., http.NewRequest
_HTTP_CLIENT_RE = re.compile(
    r"\bhttp\s*\.\s*(?:Get|Post|Put|Delete|Patch|NewRequest)\s*\(\s*['\"]([^'\"]+)['\"]"
)

# HTTP servers: http.HandleFunc, mux.HandleFunc, r.HandleFunc (gorilla/mux)
_HANDLE_FUNC_RE = re.compile(r"(?:\w+)\s*\.\s*HandleFunc\s*\(\s*['\"]([^'\"]+)['\"]")
# http.Handle
_HTTP_HANDLE_RE = re.compile(r"\bhttp\s*\.\s*Handle\s*\(\s*['\"]([^'\"]+)['\"]")

# Subprocess: exec.Command("cmd", ...)
_EXEC_COMMAND_RE = re.compile(r"\bexec\s*\.\s*Command\s*\(\s*['\"]([^'\"]+)['\"]")


def extract_hints(source: str, file_id: str) -> list[dict[str, Any]]:
    """Return hint dicts extracted from *source* attributed to *file_id*."""
    hints: list[dict[str, Any]] = []

    for m in _HTTP_CLIENT_RE.finditer(source):
        hints.append(
            {
                "kind": "http_client",
                "node_id": file_id,
                "language": "go",
                "payload": {"path": m.group(1)},
            }
        )

    for m in _HANDLE_FUNC_RE.finditer(source):
        hints.append(
            {
                "kind": "http_server",
                "node_id": file_id,
                "language": "go",
                "payload": {"path": m.group(1)},
            }
        )

    for m in _HTTP_HANDLE_RE.finditer(source):
        hints.append(
            {
                "kind": "http_server",
                "node_id": file_id,
                "language": "go",
                "payload": {"path": m.group(1)},
            }
        )

    for m in _EXEC_COMMAND_RE.finditer(source):
        hints.append(
            {
                "kind": "subprocess",
                "node_id": file_id,
                "language": "go",
                "payload": {"command": m.group(1)},
            }
        )

    return hints
