"""Test campaign T6-3 / T7-5 / T10-1 (docs/test-campaigns/phase-12.md): the
server's live-ingest path under fault.

A producer's ``trace_session_start`` / ``trace_event`` / ``trace_session_end``
messages are handled in that connection's ``_receive_loop``: each one is
ring-buffered, broadcast to every other connection (``live_buffer.broadcast``,
one ``await ws.send`` per consumer, in turn), and — with a store — tee'd to a
``RecordingSink``. Probed against the real server through ``start_server``:

* T6-3 oversized frame: a message over websockets' default 1 MiB ``max_size``
  closes the producer with 1009 before the receive loop sees it. The in-flight
  recording is finalized with exactly the events before it — including ones
  still queued, unconsumed, when the protocol failed. Pinned.
* T6-3 ``session_load_request`` flood: each request spawns an untracked task
  whose index build runs on the loop's shared default executor — the same
  executor ``RecordingSink.finalize`` registers sessions through — and
  concurrent loads of one session each build their own index. Ledgered ×2.
* T6-3 slow consumer: one consumer that stops reading blocks the broadcast,
  and with it every other consumer, the ring buffer, and the recording; and
  the keepalive then disconnects the healthy *producer* rather than the stuck
  consumer. Ledgered ×2. Once the stuck consumer goes away, ingest recovers
  intact — pinned.
* T7-5 two producers on two connections, one store: interleaved sessions are
  recorded separately and intact. Pinned (plus a hammer). Writing the hammer
  surfaced a first-party non-reading consumer: ``grackle trace --connect``'s
  post-run replay never reads its socket, so after the server pushes it a
  recent session's ring-buffer history it cannot complete its close
  handshake. Ledgered, with a passing control.
* T10-1 a real server-produced recording is LF-only, even from a producer
  whose frames carry CRLF whitespace, and byte-identical to the CLI writer's
  output. Pinned.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import json
import socket
import threading
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import pytest
from websockets.asyncio.client import connect
from websockets.asyncio.server import serve as real_ws_serve
from websockets.exceptions import ConnectionClosedError
from websockets.frames import CloseCode, Frame, Opcode

from grackle import server as server_mod
from grackle.python_runtime import aggregates
from grackle.python_runtime.writer import read_jsonl, write_jsonl
from grackle.session_store import SessionMeta, SessionStore

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable
    from contextlib import AbstractAsyncContextManager
    from pathlib import Path

    from conftest import StartServer
    from websockets.asyncio.client import ClientConnection
    from websockets.asyncio.server import Server, ServerConnection

    from grackle.adapters.base import TraceEvent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _payload(i: int, *, prefix: str = "app", pad: int = 0) -> dict[str, Any]:
    return {
        "event": "call",
        "node_id": f"{prefix}.py:f{i}",
        "ts_ns": i * 1_000,
        "thread_id": 1,
        "frame_depth": 0,
        "metadata": {"pad": "x" * pad} if pad else {},
    }


def _msg(kind: str, payload: dict[str, Any], msg_id: str = "m") -> str:
    return json.dumps({"id": msg_id, "type": kind, "payload": payload})


def _session_start(sid: str) -> str:
    return _msg("trace_session_start", {"session_id": sid, "started_ns": 1, "source": "live"})


def _session_end(sid: str, count: int) -> str:
    return _msg("trace_session_end", {"session_id": sid, "ended_ns": 2, "event_count": count})


def _event(payload: dict[str, Any]) -> str:
    return _msg("trace_event", payload)


def _lf_bytes(payloads: list[dict[str, Any]]) -> bytes:
    """What grackle writes for *payloads*: one ``ensure_ascii=False`` JSON
    object per line, LF-terminated, UTF-8 — the shared JSONL contract."""
    return b"".join((json.dumps(p, ensure_ascii=False) + "\n").encode("utf-8") for p in payloads)


async def _barrier(ws: ClientConnection, tag: str, timeout: float = 5.0) -> None:
    """Round-trip a ping. One connection's messages are handled strictly in
    order, so the pong proves every message sent before it — including a
    ``trace_session_end``'s awaited finalize, store row and all — is done."""
    await ws.send(json.dumps({"id": tag, "type": "ping", "payload": {}}))
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=timeout))
        if msg["type"] == "pong" and msg["id"] == tag:
            return


