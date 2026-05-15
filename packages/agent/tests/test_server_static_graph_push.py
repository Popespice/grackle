from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from websockets.asyncio.client import connect

from grackle.server import serve

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

_TINY_APP = Path(__file__).parent.parent.parent.parent / "fixtures" / "tiny-app"


@pytest.fixture
async def tiny_app_server(free_port: int) -> AsyncGenerator[int, None]:
    task = asyncio.create_task(serve("127.0.0.1", free_port, root=_TINY_APP))
    await asyncio.sleep(0.05)
    yield free_port
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_static_graph_pushed_on_connect(tiny_app_server: int) -> None:
    async with connect(f"ws://127.0.0.1:{tiny_app_server}") as ws:
        raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
        data = json.loads(raw)

    assert data["type"] == "static_graph"
    assert isinstance(data["id"], str) and data["id"]
    payload = data["payload"]
    assert payload["language"] == "python"
    assert len(payload["nodes"]) == 25
    assert len(payload["edges"]) == 42


async def test_static_graph_has_posix_paths(tiny_app_server: int) -> None:
    async with connect(f"ws://127.0.0.1:{tiny_app_server}") as ws:
        raw = await asyncio.wait_for(ws.recv(), timeout=10.0)
        data = json.loads(raw)

    nodes = data["payload"]["nodes"]
    for node in nodes:
        assert "\\" not in node["id"], f"backslash in node id: {node['id']}"
        assert "\\" not in node["path"], f"backslash in node path: {node['path']}"


async def test_ping_still_works_after_graph_push(tiny_app_server: int) -> None:
    async with connect(f"ws://127.0.0.1:{tiny_app_server}") as ws:
        # Consume the static_graph push
        first = json.loads(await asyncio.wait_for(ws.recv(), timeout=10.0))
        assert first["type"] == "static_graph"

        # Ping should still work
        await ws.send(json.dumps({"id": "ping1", "type": "ping", "payload": {}}))
        reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=2.0))

    assert reply["type"] == "pong"
    assert reply["id"] == "ping1"


async def test_no_graph_for_empty_root(free_port: int, tmp_path: Path) -> None:
    task = asyncio.create_task(serve("127.0.0.1", free_port, root=tmp_path))
    await asyncio.sleep(0.05)
    try:
        async with connect(f"ws://127.0.0.1:{free_port}") as ws:
            await ws.send(json.dumps({"id": "probe", "type": "ping", "payload": {}}))
            raw = await asyncio.wait_for(ws.recv(), timeout=1.0)
            data = json.loads(raw)
        assert data["type"] == "pong", "expected pong as first message for empty root"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
