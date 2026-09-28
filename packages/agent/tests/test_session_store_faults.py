"""Fault injection and concurrency for the SQLite session store (campaign C4).

Probes T6-1, T7-3 and T10-3 of ``docs/test-campaigns/phase-12.md``, run
against ``grackle.session_store.SessionStore`` and the two server paths that
use it: the live-recording sink's ``save_session`` and the library's
``session_list_request`` / ``session_load_request``.

Every passing test here is a pin backed by a mutation spec under
``tools/mutation/specs/agent-session-store-*.json`` or
``agent-server-*.json``; every strict ``xfail`` is a confirmed defect,
committed red so a fix has a discriminating test already waiting (the T5
promotion protocol: the fixing PR removes the marker).

**T7-3, the store's lock.** Each store call is serialized through one
``threading.Lock`` because a ``sqlite3.Connection`` is not safe under truly
concurrent calls. Without the lock a 4-writer, 25-save hammer loses about a
quarter of its rows to ``InterfaceError`` / ``SystemError`` /
"cannot start a transaction within a transaction". The deterministic tests
below do not rely on the scheduler to find that race: a probed connection
parks the first caller inside SQLite and checks whether a second caller can
get in beside it.
"""

from __future__ import annotations

import asyncio
import contextlib
import errno
import json
import os
import sqlite3
import sys
import threading
import time
import uuid
from typing import TYPE_CHECKING, Any, cast

import pytest
from websockets.asyncio.client import connect
from websockets.exceptions import ConnectionClosed

from grackle.python_runtime.recording_sink import RecordingSink
from grackle.session_store import SessionMeta, SessionStore

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path

    from conftest import StartServer


# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------


def _meta(
    session_id: str,
    *,
    started_ns: int = 1,
    source_path: str = "/recordings/x.jsonl",
    event_count: int = 1,
    language: str = "python",
) -> SessionMeta:
    return SessionMeta(
        id=session_id,
        label=f"label {session_id}",
        started_ns=started_ns,
        ended_ns=started_ns + 1,
        source_path=source_path,
        event_count=event_count,
        language=language,
    )


def _stored_row(db: Path, session_id: str) -> SessionMeta | None:
    """Read one row through a fresh store (the server's own store is closed)."""
    store = SessionStore.open(db)
    try:
        return store.get_session(session_id)
    finally:
        store.close()


# ---------------------------------------------------------------------------
# T7-3 — the lock serializes every call on the connection
# ---------------------------------------------------------------------------

# How long the first caller waits inside SQLite for a second one to show up.
# With the store's lock held, a second caller cannot arrive, so this is the
# cost of each passing case; without it, the second arrives in microseconds.
_ARRIVAL_GRACE_S = 0.3


class _OverlapProbe:
    """Measures how many store calls are inside the connection at once.

    The first call to enter *parks*: it waits up to ``_ARRIVAL_GRACE_S`` for a
    second call to enter beside it, and if one does, lets that call finish
    first. That forces the worst interleaving deterministically — the
    contender's call runs to completion while the first call sits between
    entering the store and touching SQLite.
    """

    def __init__(self) -> None:
        self._mu = threading.Lock()
        self._inside = 0
        self._parked_thread: int | None = None
        self.max_inside = 0
        self.first_parked = threading.Event()
        self.contender_entered = threading.Event()
        self.contender_left = threading.Event()

    def enter(self) -> None:
        with self._mu:
            self._inside += 1
            self.max_inside = max(self.max_inside, self._inside)
            if self._inside > 1:
                self.contender_entered.set()
            park = self._parked_thread is None
            if park:
                self._parked_thread = threading.get_ident()
        if park:
            self.first_parked.set()
            if self.contender_entered.wait(_ARRIVAL_GRACE_S):
                self.contender_left.wait(5.0)

    def leave(self) -> None:
        with self._mu:
            self._inside -= 1
            if threading.get_ident() != self._parked_thread:
                self.contender_left.set()


class _ProbedConnection:
    """A ``sqlite3.Connection`` stand-in routing each call through a probe."""

    def __init__(self, real: sqlite3.Connection, probe: _OverlapProbe) -> None:
        self._real = real
        self._probe = probe

    def execute(self, sql: str, params: Sequence[object] = ()) -> sqlite3.Cursor:
        self._probe.enter()
        try:
            return self._real.execute(sql, params)
        finally:
            self._probe.leave()

    def commit(self) -> None:
        self._probe.enter()
        try:
            self._real.commit()
        finally:
            self._probe.leave()

    def close(self) -> None:
        self._probe.enter()
        try:
            self._real.close()
        finally:
            self._probe.leave()