async def _wait_until(predicate: Callable[[], bool], timeout: float) -> bool:
    """Poll *predicate* until true or *timeout* elapses; never raises."""
    deadline = time.monotonic() + timeout
    while not predicate():
        if time.monotonic() >= deadline:
            return False
        await asyncio.sleep(0.01)
    return True


async def _wait_for_row(store: SessionStore, sid: str, timeout: float = 5.0) -> SessionMeta:
    meta: list[SessionMeta | None] = [None]

    def _registered() -> bool:
        meta[0] = store.get_session(sid)
        return meta[0] is not None

    assert await _wait_until(_registered, timeout), f"session {sid!r} was never registered"
    assert meta[0] is not None
    return meta[0]


async def _collect_session(ws: ClientConnection) -> list[dict[str, Any]]:
    """Receive until a ``trace_session_end`` (inclusive)."""
    received: list[dict[str, Any]] = []
    while not received or received[-1]["type"] != "trace_session_end":
        received.append(json.loads(await ws.recv()))
    return received


def _assert_recorded(
    store: SessionStore, recordings: Path, sid: str, payloads: list[dict[str, Any]]
) -> None:
    meta = store.get_session(sid)
    assert meta is not None, f"session {sid!r} not registered"
    assert meta.event_count == len(payloads)
    assert (recordings / f"{sid}.jsonl").read_bytes() == _lf_bytes(payloads)
    assert not (recordings / f"{sid}.jsonl.part").exists()


# ---------------------------------------------------------------------------
# T6-3: an oversized frame
# ---------------------------------------------------------------------------

# websockets' default max_size, which serve() does not override.
_MAX_SIZE = 2**20


async def test_oversized_frame_closes_1009_and_finalizes_the_in_flight_recording(
    start_server: StartServer, tmp_path: Path
) -> None:
    """A message over ``max_size`` is refused at the frame header: the server
    closes the producer with 1009 and the receive loop never sees it. The loop
    exits by ``ConnectionClosedError`` — not the clean end-of-iteration a
    normal disconnect produces — and its ``finally`` must still finalize the
    recording, holding exactly the events before the oversized one.

    Compression is off, so the header's declared length is the real one. Only
    the header and a few payload bytes are written: the server refuses the
    frame on the header alone, and having the client buffer the whole megabyte
    only exposes an unrelated race in CPython 3.14's selector transport at the
    client's teardown (``abort()`` after a drain-then-close)."""
    store = SessionStore.open(tmp_path / "sessions.db")
    _, port = await start_server(root=tmp_path, store=store)
    url = f"ws://127.0.0.1:{port}"
    before = [_payload(i) for i in range(3)]
    oversized = Frame(Opcode.TEXT, _event(_payload(3, pad=_MAX_SIZE)).encode())

    async with connect(url) as consumer:
        await _barrier(consumer, "consumer-ready")
        async with connect(url, compression=None) as producer:
            await producer.send(_session_start("big"))
            for p in before:
                await producer.send(_event(p))
            await _barrier(producer, "before-oversized")
            producer.transport.write(oversized.serialize(mask=True)[:64])
            with pytest.raises(ConnectionClosedError) as closed:
                await asyncio.wait_for(producer.recv(), timeout=5.0)
        assert closed.value.rcvd is not None
        assert closed.value.rcvd.code == CloseCode.MESSAGE_TOO_BIG

        await _wait_for_row(store, "big")
        _assert_recorded(store, tmp_path / "recordings", "big", before)

        # Fan-out never saw the oversized message, and the server still serves.
        seen = [json.loads(await asyncio.wait_for(consumer.recv(), 5.0)) for _ in range(4)]
        assert [m["type"] for m in seen] == ["trace_session_start"] + ["trace_event"] * 3
        await _barrier(consumer, "consumer-still-served")


