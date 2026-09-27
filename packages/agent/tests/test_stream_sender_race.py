"""Test campaign T7-2 (docs/test-campaigns/phase-12.md): the stream sender's
``_counter_lock``.

``TraceStreamSender._inflight`` is read-modify-written from two threads:
``sink()`` increments it on the traced (producer) thread, and ``_drain_loop()``
decrements it on the sender thread after each send. The lock's own comment says
it exists because, without it, the non-atomic read-modify-write "can lose
decrements and permanently wedge sink() at low caps". Until this file nothing
raced the two, and the lock could be deleted with the suite staying green.

Why these tests widen the window rather than just hammering the real class
(measured on macOS arm64, CPython 3.12.13, 3.13.13 and 3.14.4, GIL builds): an
unlocked ``obj.x += 1`` racing ``obj.x -= 1`` on a plain int attribute lost
**0** updates in 5 x 200k-operation runs under ``sys.setswitchinterval(1e-6)``.
``LOAD_ATTR`` / ``BINARY_OP`` / ``STORE_ATTR`` contain no eval-breaker check, so
a GIL build never hands the GIL over mid-update. The same race through a
property, whose Python-level getter and setter *are* switch points, lost
thousands of updates per run. So the lost decrement is real wherever the update
can be interrupted (a free-threaded build, or any future change that routes the
counter through Python code), but it cannot happen today on the GIL builds CI
runs. These tests reproduce the interruptible case on purpose: a subclass puts
``_inflight`` behind a property, and a wrapper around the sender's own lock
object reports when a second thread finds it held.

The deterministic tests pause one thread between its read and its write. With
the lock in place, the other thread blocks on the lock, the probe sees the
contention, and the paused write lands first. Without it, the other thread
finishes its own update in the gap and the paused write overwrites it. The
mutation specs ``agent-stream-sender-*`` (``tools/mutation/specs/``) remove the
lock outright, and each side of it separately, and all must be killed here.
"""

from __future__ import annotations

import asyncio
import json
import sys
import threading
import time
from typing import TYPE_CHECKING, Any

import pytest
import websockets.asyncio.client

from grackle.python_runtime.stream_sender import TraceStreamSender

if TYPE_CHECKING:
    from collections.abc import Callable

    from grackle.adapters.base import TraceEvent

# Every wait in this file is bounded. A guard that expires is recorded and fails
# the test; nothing here can hang the suite, even under a mutant.
_GUARD_S = 10.0
_SENDER_THREAD = "grackle-stream-sender"


def _event(i: int) -> TraceEvent:
    return {
        "event": "call",
        "node_id": f"race.py:fn_{i}",
        "ts_ns": i,
        "thread_id": 1,
        "frame_depth": 0,
        "metadata": {},
    }


def _wait_for(cond: Callable[[], bool], what: str) -> None:
    deadline = time.monotonic() + _GUARD_S
    while not cond():
        if time.monotonic() > deadline:
            pytest.fail(f"timed out after {_GUARD_S}s waiting for {what}")
        time.sleep(0.0005)


class _FakeWebSocket:
    """Stands in for the websockets client connection.

    While ``gate`` is clear, sending a ``trace_event`` parks the sender thread
    inside ``send()`` with its event counted as in flight, and sets ``parked``.
    ``gate=None`` never parks (the hammer tests).
    """

    def __init__(self, gate: threading.Event | None) -> None:
        self.gate = gate
        self.parked = threading.Event()
        self.frames: list[str] = []

    async def send(self, message: str) -> None:
        if self.gate is not None and json.loads(message)["type"] == "trace_event":
            deadline = time.monotonic() + _GUARD_S
            while not self.gate.is_set():
                self.parked.set()
                if time.monotonic() > deadline:
                    raise ConnectionError("test gate never opened")
                await asyncio.sleep(0.0005)
        self.frames.append(message)

    def __aiter__(self) -> _FakeWebSocket:
        return self

    async def __anext__(self) -> str:
        raise StopAsyncIteration  # the server never sends anything


class _FakeConnect:
    def __init__(self, ws: _FakeWebSocket) -> None:
        self._ws = ws

    async def __aenter__(self) -> _FakeWebSocket:
        return self._ws

    async def __aexit__(self, *exc_info: object) -> None:
        return None


def _use_fake_socket(monkeypatch: pytest.MonkeyPatch, ws: _FakeWebSocket) -> None:
    # _sender_main imports ``connect`` from this module at call time.
    monkeypatch.setattr(websockets.asyncio.client, "connect", lambda *_a, **_k: _FakeConnect(ws))


class _LockProbe:
    """Wraps the sender's own lock object and records contention on it.

    Wrapping (rather than replacing) keeps a mutant's lock semantics intact: a
    real lock still excludes, and a neutered one (anything without
    ``acquire``/``release``, e.g. ``contextlib.nullcontext()``) still does not.
    """

    def __init__(self, inner: Any) -> None:
        self._inner = inner
        self.contended = threading.Event()

    def __enter__(self) -> object:
        acquire = getattr(self._inner, "acquire", None)
        if acquire is None:
            return self._inner.__enter__()
        if not acquire(blocking=False):
            self.contended.set()
            acquire()
        return self

    def __exit__(self, *exc_info: object) -> None:
        release = getattr(self._inner, "release", None)
        if release is None:
            self._inner.__exit__(*exc_info)
        else:
            release()