def _probed_store(db: Path) -> tuple[SessionStore, _OverlapProbe]:
    SessionStore.open(db).close()  # create the schema the normal way
    probe = _OverlapProbe()
    real = sqlite3.connect(str(db), check_same_thread=False)
    conn = cast("sqlite3.Connection", _ProbedConnection(real, probe))
    return SessionStore(conn, db), probe


def _run_catching(fn: Callable[[], object], errors: list[BaseException]) -> None:
    try:
        fn()
    except BaseException as exc:  # recorded and asserted on by the test
        errors.append(exc)


_CONTENDERS: dict[str, Callable[[SessionStore], object]] = {
    "save": lambda s: s.save_session(_meta("contender")),
    "list": lambda s: s.list_sessions(),
    "get": lambda s: s.get_session("first"),
    "close": lambda s: s.close(),
}


@pytest.mark.parametrize("contender", sorted(_CONTENDERS))
def test_no_store_call_runs_beside_an_in_flight_save(tmp_path: Path, contender: str) -> None:
    """T7-3: while one ``save_session`` is inside SQLite, no other store call
    — save, list, get or close — may enter the connection. This is the
    module docstring's contract, and ADR-0020's: "every access ... is
    serialized through a threading.Lock"."""
    store, probe = _probed_store(tmp_path / "sessions.db")
    errors: list[BaseException] = []

    first = threading.Thread(
        target=_run_catching, args=(lambda: store.save_session(_meta("first")), errors)
    )
    first.start()
    assert probe.first_parked.wait(5.0), "the first save never reached the connection"
    second = threading.Thread(
        target=_run_catching, args=(lambda: _CONTENDERS[contender](store), errors)
    )
    second.start()
    first.join(10.0)
    second.join(10.0)
    assert not first.is_alive()
    assert not second.is_alive()
    store.close()

    assert probe.max_inside == 1, (
        f"a {contender!r} call entered the SQLite connection while a save was "
        "still inside it — the store's lock is not serializing access"
    )
    assert errors == []


def test_close_waits_for_an_in_flight_save(tmp_path: Path) -> None:
    """T7-3/T6-1: ``serve()`` closes the store in its ``finally`` while a
    recording's ``save_session`` may still be running on an executor thread.
    The lock is what makes that close wait for the save instead of pulling the
    connection out from under it — so the row lands."""
    db = tmp_path / "sessions.db"
    store, probe = _probed_store(db)
    save_errors: list[BaseException] = []
    close_errors: list[BaseException] = []

    saver = threading.Thread(
        target=_run_catching, args=(lambda: store.save_session(_meta("in-flight")), save_errors)
    )
    saver.start()
    assert probe.first_parked.wait(5.0)
    closer = threading.Thread(target=_run_catching, args=(store.close, close_errors))
    closer.start()
    saver.join(10.0)
    closer.join(10.0)

    assert save_errors == [], f"the in-flight save failed: {save_errors!r}"
    assert close_errors == []
    assert _stored_row(db, "in-flight") == _meta("in-flight")


# -- hammers ----------------------------------------------------------------


def _hammer_meta(writer: int, n: int) -> SessionMeta:
    return SessionMeta(
        id=f"w{writer}-{n}",
        label=f"writer {writer}",
        started_ns=writer * 1_000_000 + n,
        ended_ns=n,
        source_path=f"/recordings/w{writer}/{n}.jsonl",
        event_count=n,
        language="python",
    )


def _hammer_meta_for(session_id: str) -> SessionMeta:
    writer, n = session_id[1:].split("-")
    return _hammer_meta(int(writer), int(n))


