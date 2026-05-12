import asyncio
import contextlib
import json
from collections.abc import AsyncGenerator

import pytest
from websockets.asyncio.client import connect

from grackle.server import serve


@pytest.fixture
async def agent_server(free_port: int) -> AsyncGenerator[int, None]:
    task = asyncio.create_task(serve("127.0.0.1", free_port))
    await asyncio.sleep(0.05)  # let the server bind and start listening
    yield free_port
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_ping_returns_pong(agent_server: int) -> None:
    async with connect(f"ws://127.0.0.1:{agent_server}") as ws:
        await ws.send(json.dumps({"id": "t1", "type": "ping", "payload": {}}))
        reply = await ws.recv()
        data = json.loads(reply)
    assert data["type"] == "pong"
    assert data["id"] == "t1"
    assert data["payload"]["ping_id"] == "t1"


async def test_unknown_type_receives_no_reply(agent_server: int) -> None:
    async with connect(f"ws://127.0.0.1:{agent_server}") as ws:
        await ws.send(json.dumps({"id": "t2", "type": "future-unknown-type", "payload": {}}))
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(ws.recv(), timeout=0.1)
            pytest.fail("expected no reply to unknown message type")


async def test_malformed_json_receives_no_reply(agent_server: int) -> None:
    async with connect(f"ws://127.0.0.1:{agent_server}") as ws:
        await ws.send("not valid json at all")
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(ws.recv(), timeout=0.1)
            pytest.fail("expected no reply to malformed JSON")


async def test_missing_required_field_receives_no_reply(agent_server: int) -> None:
    async with connect(f"ws://127.0.0.1:{agent_server}") as ws:
        await ws.send(json.dumps({"type": "ping"}))  # missing id and payload
        with contextlib.suppress(TimeoutError):
            await asyncio.wait_for(ws.recv(), timeout=0.1)
            pytest.fail("expected no reply to invalid envelope")


async def test_multiple_pings(agent_server: int) -> None:
    async with connect(f"ws://127.0.0.1:{agent_server}") as ws:
        for i in range(3):
            await ws.send(json.dumps({"id": f"ping-{i}", "type": "ping", "payload": {}}))
        for i in range(3):
            data = json.loads(await ws.recv())
            assert data["type"] == "pong"
            assert data["id"] == f"ping-{i}"