async def test_oversized_frame_keeps_the_events_queued_ahead_of_it(
    start_server: StartServer, tmp_path: Path
) -> None:
    """The five events and the oversized frame reach the server in ONE TCP
    write, so it parses all six in a single read and fails the connection
    before the receive loop has consumed any of the five. They must still be
    recorded: websockets hands over messages queued before a failure, and the
    loop records them before its ``finally`` finalizes. Compressed (the
    client's default), so this is also the decompressed-size path — the frame
    on the wire is a few bytes."""
    store = SessionStore.open(tmp_path / "sessions.db")
    _, port = await start_server(root=tmp_path, store=store)
    queued = [_payload(i) for i in range(5)]

    async with connect(f"ws://127.0.0.1:{port}") as producer:
        protocol = producer.protocol
        protocol.send_text(_session_start("burst").encode())
        for p in queued:
            protocol.send_text(_event(p).encode())
        protocol.send_text(_event(_payload(5, pad=2 * _MAX_SIZE)).encode())
        producer.transport.write(b"".join(protocol.data_to_send()))
        with pytest.raises(ConnectionClosedError) as closed:
            await asyncio.wait_for(producer.recv(), timeout=5.0)
    assert closed.value.rcvd is not None
    assert closed.value.rcvd.code == CloseCode.MESSAGE_TOO_BIG

    await _wait_for_row(store, "burst")
    _assert_recorded(store, tmp_path / "recordings", "burst", queued)


# ---------------------------------------------------------------------------
# T6-3: a session_load_request flood
# ---------------------------------------------------------------------------


class _GatedBuilds:
    """Stands in for ``build_seekable``: counts each call, then holds it until
    released before running the real build — an index build over a file large
    enough to take a while, without needing one."""

    def __init__(self) -> None:
        self._real = aggregates.build_seekable
        self._lock = threading.Lock()
        self.release = threading.Event()
        self.calls = 0

    def __call__(self, path: Path) -> Any:
        with self._lock:
            self.calls += 1
        self.release.wait(timeout=30.0)
        return self._real(path)


def _store_sessions(tmp_path: Path, sids: list[str]) -> SessionStore:
    store = SessionStore.open(tmp_path / "sessions.db")
    for sid in sids:
        path = tmp_path / f"{sid}.jsonl"
        write_jsonl([cast("TraceEvent", _payload(0, prefix=sid))], path)
        store.save_session(SessionMeta(sid, sid, 0, 0, str(path), 1, "python"))
    return store