def _hammer_one_store(db: Path, *, writers: int, saves_per_writer: int, readers: int) -> None:
    """N writer threads and M reader threads share one store; no call may
    fail, every read row must be one some writer actually wrote, and every
    write must be on disk afterwards."""
    store = SessionStore.open(db)
    errors: list[str] = []
    torn: list[SessionMeta] = []
    start = threading.Barrier(writers + readers, timeout=10.0)
    writers_done = threading.Event()

    def writer(w: int) -> None:
        start.wait()
        for n in range(saves_per_writer):
            try:
                store.save_session(_hammer_meta(w, n))
            except Exception as exc:
                errors.append(f"save w{w}-{n}: {exc!r}")

    def reader() -> None:
        start.wait()
        while True:
            done = writers_done.is_set()  # sampled first: one full pass after
            try:
                torn.extend(r for r in store.list_sessions() if r != _hammer_meta_for(r.id))
                row = store.get_session("w0-0")
                if row is not None and row != _hammer_meta(0, 0):
                    torn.append(row)
            except Exception as exc:
                errors.append(f"read: {exc!r}")
            if done:
                return

    writer_threads = [threading.Thread(target=writer, args=(w,)) for w in range(writers)]
    reader_threads = [threading.Thread(target=reader) for _ in range(readers)]
    for t in writer_threads + reader_threads:
        t.start()
    for t in writer_threads:
        t.join(120.0)
    writers_done.set()
    for t in reader_threads:
        t.join(120.0)
    store.close()

    assert errors == [], f"{len(errors)} store calls failed, e.g. {errors[:3]}"
    assert torn == [], f"read back rows no writer wrote: {torn[:3]}"
    fresh = SessionStore.open(db)
    ids = {m.id for m in fresh.list_sessions()}
    fresh.close()
    expected = {f"w{w}-{n}" for w in range(writers) for n in range(saves_per_writer)}
    assert ids == expected, f"{len(expected - ids)} of {len(expected)} saves never reached disk"


def test_writer_reader_hammer_small(tmp_path: Path) -> None:
    """T7-3: the gate-sized sibling of the hammer below (same body)."""
    _hammer_one_store(tmp_path / "sessions.db", writers=4, saves_per_writer=25, readers=2)


@pytest.mark.hammer
@pytest.mark.parametrize("round_", range(5))
def test_writer_reader_hammer(tmp_path: Path, round_: int) -> None:
    """T7-3 hammer (nightly): 16 writers and 4 readers on one store."""
    _hammer_one_store(tmp_path / "sessions.db", writers=16, saves_per_writer=200, readers=4)


# ---------------------------------------------------------------------------
# T6-1 — two stores on one database (two `serve --store` sharing a library)
# ---------------------------------------------------------------------------


def test_save_waits_out_another_connections_write_lock(tmp_path: Path) -> None:
    """T6-1 (locked db): a second process mid-write holds SQLite's write lock.
    ``save_session`` must wait for it (sqlite3's busy timeout), not fail with
    "database is locked" the moment it collides.

    Observed beyond the timeout (not pinned — it takes 5 s): a lock held past
    sqlite3's default 5.0 s makes ``save_session`` raise
    ``OperationalError: database is locked`` after ~5.2 s. Reads and
    ``SessionStore.open`` are not blocked by a writer at all (WAL)."""
    db = tmp_path / "sessions.db"
    store = SessionStore.open(db)
    blocker = sqlite3.connect(str(db), isolation_level=None, check_same_thread=False)
    blocker.execute("BEGIN IMMEDIATE")  # take the write lock and sit on it
    release = threading.Timer(0.3, lambda: blocker.execute("ROLLBACK"))
    release.start()
    try:
        t0 = time.monotonic()
        store.save_session(_meta("patient"))
        waited = time.monotonic() - t0
    finally:
        release.join()
        blocker.close()
    assert store.get_session("patient") == _meta("patient")
    store.close()
    # Discriminating-power companion: the save really did collide with the
    # lock (otherwise this test would pass whatever the busy handling was).
    assert waited >= 0.2, f"save_session returned after {waited:.3f}s — it never hit the lock"


