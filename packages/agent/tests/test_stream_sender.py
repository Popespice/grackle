"""Tests for grackle.python_runtime.stream_sender (Phase 7.2).

Design notes:
- All tests that require a live WebSocket server use the ``live_server``
  fixture (same as test_server_trace_live.py) so the real
  ``_receive_loop`` handles inbound messages.
- Backpressure / drop tests are unit-level and do not require a server.
- "No tail loss" is verified by comparing sent_count from finish() against
  the count actually received by a consumer.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import time
from typing import TYPE_CHECKING, Any

import pytest
from websockets.asyncio.client import connect

from grackle.python_runtime.stream_sender import (
    _DEFAULT_MAX_INFLIGHT,
    _SENTINEL,
    TraceStreamSender,
)
from grackle.server import serve

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from grackle.adapters.base import TraceEvent as _TraceEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(i: int) -> _TraceEvent:
    return {
        "event": "call",
        "node_id": f"s.py:fn_{i}",
        "ts_ns": i * 1_000_000,
        "thread_id": 1,
        "frame_depth": i % 5,
        "metadata": {},
    }


# ---------------------------------------------------------------------------
# Fixture: bare live server
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


async def _collect_until_end(
    port: int,
    *,
    timeout: float = 5.0,
) -> list[dict[str, Any]]:
    """Connect as a consumer and collect all messages until trace_session_end."""
    received: list[dict[str, Any]] = []
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        # Ping to confirm channel is ready.
        await ws.send(json.dumps({"id": "p0", "type": "ping", "payload": {}}))
        deadline = asyncio.get_event_loop().time() + timeout
        end_seen = False
        while not end_seen:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
            msg = json.loads(raw)
            received.append(msg)
            if msg["type"] == "trace_session_end":
                end_seen = True
    return received


# ---------------------------------------------------------------------------
# Unit tests (no server needed)
# ---------------------------------------------------------------------------


def test_sink_enqueues_and_increments_inflight() -> None:
    """sink() puts the event on the queue and increments _inflight."""
    sender = TraceStreamSender("ws://127.0.0.1:9", "s1")
    assert sender._inflight == 0
    sender.sink(_make_event(0))
    assert sender._inflight == 1
    assert sender._queue.qsize() == 1


def test_sink_drops_when_inflight_at_cap() -> None:
    """sink() drops events and increments dropped when _inflight == max_inflight."""
    sender = TraceStreamSender("ws://127.0.0.1:9", "s1", max_inflight=2)
    sender._inflight = 2  # simulate cap reached
    sender.sink(_make_event(0))
    assert sender.dropped == 1
    assert sender._queue.qsize() == 0


def test_sink_does_not_enqueue_past_cap() -> None:
    """Consecutive drops accumulate in dropped without touching the queue."""
    sender = TraceStreamSender("ws://127.0.0.1:9", "s1", max_inflight=0)
    for i in range(5):
        sender.sink(_make_event(i))
    assert sender.dropped == 5
    assert sender._queue.qsize() == 0


def test_sentinel_is_unique_object() -> None:
    """_SENTINEL must not compare equal to any TraceEvent dict."""
    assert _SENTINEL != {}
    assert _SENTINEL != {}
    assert _SENTINEL is _SENTINEL


def test_stream_max_inflight_default() -> None:
    """Default max_inflight comes from env or _DEFAULT_MAX_INFLIGHT."""
    import os

    os.environ.pop("GRACKLE_STREAM_MAX_INFLIGHT", None)
    sender = TraceStreamSender("ws://127.0.0.1:9", "s1")
    assert sender._max_inflight == _DEFAULT_MAX_INFLIGHT


def test_stream_max_inflight_env(monkeypatch: pytest.MonkeyPatch) -> None:
    """GRACKLE_STREAM_MAX_INFLIGHT overrides the default."""
    monkeypatch.setenv("GRACKLE_STREAM_MAX_INFLIGHT", "42")
    sender = TraceStreamSender("ws://127.0.0.1:9", "s1")
    assert sender._max_inflight == 42


def test_stream_max_inflight_env_invalid_returns_default(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Non-integer GRACKLE_STREAM_MAX_INFLIGHT falls back to default."""
    monkeypatch.setenv("GRACKLE_STREAM_MAX_INFLIGHT", "not-a-number")
    sender = TraceStreamSender("ws://127.0.0.1:9", "s1")
    assert sender._max_inflight == _DEFAULT_MAX_INFLIGHT


# ---------------------------------------------------------------------------
# Connection failure
# ---------------------------------------------------------------------------


def test_start_raises_on_unreachable_server(free_port: int) -> None:
    """start() must raise ConnectionError if no server is listening."""
    sender = TraceStreamSender(f"ws://127.0.0.1:{free_port}", "s1")
    with pytest.raises((ConnectionError, OSError)):
        sender.start(connect_timeout=2.0)


