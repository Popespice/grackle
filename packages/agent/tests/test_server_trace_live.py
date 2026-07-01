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

Phase 7.1 additions:
- ``trim_ring_buffer`` unit tests for the count-cap eviction path.
- Integration test: late joiner receives ≤ GRACKLE_TRACE_BUFFER_MAX_EVENTS
  messages when the env var is set.
"""

from __future__ import annotations

import asyncio
import collections
import contextlib
import json
import os
from typing import TYPE_CHECKING, Any

import pytest
from websockets.asyncio.client import connect

from grackle.python_runtime.live_buffer import trace_buffer_max_events, trim_ring_buffer
from grackle.server import serve
from grackle.session_store import SessionStore

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


# ---------------------------------------------------------------------------
# Phase 7.1 — trim_ring_buffer count-cap unit tests
# ---------------------------------------------------------------------------


_RING_BASE_NS: int = 10**15  # arbitrary large timestamp well within any 60 s window


def _make_ring(n: int, base_ts: int = _RING_BASE_NS) -> collections.deque[tuple[int, str]]:
    """Return a deque of n entries with ascending timestamps near *base_ts*.

    ``_RING_BASE_NS`` is chosen so that with ``buffer_seconds=60.0`` and
    ``now_ns = base_ts + n``, the age-cutoff is negative — i.e. no entries
    are evicted by the age trim.  Tests that want to isolate the count cap
    should pass ``now_ns = base_ts + n + 1`` with ``buffer_seconds=60.0``.
    """
    return collections.deque((base_ts + i, f"msg-{i}") for i in range(n))


def testtrim_ring_buffer_count_cap_evicts_oldest() -> None:
    """When max_events is set, oldest entries are evicted until len <= max_events."""
    buf = _make_ring(10)
    # now_ns is just past the last entry; buffer_seconds=60 → cutoff is negative
    # (no age eviction), so only the count cap fires.
    now_ns = _RING_BASE_NS + 10 + 1
    trim_ring_buffer(buf, now_ns=now_ns, buffer_seconds=60.0, max_events=3)
    assert len(buf) == 3
    # The *newest* three are retained (oldest evicted).
    assert buf[-1][1] == "msg-9"
    assert buf[0][1] == "msg-7"


def testtrim_ring_buffer_count_cap_none_is_unbounded() -> None:
    """max_events=None leaves size unlimited (original behaviour)."""
    buf = _make_ring(20)
    now_ns = _RING_BASE_NS + 20 + 1
    trim_ring_buffer(buf, now_ns=now_ns, buffer_seconds=60.0, max_events=None)
    assert len(buf) == 20


def testtrim_ring_buffer_count_cap_already_within_limit() -> None:
    """No eviction when len <= max_events."""
    buf = _make_ring(5)
    now_ns = _RING_BASE_NS + 5 + 1
    trim_ring_buffer(buf, now_ns=now_ns, buffer_seconds=60.0, max_events=10)
    assert len(buf) == 5


def testtrim_ring_buffer_age_and_count_interplay() -> None:
    """Age trim runs first; count cap then applies to whatever remains."""
    # 10 entries; first 5 are old, last 5 are recent.
    now_ns = 2_000_000_000
    old = [(500_000_000 + i, f"old-{i}") for i in range(5)]
    new = [(1_900_000_000 + i, f"new-{i}") for i in range(5)]
    buf: collections.deque[tuple[int, str]] = collections.deque(old + new)

    # buffer_seconds=1 means cutoff = now_ns - 1e9 = 1_000_000_000
    # The old entries all have ts < 1_000_000_000, so they're evicted by age.
    # max_events=3 then caps the 5 remaining new entries to the newest 3.
    trim_ring_buffer(buf, now_ns=now_ns, buffer_seconds=1.0, max_events=3)
    assert len(buf) == 3
    assert all(entry[1].startswith("new-") for entry in buf)


def testtrim_ring_buffer_default_max_events_is_none() -> None:
    """Calling trim_ring_buffer without max_events behaves as before."""
    buf = _make_ring(100)
    now_ns = _RING_BASE_NS + 100 + 1
    trim_ring_buffer(buf, now_ns=now_ns, buffer_seconds=60.0)
    assert len(buf) == 100


# ---------------------------------------------------------------------------
# Phase 7.1 — trace_buffer_max_events env-var helper
# ---------------------------------------------------------------------------


def testtrace_buffer_max_events_default_is_none(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("GRACKLE_TRACE_BUFFER_MAX_EVENTS", raising=False)
    assert trace_buffer_max_events() is None


def testtrace_buffer_max_events_positive_integer() -> None:
    os.environ["GRACKLE_TRACE_BUFFER_MAX_EVENTS"] = "500"
    try:
        assert trace_buffer_max_events() == 500
    finally:
        del os.environ["GRACKLE_TRACE_BUFFER_MAX_EVENTS"]


def testtrace_buffer_max_events_zero_returns_none() -> None:
    os.environ["GRACKLE_TRACE_BUFFER_MAX_EVENTS"] = "0"
    try:
        assert trace_buffer_max_events() is None
    finally:
        del os.environ["GRACKLE_TRACE_BUFFER_MAX_EVENTS"]


def testtrace_buffer_max_events_negative_returns_none() -> None:
    os.environ["GRACKLE_TRACE_BUFFER_MAX_EVENTS"] = "-5"
    try:
        assert trace_buffer_max_events() is None
    finally:
        del os.environ["GRACKLE_TRACE_BUFFER_MAX_EVENTS"]


def testtrace_buffer_max_events_non_integer_returns_none() -> None:
    os.environ["GRACKLE_TRACE_BUFFER_MAX_EVENTS"] = "not-a-number"
    try:
        assert trace_buffer_max_events() is None
    finally:
        del os.environ["GRACKLE_TRACE_BUFFER_MAX_EVENTS"]


# ---------------------------------------------------------------------------
# Phase 7.1 — integration: late joiner receives ≤ max_events from ring-buffer
# ---------------------------------------------------------------------------


@pytest.fixture
async def capped_live_server(
    free_port: int,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> AsyncGenerator[int, None]:
    """Server with GRACKLE_TRACE_BUFFER_MAX_EVENTS=3 (live-attach mode)."""
    monkeypatch.setenv("GRACKLE_TRACE_BUFFER_MAX_EVENTS", "3")
    task = asyncio.create_task(serve("127.0.0.1", free_port, root=tmp_path))
    await asyncio.sleep(0.05)
    yield free_port
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_late_consumer_receives_at_most_max_events(capped_live_server: int) -> None:
    """A late joiner must receive ≤ GRACKLE_TRACE_BUFFER_MAX_EVENTS buffered messages."""
    port = capped_live_server

    # Producer sends 6 trace messages (start + 4 events + end).
    async with connect(f"ws://127.0.0.1:{port}") as producer:
        await producer.send(_make_session_start())
        for i in range(4):
            await producer.send(_make_trace_event(i))
        await producer.send(_make_session_end(count=4))
        await asyncio.sleep(0.05)  # give server time to buffer

    # Late consumer; server ring-buffer must have been capped to 3.
    async with connect(f"ws://127.0.0.1:{port}") as late:
        await late.send(json.dumps({"id": "lc-ping", "type": "ping", "payload": {}}))
        received: list[dict[str, Any]] = []
        deadline = asyncio.get_event_loop().time() + 2.0
        pong_seen = False
        while not pong_seen:
            remaining = deadline - asyncio.get_event_loop().time()
            if remaining <= 0:
                break
            raw = await asyncio.wait_for(late.recv(), timeout=remaining)
            msg = json.loads(raw)
            received.append(msg)
            if msg["type"] == "pong":
                pong_seen = True

    trace_msgs = [m for m in received if m["type"] != "pong"]
    types = [m["type"] for m in trace_msgs]
    assert len(trace_msgs) == 3, (
        f"expected exactly 3 buffered trace msgs, got {len(trace_msgs)}: {types}"
    )
    # The 3 retained entries must all be trace-protocol messages.
    assert all(t in ("trace_session_start", "trace_event", "trace_session_end") for t in types), (
        f"unexpected message types in ring-buffer flush: {types}"
    )


# ---------------------------------------------------------------------------
# Phase 9.3 — live-stream recording sink (ADR-0020 amendment)
# ---------------------------------------------------------------------------


@pytest.fixture
async def store_server(
    free_port: int, tmp_path: Path
) -> AsyncGenerator[tuple[int, SessionStore, Path], None]:
    """Server with --store set (live-attach mode + recording sink enabled)."""
    db_path = tmp_path / "sessions.db"
    store = SessionStore.open(db_path)
    task = asyncio.create_task(serve("127.0.0.1", free_port, root=tmp_path, store=store))
    await asyncio.sleep(0.05)
    yield free_port, store, tmp_path / "recordings"
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


async def test_live_session_recorded_to_store(
    store_server: tuple[int, SessionStore, Path],
) -> None:
    """A clean producer session (start/events/end) is tee'd to disk and registered."""
    port, store, recordings_dir = store_server

    async with connect(f"ws://127.0.0.1:{port}") as producer:
        await producer.send(_make_session_start("rec-1"))
        for i in range(3):
            await producer.send(_make_trace_event(i))
        await producer.send(_make_session_end("rec-1", count=3))
        await asyncio.sleep(0.1)

    meta = store.get_session("rec-1")
    assert meta is not None
    assert meta.event_count == 3

    final = recordings_dir / "rec-1.jsonl"
    assert final.exists()
    assert not (recordings_dir / "rec-1.jsonl.part").exists()