def _hammer_two_stores(db: Path, *, stores: int, writers_per_store: int, saves: int) -> None:
    handles = [SessionStore.open(db) for _ in range(stores)]
    errors: list[str] = []
    start = threading.Barrier(stores * writers_per_store, timeout=10.0)

    def writer(s: int, w: int) -> None:
        start.wait()
        for n in range(saves):
            try:
                handles[s].save_session(_meta(f"s{s}-w{w}-{n}", started_ns=n))
            except Exception as exc:
                errors.append(f"s{s}-w{w}-{n}: {exc!r}")

    threads = [
        threading.Thread(target=writer, args=(s, w))
        for s in range(stores)
        for w in range(writers_per_store)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(120.0)
    for h in handles:
        h.close()

    assert errors == [], f"{len(errors)} saves failed, e.g. {errors[:3]}"
    fresh = SessionStore.open(db)
    count = len(fresh.list_sessions())
    fresh.close()
    assert count == stores * writers_per_store * saves


def test_two_stores_one_database_small(tmp_path: Path) -> None:
    """T6-1 (concurrent writers): two ``SessionStore`` instances — two
    connections, as two ``serve --store`` processes sharing one library would
    have — writing at once lose nothing."""
    _hammer_two_stores(tmp_path / "sessions.db", stores=2, writers_per_store=2, saves=25)


@pytest.mark.hammer
def test_two_stores_one_database(tmp_path: Path) -> None:
    """T6-1 hammer (nightly): four stores, four writers each."""
    _hammer_two_stores(tmp_path / "sessions.db", stores=4, writers_per_store=4, saves=200)


# ---------------------------------------------------------------------------
# T6-1 — use after close
# ---------------------------------------------------------------------------


def test_a_closed_store_refuses_every_call(tmp_path: Path) -> None:
    """T6-1 (use-after-close): once closed, the store refuses loudly —
    ``sqlite3.ProgrammingError`` — rather than accepting a write it can no
    longer make durable. ``RecordingSink`` relies on that raise to log
    "file written but not registered"; a silent no-op would hide the loss.
    A second ``close()`` is a no-op."""
    db = tmp_path / "sessions.db"
    store = SessionStore.open(db)
    store.save_session(_meta("before-close"))
    store.close()
    store.close()

    with pytest.raises(sqlite3.ProgrammingError):
        store.save_session(_meta("after-close"))
    with pytest.raises(sqlite3.ProgrammingError):
        store.list_sessions()
    with pytest.raises(sqlite3.ProgrammingError):
        store.get_session("before-close")

    assert _stored_row(db, "before-close") == _meta("before-close")
    assert _stored_row(db, "after-close") is None


# ---------------------------------------------------------------------------
# Server helpers — a live producer, and the library's request/reply channel
# ---------------------------------------------------------------------------


def _session_start(session_id: str) -> str:
    return json.dumps(
        {
            "id": f"start-{session_id}",
            "type": "trace_session_start",
            "payload": {"session_id": session_id, "started_ns": 1_000, "source": "live"},
        }
    )


def _trace_event(i: int) -> str:
    return json.dumps(
        {
            "id": f"ev-{i}",
            "type": "trace_event",
            "payload": {
                "event": "call",
                "node_id": f"mod.py:fn_{i}",
                "ts_ns": i * 1_000_000,
                "thread_id": 1,
                "frame_depth": 0,
                "metadata": {},
            },
        }
    )


def _request(type_: str, payload: dict[str, Any] | None = None) -> str:
    return json.dumps({"id": str(uuid.uuid4()), "type": type_, "payload": payload or {}})


async def _recv_until(
    ws: Any,
    pred: Callable[[dict[str, Any]], bool],
    timeout: float = 5.0,
    seen: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Receive until a message matches *pred*; every message received on the
    way, the match included, is appended to *seen* when given."""
    deadline = asyncio.get_running_loop().time() + timeout
    while True:
        remaining = deadline - asyncio.get_running_loop().time()
        if remaining <= 0:
            raise TimeoutError("no matching message")
        msg = cast("dict[str, Any]", json.loads(await asyncio.wait_for(ws.recv(), remaining)))
        if seen is not None:
            seen.append(msg)
        if pred(msg):
            return msg


async def _pong(ws: Any) -> None:
    """Round-trip a ping: every message sent before it has been processed."""
    await ws.send(_request("ping"))
    await _recv_until(ws, lambda m: m["type"] == "pong")


async def _collect_for(ws: Any, seconds: float) -> list[dict[str, Any]]:
    msgs: list[dict[str, Any]] = []
    with contextlib.suppress(TimeoutError):
        async with asyncio.timeout(seconds):
            while True:
                msgs.append(json.loads(await ws.recv()))
    return msgs


def _starts_for(msgs: list[dict[str, Any]], session_id: str) -> list[dict[str, Any]]:
    return [
        m
        for m in msgs
        if m["type"] == "trace_session_start" and m["payload"].get("session_id") == session_id
    ]


def _write_trace(path: Path, events: int) -> None:
    lines = [
        json.dumps(
            {
                "event": "call",
                "node_id": f"mod.py:fn_{i}",
                "ts_ns": i,
                "thread_id": 1,
                "frame_depth": 0,
                "metadata": {},
            }
        )
        for i in range(events)
    ]
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


# ---------------------------------------------------------------------------
# T6-1 — shutdown vs. an in-flight recording finalize
# ---------------------------------------------------------------------------

_SLOW_SAVE_S = 0.3


def _slow_saves(store: SessionStore, monkeypatch: pytest.MonkeyPatch) -> None:
    """Delay every ``save_session`` before it reaches the store — a slow disk,
    or an executor busy with other work. The delay sits *before* the lock,
    which is exactly the window a concurrent ``close()`` can win."""
    real_save = store.save_session

    def slow_save(meta: SessionMeta) -> None:
        time.sleep(_SLOW_SAVE_S)
        real_save(meta)

    monkeypatch.setattr(store, "save_session", slow_save)


async def test_shutdown_waits_for_a_slow_in_flight_save(
    start_server: StartServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """T6-1 (shutdown race), the ordinary case: one cancellation of
    ``serve()`` with a producer mid-session. The websockets server waits for
    every connection handler before ``serve()``'s ``finally`` closes the
    store, and the handler waits for the recording's shielded finalize — so
    even a slow ``save_session`` lands. ``test_server_shutdown_cancel_finalizes``
    covers this with an instant save, where a finalize that is *not* awaited
    still usually wins the race to the store; the delay here removes the luck."""
    db = tmp_path / "sessions.db"
    store = SessionStore.open(db)
    _slow_saves(store, monkeypatch)
    task, port = await start_server(root=tmp_path, store=store)

    async with connect(f"ws://127.0.0.1:{port}") as producer:
        await producer.send(_session_start("slow-save"))
        for i in range(3):
            await producer.send(_trace_event(i))
        await _pong(producer)
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task

    row = _stored_row(db, "slow-save")
    assert row is not None, "shutdown closed the store before the recording's save ran"
    assert row.event_count == 3
    assert (tmp_path / "recordings" / "slow-save.jsonl").exists()


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T6-1: a second interrupt during shutdown lets serve() close the store "
        "under a recording's in-flight finalize — the .jsonl is kept but its "
        "row is dropped, and nothing re-registers it "
        "(docs/test-campaigns/phase-12.md)"
    ),
)
async def test_interrupted_shutdown_still_registers_the_finalized_recording(
    start_server: StartServer, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The ``finally`` finalize is shielded so that shutdown "still completes
    the close+rename+save_session sequence" (``server.py``; ADR-0020's 9.3
    amendment). But ``serve()`` itself only waits for connection handlers via
    the websockets server's drain; if ``serve()`` is cancelled again while
    draining — asyncio.run does exactly that on a second Ctrl-C — its
    ``finally`` closes the store at once, and the save lands on a closed
    connection. ``RecordingSink.finalize`` swallows the ``ProgrammingError``.

    Reproduced against the real CLI too: ``grackle serve --store`` with a
    producer mid-session that is slow to answer the close handshake (the
    server then waits up to websockets' 10 s close timeout), Ctrl-C, then
    Ctrl-C again after 1 s — the process exits with ``recordings/<id>.jsonl``
    on disk and no row. A single Ctrl-C records the row (after the 10 s)."""
    finalize_started = asyncio.Event()
    finalize_done = asyncio.Event()
    real_finalize = RecordingSink.finalize

    async def observed_finalize(self: RecordingSink) -> None:
        finalize_started.set()
        try:
            await real_finalize(self)
        finally:
            finalize_done.set()

    monkeypatch.setattr(RecordingSink, "finalize", observed_finalize)
    db = tmp_path / "sessions.db"
    store = SessionStore.open(db)
    _slow_saves(store, monkeypatch)
    task, port = await start_server(root=tmp_path, store=store)

    async with connect(f"ws://127.0.0.1:{port}") as producer:
        await producer.send(_session_start("interrupted"))
        for i in range(3):
            await producer.send(_trace_event(i))
        await _pong(producer)
        task.cancel()  # first interrupt: serve() starts draining connections
        await asyncio.wait_for(finalize_started.wait(), 5.0)
        task.cancel()  # second interrupt, while the recording's save is in flight
        with contextlib.suppress(asyncio.CancelledError):
            await task
    await asyncio.wait_for(finalize_done.wait(), 5.0)

    assert (tmp_path / "recordings" / "interrupted.jsonl").exists()
    row = _stored_row(db, "interrupted")
    assert row is not None, (
        "recordings/interrupted.jsonl was finalized but never registered — "
        "invisible to the session library and to `grackle learn --from-store`"
    )
    assert row.event_count == 3


# ---------------------------------------------------------------------------
# T6-1 — a failing store read must not take the client's connection with it
# ---------------------------------------------------------------------------


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T6-1: a SessionStore read error in session_list_request / "
        "session_load_request escapes the receive loop and the server drops "
        "the client's WebSocket with 1011 (docs/test-campaigns/phase-12.md)"
    ),
)
@pytest.mark.parametrize(
    ("request_type", "store_method"),
    [("session_list_request", "list_sessions"), ("session_load_request", "get_session")],
)
async def test_a_store_read_error_does_not_drop_the_connection(
    start_server: StartServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    request_type: str,
    store_method: str,
) -> None:
    """``trace_query_request`` catches its errors and replies; the two library
    requests do not. Real triggers, both reproduced: a partially corrupt
    database (open and list succeed, ``get_session``/``save_session`` raise
    ``DatabaseError: database disk image is malformed``), and a ``sessions``
    table missing a column (T10-3 — list raises ``OperationalError: no such
    column``). The frontend's ``SessionLibraryPanel`` sends
    ``session_list_request`` on every connect, so a store that fails the list
    drops every UI connection moments after it opens."""

    def broken(*_args: object) -> object:
        raise sqlite3.DatabaseError("database disk image is malformed")

    store = SessionStore.open(tmp_path / "sessions.db")
    monkeypatch.setattr(store, store_method, broken)
    _, port = await start_server(root=tmp_path, store=store)

    async with connect(f"ws://127.0.0.1:{port}") as ws:
        await ws.send(_request(request_type, {"session_id": "any"}))
        try:
            await _pong(ws)
        except ConnectionClosed as exc:
            pytest.fail(f"the server dropped the connection after {request_type}: {exc}")