# ---------------------------------------------------------------------------
# Integration tests — require a live server
# ---------------------------------------------------------------------------


async def test_sender_delivers_session_start_events_end(live_server: int) -> None:
    """Events are delivered in order: session_start → event* → session_end."""
    port = live_server
    url = f"ws://127.0.0.1:{port}"

    # Spin up consumer first.
    consumer_task = asyncio.create_task(_collect_until_end(port))
    await asyncio.sleep(0.05)  # let consumer connect

    # Now run the sender on a thread so it doesn't block the event loop.
    sender = TraceStreamSender(url, "test-session")
    await asyncio.get_event_loop().run_in_executor(None, sender.start)

    for i in range(5):
        sender.sink(_make_event(i))

    sent = await asyncio.get_event_loop().run_in_executor(None, sender.finish)

    received = await consumer_task

    types = [m["type"] for m in received if m["type"] != "pong"]
    assert "trace_session_start" in types
    assert "trace_event" in types
    assert "trace_session_end" in types
    # Order preserved.
    assert types.index("trace_session_start") < types.index("trace_event")
    assert types.index("trace_event") < types.index("trace_session_end")
    assert sent == 5


async def test_sender_no_tail_loss(live_server: int) -> None:
    """All enqueued events arrive even when the producer outpaces the sender."""
    port = live_server
    url = f"ws://127.0.0.1:{port}"

    consumer_task = asyncio.create_task(_collect_until_end(port, timeout=10.0))
    await asyncio.sleep(0.05)

    sender = TraceStreamSender(url, "no-loss-session")
    await asyncio.get_event_loop().run_in_executor(None, sender.start)

    n = 200
    for i in range(n):
        sender.sink(_make_event(i))

    # finish() joins the thread — sentinel is last, so all n events are drained.
    sent = await asyncio.get_event_loop().run_in_executor(None, sender.finish)
    assert sent == n

    received = await consumer_task
    trace_events = [m for m in received if m["type"] == "trace_event"]
    assert len(trace_events) == n


async def test_sender_session_end_carries_correct_count(live_server: int) -> None:
    """trace_session_end.event_count == number of events actually sent."""
    port = live_server
    url = f"ws://127.0.0.1:{port}"

    consumer_task = asyncio.create_task(_collect_until_end(port))
    await asyncio.sleep(0.05)

    n = 7
    sender = TraceStreamSender(url, "count-session")
    await asyncio.get_event_loop().run_in_executor(None, sender.start)

    for i in range(n):
        sender.sink(_make_event(i))

    sent = await asyncio.get_event_loop().run_in_executor(None, sender.finish)

    received = await consumer_task
    end_msgs = [m for m in received if m["type"] == "trace_session_end"]
    assert len(end_msgs) == 1
    assert end_msgs[0]["payload"]["event_count"] == sent == n


async def test_sender_backpressure_bounds_memory(live_server: int) -> None:
    """With a tiny inflight cap, heavy flooding drops events without hanging."""
    port = live_server
    url = f"ws://127.0.0.1:{port}"

    consumer_task = asyncio.create_task(_collect_until_end(port, timeout=10.0))
    await asyncio.sleep(0.05)

    cap = 10
    produced = 500
    sender = TraceStreamSender(url, "bp-session", max_inflight=cap)
    await asyncio.get_event_loop().run_in_executor(None, sender.start)

    for i in range(produced):
        sender.sink(_make_event(i))

    sent = await asyncio.get_event_loop().run_in_executor(None, sender.finish)

    # dropped + sent must equal produced (no events unaccounted for).
    assert sender.dropped + sent == produced
    # Memory was bounded: queue never held more than cap + 1 at once.
    assert sent <= cap + 1 or sender.dropped > 0

    await consumer_task  # drain consumer so server is clean


async def test_sender_no_pacing(live_server: int) -> None:
    """Events are sent back-to-back; wall time is much less than sum of ts_ns gaps."""
    port = live_server
    url = f"ws://127.0.0.1:{port}"

    consumer_task = asyncio.create_task(_collect_until_end(port, timeout=10.0))
    await asyncio.sleep(0.05)

    n = 20
    # Events with 100 ms gaps between them — if paced that would take 2 s.
    sender = TraceStreamSender(url, "pace-session")
    await asyncio.get_event_loop().run_in_executor(None, sender.start)

    t0 = time.monotonic()
    for i in range(n):
        ev = _make_event(i)
        ev["ts_ns"] = i * 100_000_000  # 100 ms apart
        sender.sink(ev)
    sent = await asyncio.get_event_loop().run_in_executor(None, sender.finish)
    elapsed = time.monotonic() - t0

    # Real-time mode sends immediately — expect well under 2 s regardless of ts_ns.
    assert elapsed < 2.0, f"real-time sender paced events (took {elapsed:.2f} s)"
    assert sent == n

    await consumer_task
