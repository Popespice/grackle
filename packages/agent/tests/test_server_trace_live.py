"""Tests for grackle WebSocket live-attach mode.

A *producer* connects and sends ``trace_session_start`` /
``trace_event*`` / ``trace_session_end`` messages.  The server buffers
them in a ring-buffer and fans them out to all other connected
*consumers*.

Key invariants:
- Events are NOT echoed back to the producer.
- A consumer connecting *after* the producer has already sent events
  receives the ring-buffer history.
- A producer disconnecting mid-stream does not crash ongoing fan-out.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, Any

import pytest
from websockets.asyncio.client import connect

from grackle.server import serve

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_session_start(session_id: str = "s1") -> str:
    return json.dumps(
        {
            "id": "msg-start",
            "type": "trace_session_start",
            "payload": {"session_id": session_id, "started_ns": 1000, "source": "live"},
        }
    )


def _make_trace_event(i: int) -> str:
    return json.dumps(
        {
            "id": f"msg-ev-{i}",
            "type": "trace_event",
            "payload": {
                "event": "call",
                "node_id": f"script.py:func_{i}",
                "ts_ns": i * 1_000_000,
                "thread_id": 1,
                "frame_depth": i,
                "metadata": {},
            },
        }
    )


def _make_session_end(session_id: str = "s1", count: int = 1) -> str:
    return json.dumps(
        {
            "id": "msg-end",
            "type": "trace_session_end",
            "payload": {"session_id": session_id, "ended_ns": 9_000_000, "event_count": count},
        }
    )


async def _drain_until(ws: Any, *, until_type: str, timeout: float = 5.0) -> list[dict[str, Any]]:
    """Collect messages until a message of *until_type* is received (inclusive)."""
    received: list[dict[str, Any]] = []
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        msg = json.loads(raw)
        received.append(msg)
        if msg["type"] == until_type:
            break
    return received


# ---------------------------------------------------------------------------
# Fixture: bare server (no trace_source → live-attach mode)
# ---------------------------------------------------------------------------


@pytest.fixture
async def live_server(free_port: int, tmp_path: Path) -> AsyncGenerator[int, None]:
    """Server with no trace_source (live-attach mode)."""
    task = asyncio.create_task(serve("127.0.0.1", free_port, root=tmp_path))
    await asyncio.sleep(0.05)
    yield free_port
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


async def test_producer_events_broadcast_to_consumer(live_server: int) -> None:
    """Events pushed by a producer must arrive at a separate consumer."""
    port = live_server

    async with connect(f"ws://127.0.0.1:{port}") as consumer:
        # Consumer may or may not receive a static_graph (empty root → none).
        # Send a ping to confirm the channel is live.
        await consumer.send(json.dumps({"id": "p0", "type": "ping", "payload": {}}))
        pong = json.loads(await asyncio.wait_for(consumer.recv(), timeout=5.0))
        assert pong["type"] == "pong"

        async with connect(f"ws://127.0.0.1:{port}") as producer:
            await producer.send(_make_session_start())
            await producer.send(_make_trace_event(0))
            await producer.send(_make_session_end(count=1))

            # Consumer must receive all three messages in order.
            received = await _drain_until(consumer, until_type="trace_session_end")

    types = [m["type"] for m in received]
    assert "trace_session_start" in types
    assert "trace_event" in types
    assert "trace_session_end" in types
    # Ordering preserved.
    assert types.index("trace_session_start") < types.index("trace_event")
    assert types.index("trace_event") < types.index("trace_session_end")


async def test_events_not_echoed_to_producer(live_server: int) -> None:
    """The producer must NOT receive its own broadcast messages."""
    port = live_server

    async with connect(f"ws://127.0.0.1:{port}") as producer:
        # Flush any static_graph push first.
        await producer.send(json.dumps({"id": "px", "type": "ping", "payload": {}}))
        while True:
            msg = json.loads(await asyncio.wait_for(producer.recv(), timeout=5.0))
            if msg["type"] == "pong":
                break

        await producer.send(_make_session_start())
        await producer.send(_make_trace_event(0))
        await producer.send(_make_session_end(count=1))

        # Give the server a moment to process and (not) echo.
        await asyncio.sleep(0.05)

        # Producer should receive nothing (connection is idle).
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(producer.recv(), timeout=0.2)


async def test_late_consumer_gets_ring_buffer_history(live_server: int) -> None:
    """A consumer connecting after the producer has already sent events gets ring-buffer history."""
    port = live_server

    # Producer sends a session.
    async with connect(f"ws://127.0.0.1:{port}") as producer:
        await producer.send(_make_session_start())
        await producer.send(_make_trace_event(0))
        await producer.send(_make_session_end(count=1))
        # Give the server time to buffer.
        await asyncio.sleep(0.05)

    # Late consumer connects after producer is gone.
    async with connect(f"ws://127.0.0.1:{port}") as late_consumer:
        # Flush any static_graph.
        await late_consumer.send(json.dumps({"id": "lc-ping", "type": "ping", "payload": {}}))

        received: list[dict[str, Any]] = []
        deadline = asyncio.get_event_loop().time() + 2.0
        pong_seen = False
        while not pong_seen:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            raw = await asyncio.wait_for(late_consumer.recv(), timeout=remaining)
            msg = json.loads(raw)
            received.append(msg)
            if msg["type"] == "pong":
                pong_seen = True

    types = {m["type"] for m in received}
    assert "trace_session_start" in types, (
        f"late consumer missing ring-buffer history; got: {types}"
    )
    assert "trace_event" in types
    assert "trace_session_end" in types


async def test_producer_disconnect_doesnt_crash_fanout(live_server: int) -> None:
    """If the producer disconnects mid-stream, the server must remain reachable."""
    port = live_server

    # Consumer establishes first.
    async with connect(f"ws://127.0.0.1:{port}") as consumer:
        await consumer.send(json.dumps({"id": "c0", "type": "ping", "payload": {}}))
        while True:
            msg = json.loads(await asyncio.wait_for(consumer.recv(), timeout=5.0))
            if msg["type"] == "pong":
                break

        # Producer sends one event, then disconnects abruptly.
        async with connect(f"ws://127.0.0.1:{port}") as producer:
            await producer.send(_make_session_start())
            await producer.send(_make_trace_event(0))
            # producer goes out of scope — connection closed.

        # Give server time to clean up the producer connection.
        await asyncio.sleep(0.05)

        # Server must still respond to the consumer's ping.
        await consumer.send(json.dumps({"id": "after-drop", "type": "ping", "payload": {}}))
        while True:
            msg = json.loads(await asyncio.wait_for(consumer.recv(), timeout=5.0))
            if msg["type"] == "pong":
                break

    assert msg["id"] == "after-drop"