# ---------------------------------------------------------------------------
# T6-1 — a stored row whose source_path is missing or not a regular file
# ---------------------------------------------------------------------------


@pytest.fixture
async def library_server(
    start_server: StartServer, tmp_path: Path
) -> tuple[int, SessionStore, Path]:
    """A server whose store already holds one good, loadable session."""
    trace = tmp_path / "good.jsonl"
    _write_trace(trace, 2)
    store = SessionStore.open(tmp_path / "sessions.db")
    store.save_session(_meta("good", source_path=str(trace), event_count=2))
    _, port = await start_server(root=tmp_path, store=store)
    return port, store, tmp_path


async def _load_then_barrier(ws: Any, session_id: str) -> list[dict[str, Any]]:
    """Load *session_id*, then load the good session and wait until it has
    replayed; plus a short grace for any stray reply. Loads run as separate
    tasks, so the good session's replay is the barrier that the first load's
    own task has had its turn."""
    await ws.send(_request("session_load_request", {"session_id": session_id}))
    await ws.send(_request("session_load_request", {"session_id": "good"}))
    msgs: list[dict[str, Any]] = []
    await _recv_until(
        ws,
        lambda m: m["type"] == "trace_session_end" and m["payload"]["session_id"] == "good",
        seen=msgs,
    )
    msgs.extend(await _collect_for(ws, 0.3))
    return msgs