async def test_producer_disconnect_without_end_finalizes(
    store_server: tuple[int, SessionStore, Path],
) -> None:
    """A producer that disconnects mid-stream (no trace_session_end) still
    gets its recording finalized via the receive-loop's finally block."""
    port, store, recordings_dir = store_server

    async with connect(f"ws://127.0.0.1:{port}") as producer:
        await producer.send(_make_session_start("rec-2"))
        for i in range(2):
            await producer.send(_make_trace_event(i))
        # producer goes out of scope without sending trace_session_end.

    await asyncio.sleep(0.2)

    meta = store.get_session("rec-2")
    assert meta is not None
    assert meta.event_count == 2

    final = recordings_dir / "rec-2.jsonl"
    assert final.exists()
    assert not (recordings_dir / "rec-2.jsonl.part").exists()


async def test_server_shutdown_cancel_finalizes(free_port: int, tmp_path: Path) -> None:
    """Cancelling the server task mid-stream (no session_end) still finalizes
    the in-flight recording via the shielded finally-block finalize."""
    db_path = tmp_path / "sessions.db"
    store = SessionStore.open(db_path)
    recordings_dir = tmp_path / "recordings"
    task = asyncio.create_task(serve("127.0.0.1", free_port, root=tmp_path, store=store))
    await asyncio.sleep(0.05)

    async with connect(f"ws://127.0.0.1:{free_port}") as producer:
        await producer.send(_make_session_start("rec-3"))
        for i in range(4):
            await producer.send(_make_trace_event(i))
        await asyncio.sleep(0.1)

        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    # serve()'s own finally already closed `store` on shutdown; reopen from
    # disk to verify the shielded finalize actually persisted before close.
    reopened = SessionStore.open(db_path)
    meta = reopened.get_session("rec-3")
    reopened.close()
    assert meta is not None
    assert meta.event_count == 4

    final = recordings_dir / "rec-3.jsonl"
    assert final.exists()
    assert not (recordings_dir / "rec-3.jsonl.part").exists()


