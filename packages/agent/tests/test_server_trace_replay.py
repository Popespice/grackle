"""Tests for ``grackle serve --trace-source`` file-replay mode.

Phase 7.3 introduced **window-only seekable mode**: when the ``JsonlIndex``
builds successfully, the server switches to:

    static_graph → trace_session_start(seekable=true) → trace_session_end

No individual ``trace_event`` messages are streamed — the browser fetches event
windows on demand via ``trace_seek_request``.  The ``trace_session_end`` payload
still carries the correct ``event_count`` (from the index).

Tests use pace=False so they complete in milliseconds.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from websockets.asyncio.client import connect

from grackle.adapters.base import TraceEvent
from grackle.python_runtime.writer import write_jsonl
from grackle.server import serve

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

_TINY_APP = Path(__file__).parent.parent.parent.parent / "fixtures" / "tiny-python-app"
_GOLDEN_JSONL = _TINY_APP / "trace.golden.jsonl"


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_trace_events(n: int = 3) -> list[TraceEvent]:
    """Return n minimal trace event dicts with monotonically increasing ts_ns."""
    return [
        TraceEvent(
            event="call",
            node_id=f"script.py:func_{i}",
            ts_ns=1_000_000 * (i + 1),
            thread_id=1,
            frame_depth=i,
            metadata={},
        )
        for i in range(n)
    ]


@pytest.fixture
async def replay_server(
    free_port: int, tmp_path: Path
) -> AsyncGenerator[tuple[int, Path, int], None]:
    """Server with a 3-event trace file; pace=False for fast tests.

    Yields (port, root, event_count).
    """
    # Create a minimal Python project so static_graph is pushed.
    script = tmp_path / "script.py"
    script.write_text(
        "def func_0() -> None: pass\ndef func_1() -> None: pass\ndef func_2() -> None: pass\n",
        encoding="utf-8",
    )

    events = _make_trace_events(3)
    trace_file = tmp_path / "trace.jsonl"
    write_jsonl(events, trace_file)

    task = asyncio.create_task(
        serve("127.0.0.1", free_port, root=tmp_path, trace_source=trace_file, pace=False)
    )
    await asyncio.sleep(0.05)
    yield free_port, tmp_path, len(events)
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def _recv_all_until_session_end(ws: Any, timeout: float = 5.0) -> list[dict[str, Any]]:
    """Receive messages until trace_session_end (inclusive) or timeout."""
    messages: list[dict[str, Any]] = []
    deadline = asyncio.get_event_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_event_loop().time()
        if remaining <= 0:
            break
        raw = await asyncio.wait_for(ws.recv(), timeout=remaining)
        msg = json.loads(raw)
        messages.append(msg)
        if msg["type"] == "trace_session_end":
            break
    return messages


# ---------------------------------------------------------------------------
# Sequence: static_graph → session_start → events → session_end
# ---------------------------------------------------------------------------


async def test_static_graph_arrives_before_session_start(
    replay_server: tuple[int, Path, int],
) -> None:
    """static_graph message must precede trace_session_start (guaranteed by sequential await)."""
    port, _root, _n = replay_server
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        msgs = await _recv_all_until_session_end(ws)
    types = [m["type"] for m in msgs]
    assert "static_graph" in types
    assert "trace_session_start" in types
    sg_idx = types.index("static_graph")
    start_idx = types.index("trace_session_start")
    assert sg_idx < start_idx, f"static_graph at {sg_idx} must precede session_start at {start_idx}"


async def test_session_start_then_session_end_no_events(
    replay_server: tuple[int, Path, int],
) -> None:
    """Seekable mode: session_start is first trace message, session_end is last; no events streamed.

    Phase 7.3 window-only mode: the browser fetches event windows on demand via
    trace_seek_request.  No individual trace_event messages are sent during replay.
    """
    port, _root, _n = replay_server
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        msgs = await _recv_all_until_session_end(ws)

    trace_msgs = [m for m in msgs if m["type"].startswith("trace_")]
    types = [m["type"] for m in trace_msgs]
    assert types[0] == "trace_session_start"
    assert types[-1] == "trace_session_end"
    # In window-only seekable mode no events are streamed — the middle is empty.
    assert not any(t == "trace_event" for t in types)


async def test_event_count_in_session_end_matches(
    replay_server: tuple[int, Path, int],
) -> None:
    """session_end.payload.event_count must equal the total events in the trace file.

    In seekable mode the count comes from the JsonlIndex (no events are streamed),
    so event_count is the full file total and received trace_event count is 0.
    """
    port, _root, expected_count = replay_server
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        msgs = await _recv_all_until_session_end(ws)

    session_end = next(m for m in msgs if m["type"] == "trace_session_end")
    trace_events = [m for m in msgs if m["type"] == "trace_event"]
    # event_count in session_end must be the file total (from JsonlIndex).
    assert session_end["payload"]["event_count"] == expected_count
    # No individual events are streamed in seekable window-only mode.
    assert len(trace_events) == 0


async def test_no_pace_completes_fast(free_port: int, tmp_path: Path) -> None:
    """pace=False (--no-pace) must complete the full session in well under 1s."""
    script = tmp_path / "script.py"
    script.write_text("def f(): pass\n", encoding="utf-8")

    events = _make_trace_events(10)
    trace_file = tmp_path / "trace.jsonl"
    write_jsonl(events, trace_file)

    task = asyncio.create_task(
        serve("127.0.0.1", free_port, root=tmp_path, trace_source=trace_file, pace=False)
    )
    await asyncio.sleep(0.05)
    try:
        async with connect(f"ws://127.0.0.1:{free_port}") as ws:
            msgs = await asyncio.wait_for(_recv_all_until_session_end(ws), timeout=2.0)
        assert any(m["type"] == "trace_session_end" for m in msgs)
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# ---------------------------------------------------------------------------
# Content: node_ids must come from the static graph (or be <unresolved>)
# ---------------------------------------------------------------------------


async def test_event_node_ids_in_static_graph(
    replay_server: tuple[int, Path, int],
) -> None:
    """Every non-<unresolved> node_id in trace_events must exist in the static graph."""
    port, _root, _n = replay_server
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        msgs = await _recv_all_until_session_end(ws)

    sg = next((m for m in msgs if m["type"] == "static_graph"), None)
    assert sg is not None
    graph_node_ids = {n["id"] for n in sg["payload"]["nodes"]}

    trace_events = [m for m in msgs if m["type"] == "trace_event"]
    for ev in trace_events:
        nid = ev["payload"]["node_id"]
        if nid != "<unresolved>":
            assert nid in graph_node_ids, (
                f"node_id {nid!r} in trace_event not found in static graph"
            )


# ---------------------------------------------------------------------------
# Mid-replay disconnect → server survives
# ---------------------------------------------------------------------------


async def test_mid_replay_disconnect_server_survives(
    replay_server: tuple[int, Path, int],
) -> None:
    """Disconnecting mid-replay must not crash the server; a reconnect gets pong."""
    port, _root, _n = replay_server

    # First client disconnects immediately after session_start.
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            if json.loads(raw)["type"] == "trace_session_start":
                break
        # ws goes out of scope here — connection closed.

    # Server must still be reachable.
    async with connect(f"ws://127.0.0.1:{port}") as ws:
        await ws.send(json.dumps({"id": "ping1", "type": "ping", "payload": {}}))
        while True:
            raw = await asyncio.wait_for(ws.recv(), timeout=5.0)
            msg = json.loads(raw)
            if msg["type"] == "pong":
                break
    assert msg["id"] == "ping1"


# ---------------------------------------------------------------------------
# Missing trace source → empty session (server stays up)
# ---------------------------------------------------------------------------


async def test_missing_trace_source_sends_empty_session(free_port: int, tmp_path: Path) -> None:
    """When the trace file does not exist, an empty session is emitted and the server stays up."""
    missing = tmp_path / "does_not_exist.jsonl"
    # Do NOT create the file.

    task = asyncio.create_task(
        serve("127.0.0.1", free_port, root=tmp_path, trace_source=missing, pace=False)
    )
    await asyncio.sleep(0.05)
    try:
        async with connect(f"ws://127.0.0.1:{free_port}") as ws:
            msgs = await _recv_all_until_session_end(ws)

        session_end = next((m for m in msgs if m["type"] == "trace_session_end"), None)
        assert session_end is not None
        assert session_end["payload"]["event_count"] == 0

        trace_events = [m for m in msgs if m["type"] == "trace_event"]
        assert len(trace_events) == 0
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task


# ---------------------------------------------------------------------------
# Golden fixture replay (integration smoke)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(not _GOLDEN_JSONL.exists(), reason="golden trace not found")
async def test_golden_trace_replays_correctly(free_port: int) -> None:
    """The golden trace for tiny-python-app replays with correct event_count."""
    task = asyncio.create_task(
        serve(
            "127.0.0.1",
            free_port,
            root=_TINY_APP,
            trace_source=_GOLDEN_JSONL,
            pace=False,
        )
    )
    await asyncio.sleep(0.05)
    try:
        async with connect(f"ws://127.0.0.1:{free_port}") as ws:
            msgs = await _recv_all_until_session_end(ws, timeout=10.0)

        session_end = next(m for m in msgs if m["type"] == "trace_session_end")
        trace_events = [m for m in msgs if m["type"] == "trace_event"]
        # Seekable window-only mode: event_count > 0 (from index), no events streamed.
        assert session_end["payload"]["event_count"] > 0
        assert len(trace_events) == 0

        # trace_session_start must include source="replay" and seekable=true.
        session_start = next(m for m in msgs if m["type"] == "trace_session_start")
        assert session_start["payload"]["source"] == "replay"
        assert session_start["payload"].get("seekable") is True
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
