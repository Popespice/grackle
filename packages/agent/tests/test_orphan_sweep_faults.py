"""Test campaign T6-2 (docs/test-campaigns/phase-12.md): orphan-sweep hazards.

``serve()`` runs :func:`~grackle.python_runtime.recording_sink.sweep_orphaned_recordings`
over ``<store dir>/recordings`` before it binds. The sweep deletes every
``*.jsonl.part`` whose mtime is at least 30 s old, on the theory that such a
file was left by a hard-killed server. Its docstring admits the heuristic is
not an ownership protocol: a peer ``serve --store`` sharing the directory can
sweep a *live* recording that has gone idle.

Probed against the real server (two servers started through ``start_server``
over one store directory):

* An unremovable orphan (``PermissionError`` on unlink) raises out of
  ``serve()`` before the bind, so one stale file stops ``serve --store`` from
  starting at all — ledgered. ``start_server`` surfaces it immediately (the C2
  readiness future), rather than as a timeout or a refused connect.
* A fresh live recording survives a peer's startup sweep — pinned.
* An idle live recording is swept, and the cost is the WHOLE session, not its
  idle tail: the owner's finalize fails at the rename and ``RecordingSink``
  discards what is left — pinned as the documented, accepted hazard.
* "Idle" is judged by mtime, and mtime advances only when the writer's
  userspace buffer flushes (st_blksize / 8 KiB on 3.12-3.13, 128 KiB on 3.14).
  A recording that received an event a moment ago still looks 30 s old, so an
  *active* recording is swept too — the case the guard exists to prevent.
  Ledgered.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any

import pytest
from websockets.asyncio.client import connect

from grackle.python_runtime.writer import read_jsonl
from grackle.session_store import SessionStore

if TYPE_CHECKING:
    from conftest import StartServer
    from websockets.asyncio.client import ClientConnection


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _payload(i: int) -> dict[str, Any]:
    return {
        "event": "call",
        "node_id": f"app.py:f{i}",
        "ts_ns": i * 1_000,
        "thread_id": 1,
        "frame_depth": 0,
        "metadata": {},
    }


def _msg(kind: str, payload: dict[str, Any]) -> str:
    return json.dumps({"id": f"{kind}-{id(payload)}", "type": kind, "payload": payload})


def _session_start(sid: str) -> str:
    return _msg("trace_session_start", {"session_id": sid, "started_ns": 1, "source": "live"})


def _session_end(sid: str, count: int) -> str:
    return _msg("trace_session_end", {"session_id": sid, "ended_ns": 2, "event_count": count})


async def _barrier(ws: ClientConnection, tag: str) -> None:
    """Round-trip a ping. One connection's messages are handled strictly in
    order, so the pong proves every message sent before it — including a
    ``trace_session_end``'s awaited finalize — has been processed."""
    await ws.send(json.dumps({"id": tag, "type": "ping", "payload": {}}))
    while True:
        msg = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
        if msg["type"] == "pong" and msg["id"] == tag:
            return


def _backdate(path: Path, seconds: float) -> None:
    then = time.time() - seconds
    os.utime(path, (then, then))


async def _record_until_peer_starts(
    start_server: StartServer,
    tmp_path: Path,
    *,
    sid: str,
    idle_s: float,
    event_after_idle: bool,
) -> tuple[SessionStore, Path, bool]:
    """Server A records session *sid*; its ``.part`` is aged by *idle_s*
    seconds (optionally followed by one more event); then server B starts on
    the same store directory, running its startup sweep. A's session is then
    ended. Returns A's store, the recordings dir, and whether the ``.part``
    survived B's startup."""
    db = tmp_path / "sessions.db"
    store_a = SessionStore.open(db)
    _, port_a = await start_server(root=tmp_path, store=store_a)
    recordings = tmp_path / "recordings"
    part = recordings / f"{sid}.jsonl.part"

    async with connect(f"ws://127.0.0.1:{port_a}") as producer:
        await producer.send(_session_start(sid))
        await producer.send(_msg("trace_event", _payload(0)))
        await producer.send(_msg("trace_event", _payload(1)))
        await _barrier(producer, "opened")
        assert part.exists()

        _backdate(part, idle_s)
        sent = 2
        if event_after_idle:
            # A small event: it lands in the writer's buffer, not on disk, so
            # it does not touch the .part's mtime.
            await producer.send(_msg("trace_event", _payload(2)))
            await _barrier(producer, "fresh-event")
            sent = 3

        await start_server(root=tmp_path, store=SessionStore.open(db))  # peer: sweeps
        survived = part.exists()

        await producer.send(_session_end(sid, sent))
        await _barrier(producer, "ended")
    return store_a, recordings, survived


# ---------------------------------------------------------------------------
# An unremovable orphan must not stop the server starting
# ---------------------------------------------------------------------------


# A read-only directory denies unlinking its entries only where POSIX
# permission bits apply to the caller: not on Windows, and not for root.
if sys.platform == "win32":
    _PERMISSION_BITS_BIND = False
else:
    _PERMISSION_BITS_BIND = os.geteuid() != 0


def _deny_part_unlink(monkeypatch: pytest.MonkeyPatch, recordings: Path) -> None:
    """Make unlinking any ``.part`` under *recordings* fail the way a file
    held open by another process fails on Windows (WinError 32), or a file in
    a directory the server may not write fails on POSIX."""
    real_unlink = Path.unlink

    def _unlink(self: Path, missing_ok: bool = False) -> None:
        if self.parent == recordings and self.name.endswith(".jsonl.part"):
            raise PermissionError(13, "Permission denied", str(self))
        real_unlink(self, missing_ok=missing_ok)

    monkeypatch.setattr(Path, "unlink", _unlink)


