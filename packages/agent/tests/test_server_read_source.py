from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, Any

import pytest
from websockets.asyncio.client import ClientConnection, connect

from grackle.server import serve

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path


@pytest.fixture
async def source_server(free_port: int, tmp_path: Path) -> AsyncGenerator[tuple[int, Path], None]:
    # tmp_path is empty — no Python files, so no static_graph push on connect.
    task = asyncio.create_task(serve("127.0.0.1", free_port, root=tmp_path))
    await asyncio.sleep(0.05)
    yield free_port, tmp_path
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _req(ws: ClientConnection, req_id: str, path: str) -> Any:
    """Send a read_source request and return the correlated reply.

    Skips any leading messages (e.g. an initial static_graph push) that don't
    match the request id.
    """
    await ws.send(json.dumps({"id": req_id, "type": "read_source", "payload": {"path": path}}))
    for _ in range(10):
        raw = await asyncio.wait_for(ws.recv(), timeout=2.0)
        data = json.loads(raw)
        if data.get("id") == req_id:
            return data
    raise AssertionError(f"no reply with id={req_id!r} received")


async def test_read_source_success(source_server: tuple[int, Path]) -> None:
    port, root = source_server
    (root / "hello.py").write_text("x = 42\n", encoding="utf-8")

    async with connect(f"ws://127.0.0.1:{port}") as ws:
        data = await _req(ws, "r1", "hello.py")

    assert data["type"] == "source_response"
    assert data["id"] == "r1"
    assert data["payload"]["source"] == "x = 42\n"
    assert data["payload"]["encoding"] == "utf-8"
    assert data["payload"]["path"] == "hello.py"


async def test_read_source_not_found(source_server: tuple[int, Path]) -> None:
    port, _ = source_server

    async with connect(f"ws://127.0.0.1:{port}") as ws:
        data = await _req(ws, "r2", "ghost.py")

    assert data["type"] == "source_error"
    assert data["id"] == "r2"
    assert data["payload"]["reason"] == "not_found"


async def test_read_source_traversal_guard(source_server: tuple[int, Path]) -> None:
    port, _ = source_server

    async with connect(f"ws://127.0.0.1:{port}") as ws:
        data = await _req(ws, "r3", "../etc/passwd")

    assert data["type"] == "source_error"
    assert data["payload"]["reason"] == "forbidden"


async def test_read_source_double_dot_deep(source_server: tuple[int, Path]) -> None:
    port, root = source_server
    (root / "sub").mkdir()
    (root / "sub" / "real.py").write_text("pass\n", encoding="utf-8")

    async with connect(f"ws://127.0.0.1:{port}") as ws:
        data = await _req(ws, "r4", "sub/../../etc/passwd")

    assert data["type"] == "source_error"
    assert data["payload"]["reason"] == "forbidden"


async def test_read_source_too_large(source_server: tuple[int, Path]) -> None:
    port, root = source_server
    (root / "big.txt").write_bytes(b"x" * (1024 * 1024 + 1))

    async with connect(f"ws://127.0.0.1:{port}") as ws:
        data = await _req(ws, "r5", "big.txt")

    assert data["type"] == "source_error"
    assert data["payload"]["reason"] == "too_large"


async def test_read_source_binary(source_server: tuple[int, Path]) -> None:
    port, root = source_server
    (root / "binary.bin").write_bytes(b"\x80\x81\x82\xff\xfe")

    async with connect(f"ws://127.0.0.1:{port}") as ws:
        data = await _req(ws, "r6", "binary.bin")

    assert data["type"] == "source_error"
    assert data["payload"]["reason"] == "binary"


async def test_read_source_nested_path(source_server: tuple[int, Path]) -> None:
    port, root = source_server
    (root / "pkg").mkdir()
    (root / "pkg" / "mod.py").write_text("y = 1\n", encoding="utf-8")

    async with connect(f"ws://127.0.0.1:{port}") as ws:
        data = await _req(ws, "r7", "pkg/mod.py")

    assert data["type"] == "source_response"
    assert data["payload"]["source"] == "y = 1\n"


async def test_read_source_id_echoed(source_server: tuple[int, Path]) -> None:
    port, root = source_server
    (root / "a.py").write_text("pass\n", encoding="utf-8")

    async with connect(f"ws://127.0.0.1:{port}") as ws:
        data = await _req(ws, "my-correlation-id", "a.py")

    assert data["id"] == "my-correlation-id"
