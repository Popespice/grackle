"""HTTP and subprocess hint extraction for Rust source files.

Uses regex on source text. See ADR-0012 for framework coverage.
"""

from __future__ import annotations

import re
from typing import Any

# HTTP clients: reqwest::get, reqwest::Client::get/post/...
_REQWEST_GET_RE = re.compile(r"\breqwest\s*::\s*get\s*\(\s*['\"]([^'\"]+)['\"]")
_REQWEST_CLIENT_RE = re.compile(r"\.(?:get|post|put|delete|patch)\s*\(\s*['\"]([^'\"]+)['\"]")

# HTTP servers: Axum Router::route, Actix-Web .route(...)
_AXUM_ROUTE_RE = re.compile(r"\.route\s*\(\s*['\"]([^'\"]+)['\"]")

# Subprocess: std::process::Command::new("cmd")
_COMMAND_NEW_RE = re.compile(r"Command\s*::\s*new\s*\(\s*['\"]([^'\"]+)['\"]")


def extract_hints(source: str, file_id: str) -> list[dict[str, Any]]:
    """Return hint dicts extracted from *source* attributed to *file_id*."""
    hints: list[dict[str, Any]] = []

    for m in _REQWEST_GET_RE.finditer(source):
        hints.append(
            {
                "kind": "http_client",
                "node_id": file_id,
                "language": "rust",
                "payload": {"path": m.group(1)},
            }
        )

    for m in _REQWEST_CLIENT_RE.finditer(source):
        hints.append(
            {
                "kind": "http_client",
                "node_id": file_id,
                "language": "rust",
                "payload": {"path": m.group(1)},
            }
        )

    for m in _AXUM_ROUTE_RE.finditer(source):
        hints.append(
            {
                "kind": "http_server",
                "node_id": file_id,
                "language": "rust",
                "payload": {"path": m.group(1)},
            }
        )

    for m in _COMMAND_NEW_RE.finditer(source):
        hints.append(
            {
                "kind": "subprocess",
                "node_id": file_id,
                "language": "rust",
                "payload": {"command": m.group(1)},
            }
        )

    return hints