async def test_loading_a_session_whose_file_is_gone_replays_nothing(
    library_server: tuple[int, SessionStore, Path],
) -> None:
    """T6-1 (missing source_path): a row whose recording was deleted is
    skipped — no replay, and the connection carries on. The ``exists()``
    guard is load-bearing: ``build_seekable`` maps a missing file to an empty
    index, so without the guard the UI would be switched to an empty,
    zero-event session."""
    port, store, tmp_path = library_server
    store.save_session(_meta("gone", source_path=str(tmp_path / "deleted.jsonl")))

    async with connect(f"ws://127.0.0.1:{port}") as ws:
        msgs = await _load_then_barrier(ws, "gone")
        assert _starts_for(msgs, "gone") == []
        await _pong(ws)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T6-1: session_load_request guards source_path with exists(), not "
        "is_file() — a directory loads as an empty session "
        "(docs/test-campaigns/phase-12.md)"
    ),
)
async def test_loading_a_session_whose_source_is_a_directory_replays_nothing(
    library_server: tuple[int, SessionStore, Path],
) -> None:
    """A directory passes ``exists()``; ``build_seekable`` then hits
    ``IsADirectoryError`` (``PermissionError`` on Windows), maps it to an
    empty index, and the server replays a zero-event session — the outcome
    the missing-file guard exists to prevent. An empty ``source_path`` does
    the same: ``Path("")`` is ``.``, the server's working directory."""
    port, store, tmp_path = library_server
    (tmp_path / "a-directory").mkdir()
    store.save_session(_meta("dir", source_path=str(tmp_path / "a-directory")))

    async with connect(f"ws://127.0.0.1:{port}") as ws:
        msgs = await _load_then_barrier(ws, "dir")
    assert _starts_for(msgs, "dir") == [], "a directory was replayed as an empty session"


