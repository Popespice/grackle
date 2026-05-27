"""Integration tests for server-side trace seek (Phase 7.3).

All tests use a real grackle server via the ``live_server_with_trace`` fixture,
which starts a server with a pre-built JSONL trace file.  The ``free_port``
fixture (conftest.py) provides a collision-free port.

Key scenarios:
- ``trace_session_start`` includes ``seekable: true`` for file-replay mode.
- ``trace_seek_request`` → ``trace_window`` with the correct slice + total,
  echoing the request ``id``.
- Unknown/wrong session_id → ``trace_seek_error``.
- Out-of-range start_index returns a partial (clamped) window, not an error.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, Any, cast

import pytest
from websockets.asyncio.client import connect

from grackle.server import serve

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator
    from pathlib import Path


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_event(i: int) -> dict[str, Any]:
    return {
        "event": "call",
        "node_id": f"s.py:fn_{i}",
        "ts_ns": i * 1_000_000,
        "thread_id": 1,
        "frame_depth": i % 5,
        "metadata": {},
    }


def _write_trace(path: Path, n: int) -> None:
    """Write n events to a JSONL trace file."""
    lines = [json.dumps(_make_event(i)) for i in range(n)]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


async def _recv_until(ws: Any, type_: str, timeout: float = 5.0) -> dict[str, Any]:
    """Receive messages until one with the given type is found."""
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError(f"timed out waiting for message type {type_!r}")
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        msg = cast("dict[str, Any]", json.loads(raw))
        if msg["type"] == type_:
            return msg


async def _send_seek(
    ws: Any,
    session_id: str,
    start_index: int,
    count: int,
    *,
    req_id: str | None = None,
) -> dict[str, Any]:
    """Send a trace_seek_request and return the next trace_window or trace_seek_error."""
    if req_id is None:
        import uuid

        req_id = str(uuid.uuid4())
    await ws.send(
        json.dumps(
            {
                "id": req_id,
                "type": "trace_seek_request",
                "payload": {
                    "session_id": session_id,
                    "start_index": start_index,
                    "count": count,
                },
            }
        )
    )
    # Server replies with trace_window or trace_seek_error, echoing the id.
    deadline = asyncio.get_event_loop().time() + 5.0
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            raise TimeoutError("timed out waiting for seek reply")
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        reply = cast("dict[str, Any]", json.loads(raw))
        if reply["type"] in ("trace_window", "trace_seek_error"):
            return reply


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


@pytest.fixture
async def live_server_with_trace(
    free_port: int, tmp_path: Path
) -> AsyncGenerator[tuple[int, Path], None]:
    """Start a grackle server with a 10-event JSONL trace file.

    Yields (port, trace_path).  pace=False so the replay finishes instantly
    in tests.
    """
    trace_path = tmp_path / "trace.jsonl"
    _write_trace(trace_path, 10)
    task = asyncio.create_task(
        serve(
            "127.0.0.1",
            free_port,
            root=tmp_path,
            trace_source=trace_path,
            pace=False,
        )
    )
    await asyncio.sleep(0.05)
    yield free_port, trace_path
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


# ---------------------------------------------------------------------------
# trace_session_start → seekable: true
# ---------------------------------------------------------------------------


async def test_session_start_is_seekable(
    live_server_with_trace: tuple[int, Path],
) -> None:
    """trace_session_start must include seekable: true in file-replay mode."""
    port, _ = live_server_with_trace
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        msg = await _recv_until(ws, "trace_session_start")
        assert msg["payload"].get("seekable") is True, (
            f"expected seekable=true in trace_session_start payload, got: {msg['payload']}"
        )


async def test_session_start_has_session_id(
    live_server_with_trace: tuple[int, Path],
) -> None:
    """trace_session_start payload must contain a session_id string."""
    port, _ = live_server_with_trace
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        msg = await _recv_until(ws, "trace_session_start")
        sid = msg["payload"].get("session_id")
        assert isinstance(sid, str) and len(sid) > 0


async def test_session_id_stable_across_connections(
    live_server_with_trace: tuple[int, Path],
) -> None:
    """The same session_id must be used for all connections in file-replay mode."""
    port, _ = live_server_with_trace
    async with connect(f"ws://127.0.0.1:{port}") as ws1:
        msg1 = await _recv_until(ws1, "trace_session_start")
        sid1 = msg1["payload"]["session_id"]

    # Brief sleep to let the first connection close cleanly.
    await asyncio.sleep(0.05)

    async with connect(f"ws://127.0.0.1:{port}") as ws2:
        msg2 = await _recv_until(ws2, "trace_session_start")
        sid2 = msg2["payload"]["session_id"]

    assert sid1 == sid2, f"session_id changed between connections: {sid1!r} != {sid2!r}"


# ---------------------------------------------------------------------------
# trace_seek_request → trace_window
# ---------------------------------------------------------------------------


async def test_seek_full_window(
    live_server_with_trace: tuple[int, Path],
) -> None:
    """Seek the full trace (start=0, count=10) → all 10 events + total=10."""
    port, _ = live_server_with_trace
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        session_msg = await _recv_until(ws, "trace_session_start")
        sid = session_msg["payload"]["session_id"]

        reply = await _send_seek(ws, sid, 0, 10)
        assert reply["type"] == "trace_window"
        assert reply["payload"]["session_id"] == sid
        assert reply["payload"]["start_index"] == 0
        assert reply["payload"]["total"] == 10
        assert len(reply["payload"]["events"]) == 10


async def test_seek_mid_window(
    live_server_with_trace: tuple[int, Path],
) -> None:
    """Seek a mid-slice → correct events + correct start_index."""
    port, _ = live_server_with_trace
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        session_msg = await _recv_until(ws, "trace_session_start")
        sid = session_msg["payload"]["session_id"]

        reply = await _send_seek(ws, sid, 3, 4)
        assert reply["type"] == "trace_window"
        assert reply["payload"]["start_index"] == 3
        assert reply["payload"]["total"] == 10
        events = reply["payload"]["events"]
        assert len(events) == 4
        assert events[0]["node_id"] == "s.py:fn_3"
        assert events[3]["node_id"] == "s.py:fn_6"


async def test_seek_id_echoed(
    live_server_with_trace: tuple[int, Path],
) -> None:
    """trace_window must echo the request id verbatim."""
    port, _ = live_server_with_trace
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        session_msg = await _recv_until(ws, "trace_session_start")
        sid = session_msg["payload"]["session_id"]

        req_id = "my-correlation-id-42"
        reply = await _send_seek(ws, sid, 0, 5, req_id=req_id)
        assert reply["id"] == req_id


async def test_seek_count_past_eof_returns_partial(
    live_server_with_trace: tuple[int, Path],
) -> None:
    """count extending past EOF → partial window, not an error."""
    port, _ = live_server_with_trace
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        session_msg = await _recv_until(ws, "trace_session_start")
        sid = session_msg["payload"]["session_id"]

        # Start at 8, ask for 100 events — only 2 exist (8 and 9).
        reply = await _send_seek(ws, sid, 8, 100)
        assert reply["type"] == "trace_window"
        assert len(reply["payload"]["events"]) == 2
        assert reply["payload"]["total"] == 10


async def test_seek_start_past_eof_returns_empty_window(
    live_server_with_trace: tuple[int, Path],
) -> None:
    """start >= total → empty events list, type is still trace_window."""
    port, _ = live_server_with_trace
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        session_msg = await _recv_until(ws, "trace_session_start")
        sid = session_msg["payload"]["session_id"]

        reply = await _send_seek(ws, sid, 999, 10)
        assert reply["type"] == "trace_window"
        assert reply["payload"]["events"] == []
        assert reply["payload"]["total"] == 10


# ---------------------------------------------------------------------------
# trace_seek_request with unknown session → trace_seek_error
# ---------------------------------------------------------------------------


async def test_seek_unknown_session_id(
    live_server_with_trace: tuple[int, Path],
) -> None:
    """trace_seek_request for an unknown session_id → trace_seek_error."""
    port, _ = live_server_with_trace
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        # Don't wait for session_start — send seek with a bogus session_id.
        reply = await _send_seek(ws, "does-not-exist", 0, 5)
        assert reply["type"] == "trace_seek_error"
        assert reply["payload"]["session_id"] == "does-not-exist"
        assert "reason" in reply["payload"]


async def test_seek_error_echoes_request_id(
    live_server_with_trace: tuple[int, Path],
) -> None:
    """trace_seek_error must echo the request id."""
    port, _ = live_server_with_trace
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        req_id = "error-correlation-99"
        reply = await _send_seek(ws, "bad-sid", 0, 5, req_id=req_id)
        assert reply["type"] == "trace_seek_error"
        assert reply["id"] == req_id


# ---------------------------------------------------------------------------
# Live-attach mode: no seek support
# ---------------------------------------------------------------------------


async def test_live_mode_seek_returns_error(free_port: int, tmp_path: Path) -> None:
    """In live-attach mode (no --trace-source), seek requests return trace_seek_error."""
    task = asyncio.create_task(serve("127.0.0.1", free_port, root=tmp_path))
    await asyncio.sleep(0.05)
    try:
        async with connect(f"ws://127.0.0.1:{free_port}") as ws:
            # Live-attach: no trace_session_start is sent automatically.
            # A seek request for any session_id must return trace_seek_error.
            reply = await _send_seek(ws, "any-session", 0, 5)
            assert reply["type"] == "trace_seek_error"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
