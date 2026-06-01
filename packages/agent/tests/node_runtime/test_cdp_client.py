"""Tests for the minimal CDP client message routing (ADR-0022).

A fake WebSocket lets us exercise request/response correlation and event dispatch
without a real inspector socket.
"""

from __future__ import annotations

import asyncio
import json

import pytest

from grackle.node_runtime.cdp_client import CDPClient, CDPError


class _FakeWS:
    """Records outbound frames; never delivers inbound (tests drive `_dispatch`)."""

    def __init__(self) -> None:
        self.sent: list[str] = []

    async def send(self, data: str) -> None:
        self.sent.append(data)


class _FailWS:
    async def send(self, data: str) -> None:
        raise ConnectionError("socket gone")


async def test_send_resolves_on_matching_response() -> None:
    ws = _FakeWS()
    client = CDPClient(ws)
    task = asyncio.ensure_future(client.send("Profiler.stop", {"x": 1}))
    await asyncio.sleep(0)  # let send() register the pending future and write

    assert len(ws.sent) == 1
    sent = json.loads(ws.sent[0])
    assert sent["method"] == "Profiler.stop"
    assert sent["params"] == {"x": 1}

    client._dispatch(json.dumps({"id": sent["id"], "result": {"profile": {"ok": True}}}))
    assert await task == {"profile": {"ok": True}}


async def test_send_raises_on_error_response() -> None:
    ws = _FakeWS()
    client = CDPClient(ws)
    task = asyncio.ensure_future(client.send("Bad.method"))
    await asyncio.sleep(0)
    sent = json.loads(ws.sent[0])
    client._dispatch(json.dumps({"id": sent["id"], "error": {"code": -32000, "message": "no"}}))
    with pytest.raises(CDPError):
        await task


async def test_send_failure_cleans_pending() -> None:
    client = CDPClient(_FailWS())
    with pytest.raises(CDPError):
        await client.send("Profiler.start")
    assert client._pending == {}


async def test_send_timeout_raises_and_cleans_pending() -> None:
    # No response ever arrives (the fake ws only records sends) → the bounded
    # send must raise CDPError and not leak the pending future.
    client = CDPClient(_FakeWS())
    with pytest.raises(CDPError):
        await client.send("Profiler.stop", timeout=0.01)
    assert client._pending == {}


async def test_default_timeout_bounds_send_without_explicit_timeout() -> None:
    # Finding #11: the attach-phase commands (Runtime.enable / Profiler.* /
    # runIfWaitingForDebugger) pass no explicit timeout. A client default_timeout
    # must still bound them so a half-open socket cannot hang `await future` forever.
    client = CDPClient(_FakeWS(), default_timeout=0.01)
    with pytest.raises(CDPError):
        await client.send("Runtime.enable")  # no response ever arrives
    assert client._pending == {}


async def test_malformed_json_ignored() -> None:
    client = CDPClient(_FakeWS())
    client._dispatch("this is not json")  # must not raise


async def test_fail_pending_unblocks_awaiters() -> None:
    client = CDPClient(_FakeWS())
    loop = asyncio.get_running_loop()
    future: asyncio.Future[dict[str, object]] = loop.create_future()
    client._pending[1] = future
    client._fail_pending(CDPError("closed"))
    with pytest.raises(CDPError):
        await future
    assert client._pending == {}
