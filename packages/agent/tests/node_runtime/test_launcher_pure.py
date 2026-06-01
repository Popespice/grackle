"""Node-free tests for the launcher's pure helpers (ADR-0022).

These exercise launcher logic that does not spawn Node: JS error-type extraction
and the disk-backed offset→line map (CRLF / BOM handling).
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from grackle.node_runtime import launcher
from grackle.node_runtime.launcher import _NodeSession
from grackle.node_runtime.node_resolution import NodeResolver

if TYPE_CHECKING:
    import asyncio
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


def test_exception_event_uses_given_timestamp(tmp_path: Path) -> None:
    # Finding #1: the exception event must carry the caller-supplied ts_ns, never a
    # hard-coded 0 (which would mis-sort it to the front of a coverage stream and
    # inflate the reconstructed call-tree's total duration).
    resolver = NodeResolver(tmp_path, _file_graph("app.ts"))
    script = tmp_path / "app.ts"
    event = launcher._exception_event(resolver, script, tmp_path, "TypeError: boom", 4242)
    assert event["event"] == "exception"
    assert event["ts_ns"] == 4242
    metadata = event["metadata"]
    assert isinstance(metadata, dict)
    assert metadata["exc_type"] == "TypeError"


class _ChunkStderr:
    """Async stream that hands back pre-split byte chunks, then EOF."""

    def __init__(self, chunks: list[bytes]) -> None:
        self._chunks = chunks

    async def read(self, _n: int) -> bytes:
        return self._chunks.pop(0) if self._chunks else b""


class _FakeProc:
    def __init__(self, stderr: object) -> None:
        self.stderr = stderr
        self.stdout = None
        self.returncode = 0


async def test_read_stderr_handles_multibyte_split_across_chunks() -> None:
    # Finding #7: a multi-byte UTF-8 sequence straddling a read-chunk boundary must
    # decode intact. Here 'é' is 0xC3 0xA9; we split between the two bytes so a
    # per-chunk decode would emit replacement characters instead of 'é'.
    text = "héllo wörld"
    raw = (text + "\n").encode("utf-8")
    split = raw.index(b"\xa9")  # lead byte 0xC3 in chunk 1, trail byte 0xA9 in chunk 2
    proc = _FakeProc(_ChunkStderr([raw[:split], raw[split:]]))
    session = _NodeSession(cast("asyncio.subprocess.Process", proc))
    await session._read_stderr()
    # The line is reconstructed intact in the diagnostic tail (no replacement char).
    assert text in session._stderr_tail