async def test_no_store_no_recording(live_server: int, tmp_path: Path) -> None:
    """Without --store, no recordings/ directory is created and behavior is
    unchanged (regression guard)."""
    port = live_server

    async with connect(f"ws://127.0.0.1:{port}") as producer:
        await producer.send(_make_session_start("rec-4"))
        await producer.send(_make_trace_event(0))
        await producer.send(_make_session_end("rec-4", count=1))
        await asyncio.sleep(0.05)

    assert not (tmp_path / "recordings").exists()


async def test_two_sessions_back_to_back(
    store_server: tuple[int, SessionStore, Path],
) -> None:
    """Two sequential sessions on one connection produce two distinct
    recordings + store rows."""
    port, store, recordings_dir = store_server

    async with connect(f"ws://127.0.0.1:{port}") as producer:
        await producer.send(_make_session_start("rec-a"))
        await producer.send(_make_trace_event(0))
        await producer.send(_make_session_end("rec-a", count=1))

        await producer.send(_make_session_start("rec-b"))
        await producer.send(_make_trace_event(0))
        await producer.send(_make_trace_event(1))
        await producer.send(_make_session_end("rec-b", count=2))
        await asyncio.sleep(0.1)

    meta_a = store.get_session("rec-a")
    meta_b = store.get_session("rec-b")
    assert meta_a is not None and meta_a.event_count == 1
    assert meta_b is not None and meta_b.event_count == 2
    assert (recordings_dir / "rec-a.jsonl").exists()
    assert (recordings_dir / "rec-b.jsonl").exists()