def _release_fifo_reader_eventually(fifo: Path) -> None:
    """Safety net: open *fifo* for writing (blocking) on a daemon thread, so a
    server thread that opens it late is released instead of hanging the
    interpreter's exit-time executor join."""

    def release() -> None:
        with contextlib.suppress(OSError):
            os.close(os.open(fifo, os.O_WRONLY))

    threading.Thread(target=release, daemon=True).start()


@pytest.mark.skipif(sys.platform == "win32", reason="FIFOs are POSIX-only")
@pytest.mark.xfail(
    strict=True,
    reason=(
        "T6-1: session_load_request guards source_path with exists(), not "
        "is_file() — a FIFO is opened by an executor thread that blocks until "
        "a writer appears (docs/test-campaigns/phase-12.md)"
    ),
)
async def test_loading_a_session_whose_source_is_a_fifo_never_opens_it(
    library_server: tuple[int, SessionStore, Path],
) -> None:
    """``grackle learn --from-store`` already refuses non-regular files for
    exactly this reason (``cli.py``: "could point at a FIFO/device file ...
    can hang or grow memory unboundedly"); the server's load path is the
    unguarded sibling. Each such load parks one default-executor thread in
    ``open()`` until something writes to the FIFO, and shutdown joins that
    thread: against the real CLI, ``grackle serve`` was still running 20 s
    after a Ctrl-C and exited only once the FIFO got a writer. (Not executed,
    by reading: a device such as ``/dev/zero`` passes ``exists()`` too, and
    ``build_seekable`` would read it line by line with no newline arriving.)"""
    if sys.platform == "win32":
        # Already skipped at runtime by the skipif above; this branch exists so
        # `mypy --strict` narrows the rest of the body away on the Windows CI
        # leg, where typeshed has no os.mkfifo / os.O_NONBLOCK (a decorator
        # does not narrow). The same reason test_cli_learn.py gates its mkfifo.
        pytest.skip("FIFOs are POSIX-only")
    port, store, tmp_path = library_server
    fifo = tmp_path / "pipe.jsonl"
    os.mkfifo(fifo)
    store.save_session(_meta("fifo", source_path=str(fifo)))

    fd: int | None = None
    try:
        async with connect(f"ws://127.0.0.1:{port}") as ws:
            await _load_then_barrier(ws, "fifo")
            # A non-blocking open for writing succeeds only if some thread has
            # the FIFO open for reading (else ENXIO).
            deadline = asyncio.get_running_loop().time() + 0.5
            while fd is None and asyncio.get_running_loop().time() < deadline:
                try:
                    fd = os.open(fifo, os.O_WRONLY | os.O_NONBLOCK)
                except OSError as exc:
                    if exc.errno != errno.ENXIO:
                        raise
                    await asyncio.sleep(0.02)
    finally:
        if fd is not None:
            os.write(fd, b"\n")
            os.close(fd)  # the blocked server thread reads EOF and moves on
        else:
            _release_fifo_reader_eventually(fifo)

    assert fd is None, "the server opened the FIFO and a worker thread blocked on it"


# ---------------------------------------------------------------------------
# T10-3 — sessions.db written by a different schema version
# ---------------------------------------------------------------------------

