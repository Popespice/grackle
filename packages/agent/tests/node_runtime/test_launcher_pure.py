"""Node-free tests for the launcher's pure helpers (ADR-0022).

These exercise launcher logic that does not spawn Node: JS error-type extraction
and the disk-backed offset→line map (CRLF / BOM handling).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from grackle.node_runtime import launcher
from grackle.node_runtime.node_resolution import NodeResolver

if TYPE_CHECKING:
    from pathlib import Path

    from grackle.adapters.base import StaticGraph


def test_js_error_type_extracts_class() -> None:
    assert launcher._js_error_type("TypeError: x is not a function at file") == "TypeError"
    assert launcher._js_error_type("RangeError: invalid array length") == "RangeError"
    assert launcher._js_error_type("MyCustomError: boom") == "MyCustomError"


def test_js_error_type_falls_back_to_error() -> None:
    # Thrown non-Error string (no "<Name>Error:" prefix) → generic "Error".
    assert launcher._js_error_type("boom") == "Error"
    assert launcher._js_error_type("not an error: just text") == "Error"
    assert launcher._js_error_type("") == "Error"


def _file_graph(path: str) -> StaticGraph:
    return {
        "version": 1,
        "language": "typescript",
        "nodes": [{"id": path, "kind": "file", "name": path, "path": path}],
        "edges": [],
    }


def test_line_map_preserves_crlf_and_strips_bom(tmp_path: Path) -> None:
    resolver = NodeResolver(tmp_path, _file_graph("a.ts"))
    # BOM + CRLF source: V8's offsets count the \r and exclude the BOM, so the
    # map must be built from CRLF-preserving, BOM-stripped bytes.
    (tmp_path / "a.ts").write_bytes("﻿line1\r\nline2\r\nline3\r\n".encode())
    url = (tmp_path / "a.ts").as_uri()

    line_map = launcher._line_map_for_url(resolver, {}, url)
    assert line_map is not None
    assert line_map.line_of(0) == 1
    assert line_map.line_of(7) == 2  # start of line 2 in the \r\n-preserving source
    # The CR ending line 2 is at offset 12; it is still line 2. If CRLF were
    # collapsed to LF (the bug), this offset would resolve to line 3.
    assert line_map.line_of(12) == 2
    assert line_map.line_of(14) == 3


def test_line_map_none_for_non_project_url(tmp_path: Path) -> None:
    resolver = NodeResolver(tmp_path, _file_graph("a.ts"))
    assert launcher._line_map_for_url(resolver, {}, "node:internal/x") is None