async def test_replay_source_not_self_recorded(free_port: int, tmp_path: Path) -> None:
    """With --trace-source AND --store set, a pure consumer (no inbound trace
    messages) must not produce a recording beyond the register_trace_source
    row for the replay file itself."""
    import json as _json

    trace_source = tmp_path / "source.jsonl"
    trace_source.write_text(
        _json.dumps(
            {
                "event": "call",
                "node_id": "script.py:f",
                "ts_ns": 0,
                "thread_id": 1,
                "frame_depth": 0,
                "metadata": {},
            }
        )
        + "\n",
        encoding="utf-8",
    )
    db_path = tmp_path / "sessions.db"
    store = SessionStore.open(db_path)
    recordings_dir = tmp_path / "recordings"
    task = asyncio.create_task(
        serve(
            "127.0.0.1",
            free_port,
            root=tmp_path,
            trace_source=trace_source,
            store=store,
            pace=False,
        )
    )
    await asyncio.sleep(0.05)

    async with connect(f"ws://127.0.0.1:{free_port}") as consumer:
        await consumer.send(json.dumps({"id": "c0", "type": "ping", "payload": {}}))
        while True:
            msg = json.loads(await asyncio.wait_for(consumer.recv(), timeout=5.0))
            if msg["type"] == "pong":
                break

    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task

    # serve()'s own finally already closed `store` on shutdown; reopen from disk.
    reopened = SessionStore.open(db_path)
    sessions = reopened.list_sessions()
    reopened.close()
    assert len(sessions) == 1  # only the replay-file's register_trace_source row
    assert sessions[0].source_path == str(trace_source.resolve())
    assert not recordings_dir.exists() or list(recordings_dir.glob("*.jsonl")) == []


async def _wait_for(path: Path, *, timeout: float = 5.0) -> None:
    """Poll until *path* exists (deterministic hand-off between producers)."""
    deadline = asyncio.get_event_loop().time() + timeout
    while not path.exists():
        if asyncio.get_event_loop().time() > deadline:
            raise AssertionError(f"timed out waiting for {path}")
        await asyncio.sleep(0.01)


async def test_duplicate_session_id_second_producer_skipped(
    store_server: tuple[int, SessionStore, Path],
) -> None:
    """While producer A owns a recording for a session_id, a second producer B
    that reuses the same id must be skipped (FileExistsError caught) and must
    NOT corrupt A's recording. Ordering is made deterministic: B only sends its
    colliding start after A's .part is observably on disk."""
    port, store, recordings_dir = store_server
    part = recordings_dir / "rec-dup.jsonl.part"
    final = recordings_dir / "rec-dup.jsonl"

    async with connect(f"ws://127.0.0.1:{port}") as producer_a:
        # A opens the recording and writes one event, but does NOT end yet.
        await producer_a.send(_make_session_start("rec-dup"))
        await producer_a.send(_make_trace_event(0))
        await _wait_for(part)  # A now owns rec-dup.jsonl.part

        # B reuses the same id while A holds the .part -> exclusive-create
        # FileExistsError -> B is skipped, A is untouched.
        async with connect(f"ws://127.0.0.1:{port}") as producer_b:
            await producer_b.send(_make_session_start("rec-dup"))
            await producer_b.send(_make_trace_event(1))
            await asyncio.sleep(0.05)
        # B disconnects; its skipped session leaves no recorder, so nothing to
        # finalize. A's .part is still intact.
        assert part.exists()

        # A finishes cleanly.
        await producer_a.send(_make_session_end("rec-dup", count=1))
        await _wait_for(final)

    meta = store.get_session("rec-dup")
    assert meta is not None
    assert meta.event_count == 1  # A's single event, not corrupted by B

    assert final.exists()
    assert not part.exists()
    # Exactly one recording file for this id.
    assert list(recordings_dir.glob("rec-dup*.jsonl")) == [final]