class _RacingSender(TraceStreamSender):
    """``_inflight`` behind a property whose next write, on a chosen thread,
    can be held between the read that computed it and the store.

    A held write waits until the *other* thread either finishes its own update
    (possible only if nothing excludes it: the lost update) or is found blocked
    on the counter lock (the lock doing its job), then stores.
    """

    def __init__(self, max_inflight: int) -> None:
        # Set before super().__init__, whose ``self._inflight = 0`` goes
        # through the setter below.
        self._value = 0
        self._hold_next: str | None = None
        self._on_hold: Callable[[], None] | None = None
        self.held = threading.Event()
        self.producer_done = threading.Event()
        self.drain_wrote = threading.Event()
        self.drain_writes = 0
        self.guard_expired: list[str] = []
        super().__init__("ws://fake", "race", max_inflight=max_inflight)
        self.probe = _LockProbe(self._counter_lock)
        self._counter_lock = self.probe  # type: ignore[assignment]

    def hold_next_write(self, writer: str, on_hold: Callable[[], None] | None = None) -> None:
        self._hold_next = writer
        self._on_hold = on_hold

    @property
    def _inflight(self) -> int:
        return self._value

    @_inflight.setter
    def _inflight(self, value: int) -> None:
        on_drain = threading.current_thread().name == _SENDER_THREAD
        writer = "drain" if on_drain else "producer"
        if self._hold_next == writer:
            self._hold_next = None
            self.held.set()
            if self._on_hold is not None:
                self._on_hold()
            other_done = self.drain_wrote if writer == "producer" else self.producer_done
            deadline = time.monotonic() + _GUARD_S
            while not (other_done.is_set() or self.probe.contended.is_set()):
                if time.monotonic() > deadline:
                    self.guard_expired.append(writer)
                    break
                time.sleep(0.0005)
        self._value = value
        if on_drain:
            self.drain_writes += 1
            self.drain_wrote.set()


@pytest.mark.parametrize(
    "held_writer",
    [
        # The producer's increment is held while the sender thread decrements:
        # unlocked, the decrement is overwritten (the comment's lost decrement,
        # which strands the counter above the true in-flight count).
        "producer",
        # The sender thread's decrement is held while the producer increments:
        # unlocked, the increment is overwritten (the counter falls below the
        # true in-flight count, so the backpressure cap stops binding).
        "drain",
    ],
)
def test_counter_lock_serializes_sink_against_the_drain_decrement(
    monkeypatch: pytest.MonkeyPatch, held_writer: str
) -> None:
    gate = threading.Event()
    ws = _FakeWebSocket(gate)
    _use_fake_socket(monkeypatch, ws)
    sender = _RacingSender(max_inflight=2)
    sender.start(connect_timeout=_GUARD_S)
    try:
        sender.sink(_event(0))
        assert ws.parked.wait(_GUARD_S), "sender thread never reached send()"
        # Event 0 is in flight and the sender thread is parked in send(); the
        # decrement for it runs as soon as the gate opens.
        if held_writer == "producer":
            sender.hold_next_write("producer", on_hold=gate.set)
            sender.sink(_event(1))
        else:
            sender.hold_next_write("drain")
            gate.set()
            assert sender.held.wait(_GUARD_S), "the decrement never started"
            sender.sink(_event(1))
            sender.producer_done.set()
        _wait_for(lambda: sender.drain_writes == 2, "both events to be sent")

        # Nothing is queued or in flight, so the counter must be back at zero.
        assert sender._inflight == 0

        # The consequence a lost update has: at max_inflight=2, a burst of three
        # with the sender thread parked must drop exactly one. A lost decrement
        # drops two (the wedge the comment warns of, one event early); a lost
        # increment drops none (the cap no longer bounds the queue).
        gate.clear()
        ws.parked.clear()
        for i in range(2, 5):
            sender.sink(_event(i))
        assert sender.dropped == 1
    finally:
        gate.set()
        sent = sender.finish(timeout=_GUARD_S)
    assert sender.guard_expired == []
    assert sent == 4
    assert sender._inflight == 0


class _InterruptibleSender(TraceStreamSender):
    """``_inflight`` behind a trivial property: no holds, only switch points."""

    def __init__(self, session_id: str, max_inflight: int) -> None:
        self._value = 0
        super().__init__("ws://fake", session_id, max_inflight=max_inflight)

    @property
    def _inflight(self) -> int:
        return self._value

    @_inflight.setter
    def _inflight(self, value: int) -> None:
        self._value = value


def _race_residues(monkeypatch: pytest.MonkeyPatch, rounds: int, events: int) -> list[int]:
    """Run *rounds* free-running sink-vs-drain races; return each round's final
    ``_inflight``, which is 0 unless an update was lost."""
    ws = _FakeWebSocket(gate=None)
    _use_fake_socket(monkeypatch, ws)
    residues: list[int] = []
    previous = sys.getswitchinterval()
    sys.setswitchinterval(1e-6)
    try:
        for r in range(rounds):
            sender = _InterruptibleSender(f"hammer-{r}", max_inflight=events + 1)
            sender.start(connect_timeout=_GUARD_S)
            for i in range(events):
                sender.sink(_event(i))
            sent = sender.finish(timeout=60.0)
            assert sent == events, "every event must be sent (none dropped, none stuck)"
            residues.append(sender._inflight)
    finally:
        sys.setswitchinterval(previous)
    return residues


def test_free_running_race_loses_no_updates(monkeypatch: pytest.MonkeyPatch) -> None:
    """Small sibling of the hammer below: the same free-running race, sized to
    stay well under a second, so the gate exercises it on every run."""
    assert _race_residues(monkeypatch, rounds=3, events=5_000) == [0, 0, 0]


@pytest.mark.hammer
def test_free_running_race_loses_no_updates_hammer(monkeypatch: pytest.MonkeyPatch) -> None:
    assert _race_residues(monkeypatch, rounds=20, events=50_000) == [0] * 20