# A database written by a hypothetical *later* grackle: an extra column in
# the middle of the table (Phase 13.0's planned `root`) and one at the end
# with a default. Everything this version knows about is still there.
_NEWER_SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    root TEXT,
    started_ns INTEGER NOT NULL,
    ended_ns INTEGER NOT NULL,
    source_path TEXT NOT NULL,
    event_count INTEGER NOT NULL,
    language TEXT NOT NULL,
    tags TEXT NOT NULL DEFAULT ''
)
"""

# A database written before a column the store now needs existed. (No such
# schema exists in the wild yet — the DDL is unchanged since 8.3 — so this
# is the shape every existing library will have the first time a column is
# added, e.g. Phase 13.0's `root`.)
_OLDER_SCHEMA = """
CREATE TABLE sessions (
    id TEXT PRIMARY KEY,
    label TEXT NOT NULL,
    started_ns INTEGER NOT NULL,
    ended_ns INTEGER NOT NULL,
    source_path TEXT NOT NULL,
    event_count INTEGER NOT NULL
)
"""


def _newer_db(db: Path) -> None:
    conn = sqlite3.connect(db)
    conn.execute(_NEWER_SCHEMA)
    conn.execute(
        "INSERT INTO sessions (id, label, root, started_ns, ended_ns, source_path,"
        " event_count, language, tags) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
        ("from-newer", "label from-newer", "/proj", 5, 6, "/r/newer.jsonl", 7, "go", "x"),
    )
    conn.commit()
    conn.close()


def test_a_newer_schemas_extra_columns_are_tolerated(tmp_path: Path) -> None:
    """T10-3: reads and writes name their columns, so a database carrying
    columns this version does not know — anywhere in the table — reads back
    correctly and accepts new rows (the unknown columns take their defaults)."""
    db = tmp_path / "sessions.db"
    _newer_db(db)
    newer_row = SessionMeta(
        id="from-newer",
        label="label from-newer",
        started_ns=5,
        ended_ns=6,
        source_path="/r/newer.jsonl",
        event_count=7,
        language="go",
    )

    store = SessionStore.open(db)
    assert store.get_session("from-newer") == newer_row
    store.save_session(_meta("from-this-version", started_ns=1))
    assert store.list_sessions() == [newer_row, _meta("from-this-version", started_ns=1)]
    store.close()

    raw = sqlite3.connect(db).execute(
        "SELECT root, tags FROM sessions WHERE id = 'from-this-version'"
    )
    assert raw.fetchone() == (None, "")


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T10-3: save_session's INSERT OR REPLACE deletes and re-inserts the "
        "row, so re-saving a session wipes columns this version does not know "
        "(docs/test-campaigns/phase-12.md)"
    ),
)
def test_resaving_a_session_keeps_a_newer_schemas_columns(tmp_path: Path) -> None:
    """Re-saving an existing id is routine: ``serve --store --trace-source X``
    re-registers X under the same uuid5 id on every start. Against a library
    a newer grackle wrote, that silently resets the newer version's columns
    (here ``root`` → NULL, ``tags`` → its default). An upsert that updates
    only the known columns would leave them intact."""
    db = tmp_path / "sessions.db"
    _newer_db(db)
    store = SessionStore.open(db)
    store.save_session(
        SessionMeta(
            id="from-newer",
            label="relabelled",
            started_ns=5,
            ended_ns=6,
            source_path="/r/newer.jsonl",
            event_count=7,
            language="go",
        )
    )
    store.close()

    raw = sqlite3.connect(db).execute("SELECT label, root, tags FROM sessions")
    assert raw.fetchall() == [("relabelled", "/proj", "x")]


@pytest.mark.xfail(
    strict=True,
    reason=(
        "T10-3: the store has no migration path — a sessions table missing a "
        "column is accepted at open, then every read and write fails "
        "(docs/test-campaigns/phase-12.md)"
    ),
)
def test_an_older_schema_is_usable_after_open(tmp_path: Path) -> None:
    """``CREATE TABLE IF NOT EXISTS`` no-ops over the existing table whatever
    its shape, and ``PRAGMA user_version`` is never set, so nothing notices the
    mismatch until the first query: ``list_sessions``/``get_session`` raise
    ``OperationalError: no such column: language`` and ``save_session`` raises
    ``table sessions has no column named language``. Downstream, the server's
    library requests drop the UI connection (see the store-read-error xfail
    above) and every live recording's row is lost. The accumulated library is
    user data, so the expected behavior is a migration on open."""
    db = tmp_path / "sessions.db"
    conn = sqlite3.connect(db)
    conn.execute(_OLDER_SCHEMA)
    conn.execute(
        "INSERT INTO sessions VALUES (?, ?, ?, ?, ?, ?)",
        ("from-older", "label from-older", 5, 6, "/r/older.jsonl", 7),
    )
    conn.commit()
    conn.close()

    store = SessionStore.open(db)
    try:
        rows = store.list_sessions()
        assert [r.id for r in rows] == ["from-older"]
        older = store.get_session("from-older")
        assert older is not None
        assert (older.label, older.source_path, older.event_count) == (
            "label from-older",
            "/r/older.jsonl",
            7,
        )
        store.save_session(_meta("from-this-version"))
        assert store.get_session("from-this-version") == _meta("from-this-version")
    finally:
        store.close()
