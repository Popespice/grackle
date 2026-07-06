"""HTTP and subprocess hint extraction for Python source files.

Uses regex on source text — deliberately shallow, not a full AST pass.
See ADR-0012 for the framework allow-list and known limitations.
"""

from __future__ import annotations

import re
from typing import Any

# HTTP clients: requests / httpx
_HTTP_CLIENT_RE = re.compile(
    r"(?:requests|httpx)\s*\.\s*(?:get|post|put|delete|patch|request)\s*\(\s*['\"]([^'\"]+)['\"]"
)
# urllib
_URLLIB_RE = re.compile(r"urllib\.request\.urlopen\s*\(\s*['\"]([^'\"]+)['\"]")

# HTTP servers: Flask @app.route, FastAPI @app.get/post/..., Django path()
_FLASK_ROUTE_RE = re.compile(r"@\w+\.route\s*\(\s*['\"]([^'\"]+)['\"]")
_FASTAPI_RE = re.compile(r"@\w+\.(?:get|post|put|delete|patch)\s*\(\s*['\"]([^'\"]+)['\"]")
_DJANGO_PATH_RE = re.compile(r"\bpath\s*\(\s*['\"]([^'\"]+)['\"]")

# Subprocess: subprocess.run/Popen/call([cmd, ...]) and os.system('cmd')
_SUBPROCESS_LIST_RE = re.compile(r"subprocess\.(?:run|Popen|call)\s*\(\s*\[([^\]]+)\]")
_OS_SYSTEM_RE = re.compile(r"os\.system\s*\(\s*['\"]([^'\"]+)['\"]")


def extract_hints(source: str, file_id: str) -> list[dict[str, Any]]:
    """Return hint dicts extracted from *source* attributed to *file_id*.

    Each hint's ``payload`` carries the 1-based ``line`` of the matched
    construct (edge evidence, ADR-0026), derived from the regex match offset.
    """
    hints: list[dict[str, Any]] = []

    def _line(m: re.Match[str]) -> int:
        return source.count("\n", 0, m.start()) + 1

    for m in _HTTP_CLIENT_RE.finditer(source):
        hints.append(
            {
                "kind": "http_client",
                "node_id": file_id,
                "language": "python",
                "payload": {"path": m.group(1), "line": _line(m)},
            }
        )

    for m in _URLLIB_RE.finditer(source):
        hints.append(
            {
                "kind": "http_client",
                "node_id": file_id,
                "language": "python",
                "payload": {"path": m.group(1), "line": _line(m)},
            }
        )

    for m in _FLASK_ROUTE_RE.finditer(source):
        hints.append(
            {
                "kind": "http_server",
                "node_id": file_id,
                "language": "python",
                "payload": {"path": m.group(1), "line": _line(m)},
            }
        )

    for m in _FASTAPI_RE.finditer(source):
        hints.append(
            {
                "kind": "http_server",
                "node_id": file_id,
                "language": "python",
                "payload": {"path": m.group(1), "line": _line(m)},
            }
        )

    for m in _DJANGO_PATH_RE.finditer(source):
        hints.append(
            {
                "kind": "http_server",
                "node_id": file_id,
                "language": "python",
                "payload": {"path": m.group(1), "line": _line(m)},
            }
        )

    for m in _SUBPROCESS_LIST_RE.finditer(source):
        args = re.findall(r"['\"]([^'\"]+)['\"]", m.group(1))
        if args:
            hints.append(
                {
                    "kind": "subprocess",
                    "node_id": file_id,
                    "language": "python",
                    "payload": {"command": args[0], "line": _line(m)},
                }
            )

    for m in _OS_SYSTEM_RE.finditer(source):
        hints.append(
            {
                "kind": "subprocess",
                "node_id": file_id,
                "language": "python",
                "payload": {"command": m.group(1), "line": _line(m)},
            }
        )

    return hints