@pytest.mark.xfail(
    strict=True,
    raises=PermissionError,
    reason=(
        "T6-2: a best-effort orphan sweep that cannot unlink a stale .part "
        "raises PermissionError out of serve() before the bind, so serve --store "
        "never starts (docs/test-campaigns/phase-12.md)"
    ),
)
@pytest.mark.parametrize(
    "blocker",
    [
        "injected-permission-error",
        pytest.param(
            "read-only-recordings-dir",
            marks=pytest.mark.skipif(
                not _PERMISSION_BITS_BIND,
                reason="needs POSIX permission bits that bind this user (not Windows, not root)",
            ),
        ),
    ],
)
async def test_serve_starts_despite_an_unremovable_orphan_part(
    start_server: StartServer,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    blocker: str,
) -> None:
    """The sweep's own docstring calls it "a best-effort heuristic", and it
    already skips a ``.part`` whose ``stat()`` fails — but not one whose
    ``unlink()`` fails. A leftover from a previous crash, which the sweep
    exists to tidy up, then takes the whole server down with an unhandled
    traceback (and ``serve()`` leaves the store it was handed unclosed: the
    sweep runs before the ``try`` whose ``finally`` closes it). Expected: the
    server starts and serves, and the file it could not remove stays put."""
    recordings = tmp_path / "recordings"
    recordings.mkdir()
    orphan = recordings / "crashed-run.jsonl.part"
    orphan.write_bytes(json.dumps(_payload(0)).encode() + b"\n")
    _backdate(orphan, 3600)

    if blocker == "injected-permission-error":
        _deny_part_unlink(monkeypatch, recordings)
    else:
        recordings.chmod(0o555)
    try:
        _, port = await start_server(root=tmp_path, store=SessionStore.open(tmp_path / "s.db"))
        async with connect(f"ws://127.0.0.1:{port}") as client:
            await _barrier(client, "alive")
    finally:
        recordings.chmod(0o755)
    assert orphan.exists()


# ---------------------------------------------------------------------------
# A peer server's startup sweep vs. a live recording
# ---------------------------------------------------------------------------


async def test_peer_startup_sweep_spares_a_fresh_live_recording(
    start_server: StartServer, tmp_path: Path
) -> None:
    """The guard the 30 s threshold exists for: a second ``serve --store`` on
    the same directory, starting while the first is recording, leaves the
    first one's ``.part`` alone, and that session is recorded intact."""
    store, recordings, survived = await _record_until_peer_starts(
        start_server, tmp_path, sid="fresh", idle_s=0.0, event_after_idle=False
    )

    assert survived
    meta = store.get_session("fresh")
    assert meta is not None
    assert meta.event_count == 2
    assert read_jsonl(recordings / "fresh.jsonl") == [_payload(0), _payload(1)]


@pytest.mark.skipif(
    sys.platform == "win32",
    reason=(
        "Windows refuses to unlink a file another handle holds open, so there the "
        "peer's sweep raises instead (the unremovable-orphan xfail above)"
    ),
)
async def test_peer_startup_sweep_loses_an_idle_live_recording(
    start_server: StartServer, tmp_path: Path
) -> None:
    """Pinned as documented behavior, not ledgered: the sweep's docstring
    accepts that "a still-active but idle recording older than the threshold
    could in principle be swept by a concurrent peer".

    What the docstring leaves unsaid is the cost, pinned here: not the idle
    tail but the whole session. The owner keeps writing into the unlinked
    inode; at session end its finalize fails at the ``.part`` -> ``.jsonl``
    rename, and ``RecordingSink`` discards the recording — no file, no store
    row, one warning in the owner's log. An ownership protocol (a lock the
    sweep honours) would flip this pin; that is a product decision, recorded
    with the ledger entry below."""
    store, recordings, survived = await _record_until_peer_starts(
        start_server, tmp_path, sid="idle", idle_s=60.0, event_after_idle=False
    )

    assert not survived
    assert store.get_session("idle") is None
    assert sorted(p.name for p in recordings.iterdir()) == []


@pytest.mark.xfail(
    strict=True,
    raises=AssertionError,
    reason=(
        "T6-2: the sweep judges a .part idle by its mtime, which only advances "
        "when the writer's buffer flushes, so a peer's startup sweep deletes a "
        "recording that received an event moments ago (docs/test-campaigns/phase-12.md)"
    ),
)
async def test_peer_startup_sweep_spares_a_recording_that_just_received_an_event(
    start_server: StartServer, tmp_path: Path
) -> None:
    """The threshold is meant to protect "a ``.part`` file actively being
    written"; only a recording idle for 30 s is conceded. But ``JsonlPartWriter``
    writes through a buffered handle and never flushes before finalize, so
    the file's mtime is the time of the last *flush*, not the last event: an
    event that arrived a moment ago sits in the buffer and leaves the mtime
    untouched. On Python 3.14 (128 KiB buffer) a recording taking ~200-byte
    events at one per second flushes about every ten minutes, so it looks
    orphaned for most of its life.

    Aging the ``.part`` 60 s and then delivering a fresh event models a
    recording that opened a minute ago and is still receiving. Expected: the
    peer spares it and the session is recorded. Observed on POSIX: it is
    swept and lost exactly like the idle case above. On Windows the peer's
    startup raises instead, so this fails there too."""
    store, recordings, survived = await _record_until_peer_starts(
        start_server, tmp_path, sid="active", idle_s=60.0, event_after_idle=True
    )

    assert survived
    meta = store.get_session("active")
    assert meta is not None
    assert meta.event_count == 3
    assert read_jsonl(recordings / "active.jsonl") == [_payload(i) for i in range(3)]