def _load_request(i: int, sid: str) -> str:
    return _msg("session_load_request", {"session_id": sid}, msg_id=f"load-{i}")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T6-3: session_load_request spawns an unbounded, untracked build task per "
        "request on the loop's shared default executor, so a flood starves "
        "RecordingSink.finalize's store write (docs/test-campaigns/phase-12.md)"
    ),
)
async def test_a_session_load_flood_does_not_stall_a_live_recording(
    start_server: StartServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Each ``session_load_request`` for a stored session becomes a bare
    ``asyncio.create_task`` — no bound, no reference kept, not cancelled when
    its client leaves — whose ``build_seekable`` runs on the default executor.
    ``RecordingSink.finalize`` registers every live session through that same
    executor, as do seek, query and session-list requests. A client that
    fires more loads than there are workers (``min(32, cpus + 4)``) at large
    stored sessions therefore parks every other executor user behind them;
    ``asyncio.run``'s shutdown waits for them too.

    The loop gets a 4-worker default executor so 8 requests are a flood on any
    machine; loads target 8 distinct sessions so the index cache cannot hide
    the fan-out. Expected: a producer's session, ended during the flood, is
    registered promptly. Observed: its ``.jsonl`` is renamed into place but
    the store row waits for the flood — the pong that follows the end never
    arrives within 2 s."""
    executor = concurrent.futures.ThreadPoolExecutor(max_workers=4)
    asyncio.get_running_loop().set_default_executor(executor)
    builds = _GatedBuilds()
    monkeypatch.setattr(aggregates, "build_seekable", builds)
    sids = [f"stored-{i}" for i in range(8)]
    store = _store_sessions(tmp_path, sids)
    _, port = await start_server(root=tmp_path, store=store)
    url = f"ws://127.0.0.1:{port}"

    try:
        async with connect(url, max_queue=None) as flood, connect(url) as producer:
            for i, sid in enumerate(sids):
                await flood.send(_load_request(i, sid))
            # Bounded: a server that caps concurrent loads never holds all four.
            await _wait_until(lambda: builds.calls >= 4, timeout=2.0)

            await producer.send(_session_start("live"))
            await producer.send(_event(_payload(0)))
            await producer.send(_session_end("live", 1))
            await asyncio.wait_for(_barrier(producer, "registered"), timeout=2.0)
            meta = store.get_session("live")
            assert meta is not None
            assert meta.event_count == 1
    finally:
        builds.release.set()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T6-3: load_stored_session checks its index cache before awaiting the "
        "build, so concurrent loads of one session each build the whole index "
        "(docs/test-campaigns/phase-12.md)"
    ),
)
async def test_concurrent_loads_of_one_session_share_one_index_build(
    start_server: StartServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``load_stored_session`` caches a built index in ``seekable_sessions``,
    but only once the build has finished: every load that arrives while one is
    in flight misses the cache and starts its own full scan of the file —
    N-fold the I/O, CPU and peak memory for one session. Without the gate the
    count is timing-dependent (40 back-to-back loads of a 20,000-event session
    ran 5 builds on the probing machine); holding the first build open makes
    it deterministic. Expected: one build, every request still answered."""
    builds = _GatedBuilds()
    monkeypatch.setattr(aggregates, "build_seekable", builds)
    store = _store_sessions(tmp_path, ["stored"])
    _, port = await start_server(root=tmp_path, store=store)

    try:
        async with connect(f"ws://127.0.0.1:{port}", max_queue=None) as client:
            for i in range(8):
                await client.send(_load_request(i, "stored"))
            await _wait_until(lambda: builds.calls >= 2, timeout=1.0)
            builds.release.set()
            ends = 0
            while ends < 8:
                msg = json.loads(await asyncio.wait_for(client.recv(), timeout=5.0))
                ends += msg["type"] == "trace_session_end"
    finally:
        builds.release.set()
    assert builds.calls == 1


# ---------------------------------------------------------------------------
# T6-3: a consumer that stops reading
# ---------------------------------------------------------------------------

# Kernel send buffer for every accepted connection. Left alone, TCP
# autotuning lets a non-reading consumer absorb anywhere from ~100 KiB to
# several MiB before the server's writes back up, depending on the OS; this
# makes the flow-control stall engage after ~100 KiB everywhere. It changes
# how soon the stall happens, not whether.
_SNDBUF = 8 * 1024
# Events big enough that a few fill the stalled consumer's pipe; the backlog
# is real bytes because that consumer does not negotiate compression.
_FILL_EVENTS = 48
_FILL_PAD = 32 * 1024


@dataclass
class _StallRig:
    port: int
    store: SessionStore
    recordings: Path
    server_side: dict[str, ServerConnection]

    def url(self, path: str) -> str:
        return f"ws://127.0.0.1:{self.port}{path}"

    def stall_engaged(self) -> bool:
        """True once the server's transport to ``/stalled`` has backed up past
        its high-water mark — the state in which ``ws.send`` to it blocks."""
        ws = self.server_side.get("/stalled")
        if ws is None:
            return False
        transport = ws.transport
        return transport.get_write_buffer_size() > transport.get_write_buffer_limits()[1]


async def _start_stall_rig(
    start_server: StartServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    **ws_options: Any,
) -> _StallRig:
    server_side: dict[str, ServerConnection] = {}

    def _instrumented_ws_serve(
        handler: Any, host: str, port: int, **kwargs: Any
    ) -> AbstractAsyncContextManager[Server]:
        async def _handler(ws: ServerConnection) -> None:
            ws.transport.get_extra_info("socket").setsockopt(
                socket.SOL_SOCKET, socket.SO_SNDBUF, _SNDBUF
            )
            if ws.request is not None:
                server_side[ws.request.path] = ws
            await handler(ws)

        return real_ws_serve(_handler, host, port, **kwargs, **ws_options)

    monkeypatch.setattr(server_mod, "_ws_serve", _instrumented_ws_serve)
    store = SessionStore.open(tmp_path / "sessions.db")
    _, port = await start_server(root=tmp_path, store=store)
    return _StallRig(port, store, tmp_path / "recordings", server_side)


async def _connect_stalled(rig: _StallRig) -> ClientConnection:
    """A consumer that stops draining its socket almost at once: a small
    receive buffer, client-side flow control at one message, and no reads.
    Stands in for a frozen or suspended client process."""
    loop = asyncio.get_running_loop()
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.setsockopt(socket.SOL_SOCKET, socket.SO_RCVBUF, 4096)
    sock.setblocking(False)
    await loop.sock_connect(sock, ("127.0.0.1", rig.port))
    stalled = await connect(rig.url("/stalled"), sock=sock, max_queue=1, compression=None)
    await _barrier(stalled, "stalled-registered")  # it is in the fan-out set now
    return stalled


def _fill_payloads() -> list[dict[str, Any]]:
    return [_payload(i, pad=_FILL_PAD) for i in range(_FILL_EVENTS)]


async def _produce(producer: ClientConnection, sid: str, payloads: list[dict[str, Any]]) -> None:
    await producer.send(_session_start(sid))
    for p in payloads:
        await producer.send(_event(p))
    await producer.send(_session_end(sid, len(payloads)))


async def _require_stall(rig: _StallRig) -> None:
    if not await _wait_until(rig.stall_engaged, timeout=5.0):
        pytest.skip("could not back up the stalled consumer's socket on this runner")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T6-3: broadcast awaits each consumer's send in turn inside the producer's "
        "receive loop, so one consumer that stops reading freezes every other "
        "consumer, the ring buffer and the recording (docs/test-campaigns/phase-12.md)"
    ),
)
async def test_a_stalled_consumer_does_not_block_other_consumers_or_the_recording(
    start_server: StartServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``broadcast`` does ``await ws.send(raw)`` per consumer, and websockets'
    ``send`` waits in ``drain()`` while that consumer's transport is over its
    high-water mark. With one consumer no longer reading, the producer's
    receive loop parks there: the healthy consumer stops receiving
    mid-session, nothing more reaches the ring buffer or the recording, and
    the producer is back-pressured in turn (``TraceStreamSender``'s documented
    drop-newest policy then discards events past 100,000 in flight — not
    exercised here). Nothing ends the stall
    while the stuck client keeps its TCP connection open: the keepalive's own
    ping blocks in the same ``drain()``, so its timeout is never armed.

    Expected: the healthy consumer, the recording and a late joiner's ring
    buffer replay all get the whole session. Observed: the healthy consumer
    stalls after a handful of events and the 3 s wait times out."""
    rig = await _start_stall_rig(start_server, tmp_path, monkeypatch)
    payloads = _fill_payloads()

    async with connect(rig.url("/healthy"), max_queue=None) as healthy:
        await _barrier(healthy, "healthy-registered")
        stalled = await _connect_stalled(rig)
        producer = await connect(rig.url("/producer"))
        produce = asyncio.create_task(_produce(producer, "fan-out", payloads))
        try:
            await _require_stall(rig)

            got = await asyncio.wait_for(_collect_session(healthy), timeout=3.0)
            assert [m["payload"] for m in got[1:-1]] == payloads

            await _wait_for_row(rig.store, "fan-out", timeout=3.0)
            _assert_recorded(rig.store, rig.recordings, "fan-out", payloads)

            async with connect(rig.url("/late"), max_queue=None) as late:
                history = await asyncio.wait_for(_collect_session(late), timeout=3.0)
            assert [m["payload"] for m in history[1:-1]] == payloads
        finally:
            stalled.transport.abort()
            await asyncio.wait_for(produce, timeout=10.0)
            await producer.close()


async def test_ingest_recovers_intact_when_a_stalled_consumer_disconnects(
    start_server: StartServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The other half of the stall above, and not a defect: once the stuck
    consumer goes away, the ``send`` blocked on it fails with
    ``ConnectionClosed``, ``broadcast`` swallows that for the one dead
    consumer, and the producer's loop resumes where it stopped. Nothing is
    lost, reordered or duplicated: the healthy consumer and the recording
    both get the whole session in order."""
    rig = await _start_stall_rig(start_server, tmp_path, monkeypatch)
    payloads = _fill_payloads()

    async with connect(rig.url("/healthy"), max_queue=None) as healthy:
        await _barrier(healthy, "healthy-registered")
        stalled = await _connect_stalled(rig)
        async with connect(rig.url("/producer")) as producer:
            produce = asyncio.create_task(_produce(producer, "recovers", payloads))
            await _require_stall(rig)

            stalled.transport.abort()
            got = await asyncio.wait_for(_collect_session(healthy), timeout=10.0)
            await asyncio.wait_for(produce, timeout=10.0)
            await _barrier(producer, "recorded")

    assert [m["type"] for m in got] == (
        ["trace_session_start"] + ["trace_event"] * len(payloads) + ["trace_session_end"]
    )
    assert [m["payload"] for m in got[1:-1]] == payloads
    _assert_recorded(rig.store, rig.recordings, "recovers", payloads)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T6-3: while a stalled consumer blocks the producer's receive loop, the "
        "producer's pongs go unread and the keepalive closes the healthy producer "
        "(1011) instead of the stuck consumer (docs/test-campaigns/phase-12.md)"
    ),
)
async def test_a_stalled_consumer_does_not_get_the_producer_disconnected(
    start_server: StartServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """With the loop parked in ``broadcast``, the server stops reading the
    producer once 16 messages are queued, so the producer's keepalive pongs
    are never read either. After ``ping_interval + ping_timeout`` (40 s by
    default; 0.2 s + 0.2 s here) the server closes the *healthy producer*
    with 1011 "keepalive ping timeout", while the stuck consumer — whose ping
    is itself blocked in ``drain()`` — stays connected. Whatever the producer
    had not yet got onto the server is lost to every consumer and to the
    recording, which is later finalized as a short session: with an
    incompressible payload, 18 of 64 events on the probing machine.

    Expected: the producer outlives a 2 s stall. Observed: it is closed
    within about half a second."""
    rig = await _start_stall_rig(
        start_server, tmp_path, monkeypatch, ping_interval=0.2, ping_timeout=0.2
    )
    stalled = await _connect_stalled(rig)
    producer = await connect(rig.url("/producer"), max_queue=None)
    produce = asyncio.create_task(_produce(producer, "keepalive", _fill_payloads()))
    try:
        await _require_stall(rig)
        with pytest.raises(TimeoutError):
            await asyncio.wait_for(producer.wait_closed(), timeout=2.0)
    finally:
        stalled.transport.abort()
        await asyncio.gather(produce, return_exceptions=True)
        await producer.close()


# ---------------------------------------------------------------------------
# T7-5: two producers, two connections, one store
# ---------------------------------------------------------------------------


async def test_two_interleaved_producers_record_two_intact_sessions(
    start_server: StartServer, tmp_path: Path
) -> None:
    """Each connection has its own receive loop and its own ``RecordingSink``;
    the ring buffer, the fan-out set, the store and the recordings directory
    are shared. Strict alternation (a ping barrier after every message makes
    the server's processing order exactly the order below) exercises every
    shared piece at every step: both sessions open at once, events
    interleaved one-for-one, one session ending while the other is mid-stream.
    Each recording must hold exactly its own events, in order."""
    store = SessionStore.open(tmp_path / "sessions.db")
    _, port = await start_server(root=tmp_path, store=store)
    url = f"ws://127.0.0.1:{port}"
    ones = [_payload(i, prefix="one") for i in range(12)]
    twos = [_payload(i, prefix="two") for i in range(8)]

    async with connect(url) as p1, connect(url) as p2:

        async def step(ws: ClientConnection, raw: str) -> None:
            await ws.send(raw)
            await _barrier(ws, "step")

        await step(p1, _session_start("one"))
        await step(p2, _session_start("two"))
        for a, b in zip(ones, twos, strict=False):
            await step(p1, _event(a))
            await step(p2, _event(b))
        await step(p2, _session_end("two", len(twos)))  # two ends mid-way through one
        for a in ones[len(twos) :]:
            await step(p1, _event(a))
        await step(p1, _session_end("one", len(ones)))

    recordings = tmp_path / "recordings"
    _assert_recorded(store, recordings, "one", ones)
    _assert_recorded(store, recordings, "two", twos)


@pytest.mark.parametrize(
    ("producers", "events"),
    [
        (2, 200),
        pytest.param(8, 2_000, marks=pytest.mark.hammer),
    ],
)
async def test_free_running_producers_record_intact_sessions(
    start_server: StartServer, tmp_path: Path, producers: int, events: int
) -> None:
    """The same property with no barriers: every producer streams flat out at
    once, so how their messages interleave is up to the event loop. A healthy
    consumer is connected throughout, so every message is also fanned out.

    Every producer is also a consumer of every other producer's broadcast, so
    each one keeps draining its socket (``max_queue=None``), as grackle's own
    ``TraceStreamSender`` does with its ``_recv_drain`` task; one that stopped
    reading would be a stalled consumer to the others (see the slow-consumer
    xfails above). At this volume the socket buffers happen to absorb it
    either way. The hammer's barrier timeout is long because the server and
    all nine clients share this one event loop: at 16,000 events a single
    ``recv`` can wait several seconds on nothing but CPU."""
    store = SessionStore.open(tmp_path / "sessions.db")
    _, port = await start_server(root=tmp_path, store=store)
    url = f"ws://127.0.0.1:{port}"
    sessions = {
        f"p{k}": [_payload(i, prefix=f"p{k}") for i in range(events)] for k in range(producers)
    }

    async def run(sid: str, payloads: list[dict[str, Any]]) -> None:
        async with connect(url, max_queue=None) as producer:
            await _produce(producer, sid, payloads)
            await _barrier(producer, "done", timeout=60.0)

    async with connect(url, max_queue=None) as consumer:
        await _barrier(consumer, "consumer-ready")
        await asyncio.gather(*(run(sid, p) for sid, p in sessions.items()))

    for sid, payloads in sessions.items():
        _assert_recorded(store, tmp_path / "recordings", sid, payloads)


# The websockets client's close_timeout during the replay below. Production
# uses the library default, 10 s; a short one gives the same outcome sooner.
_REPLAY_CLOSE_TIMEOUT = 0.5


@pytest.mark.parametrize(
    "recent_session",
    [
        pytest.param(False, id="empty-ring-buffer"),
        pytest.param(
            True,
            id="recent-session-in-ring-buffer",
            marks=pytest.mark.xfail(
                strict=True,
                reason=(
                    "T6-3: grackle trace --connect's post-run replay never reads its "
                    "socket, so once the server has pushed it a recent session's "
                    "ring-buffer history it cannot see the server's close frame, and "
                    "every such run hangs for the 10 s close_timeout and ends 1006 "
                    "(docs/test-campaigns/phase-12.md)"
                ),
            ),
        ),
    ],
)
async def test_a_post_run_connect_replay_closes_cleanly(
    start_server: StartServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    recent_session: bool,
) -> None:
    """Found while writing the T7-5 hammer. ``grackle trace SCRIPT --connect``
    without ``--stream`` replays the finished trace through
    ``cli._stream_events_to_server``, which only ever sends. But the server
    treats every connection alike: on connect it pushes the static graph and
    the ring-buffer history (the last 60 s of every producer's messages,
    uncapped by default), and it fans out any other producer's live messages
    to it. After 16 unread messages the client stops reading its socket, so
    the server's reply to its close frame goes unseen: the close handshake
    times out.

    Run end to end with the real CLI, a second ``grackle trace --connect``
    within a minute of the first took 10.12 s instead of 0.13 s (exit 0, both
    sessions recorded). With a larger or less compressible history the
    server's history push to it can block outright, before its receive loop
    starts, and its session is then neither broadcast nor recorded until the
    close times out. ``--stream``'s ``TraceStreamSender`` is immune: it drains
    inbound frames in ``_recv_drain``, whose docstring names this hazard.

    Driven through ``CliRunner`` in a worker thread (as ``test_cli_trace.py``
    does); the client's close_timeout is shortened, and each connection it
    opens is kept, so the close code can be read. Expected: a clean 1000 close.
    The empty-ring-buffer case is the passing control."""
    from click.testing import CliRunner
    from websockets.asyncio import client as ws_client

    from grackle.cli import main

    root = tmp_path / "project"
    root.mkdir()
    script = root / "script.py"
    script.write_text(
        "def f(i):\n    return i * 2\n\n\nfor i in range(20):\n    f(i)\n", encoding="utf-8"
    )
    store = SessionStore.open(tmp_path / "sessions.db")
    _, port = await start_server(root=root, store=store)
    url = f"ws://127.0.0.1:{port}"

    if recent_session:
        async with connect(url, max_queue=None) as earlier:
            await _produce(earlier, "earlier", [_payload(i) for i in range(30)])
            await _barrier(earlier, "earlier-recorded")

    opened: list[ClientConnection] = []
    real_connect = ws_client.connect

    @contextlib.asynccontextmanager
    async def _observed_connect(uri: str, **kwargs: Any) -> AsyncIterator[ClientConnection]:
        async with real_connect(uri, close_timeout=_REPLAY_CLOSE_TIMEOUT, **kwargs) as ws:
            opened.append(ws)
            yield ws

    monkeypatch.setattr(ws_client, "connect", _observed_connect)
    result = await asyncio.get_running_loop().run_in_executor(
        None,
        lambda: CliRunner().invoke(
            main, ["trace", str(script), "--root", str(root), "--connect", url]
        ),
    )

    assert result.exit_code == 0, result.output
    assert len(opened) == 1
    assert opened[0].close_code == CloseCode.NORMAL_CLOSURE


# ---------------------------------------------------------------------------
# T10-1: a server-produced recording's bytes
# ---------------------------------------------------------------------------


async def test_a_live_recording_is_lf_only_and_byte_identical_to_the_cli_writer(
    start_server: StartServer, tmp_path: Path
) -> None:
    """Until now a recording's line endings were covered only transitively,
    through the writer it shares with ``grackle trace -o``. Asserted here on a
    real recorded session, from a producer whose frames are pretty-printed with
    CRLF line breaks and whose values carry non-ASCII text, an escaped CR LF
    and a raw U+2028: the recording is a re-serialization of each payload, not
    a copy of the frame, so it must hold no CR byte at all and be byte-for-byte
    what ``write_jsonl`` writes for the same events."""
    store = SessionStore.open(tmp_path / "sessions.db")
    _, port = await start_server(root=tmp_path, store=store)
    payloads = [
        {
            **_payload(i, prefix="pkg/módulo"),
            "metadata": {"note": "line one\r\nline two", "sep": " ", "cjk": "漢字"},
        }
        for i in range(4)
    ]

    def crlf_frame(kind: str, payload: dict[str, Any]) -> str:
        envelope = {"id": "crlf", "type": kind, "payload": payload}
        return json.dumps(envelope, indent=1, ensure_ascii=False).replace("\n", "\r\n")

    async with connect(f"ws://127.0.0.1:{port}") as producer:
        await producer.send(
            crlf_frame("trace_session_start", {"session_id": "crlf", "started_ns": 1})
        )
        for p in payloads:
            await producer.send(crlf_frame("trace_event", p))
        await producer.send(crlf_frame("trace_session_end", {"session_id": "crlf", "ended_ns": 2}))
        await _barrier(producer, "recorded")

    recorded = (tmp_path / "recordings" / "crlf.jsonl").read_bytes()
    assert b"\r" not in recorded
    assert recorded == _lf_bytes(payloads)

    cli_output = tmp_path / "cli.jsonl"
    write_jsonl(cast("list[TraceEvent]", payloads), cli_output)
    assert recorded == cli_output.read_bytes()
    assert read_jsonl(tmp_path / "recordings" / "crlf.jsonl") == payloads
