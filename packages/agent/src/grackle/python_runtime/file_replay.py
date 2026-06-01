"""File-replay + stored-session machinery for the grackle WS server.

Owns the value object pairing a JsonlIndex with TraceAggregates
(:class:`_SeekableSession`), the windowed/streamed replay of a trace file to one
connection (:func:`_replay_trace`), loading a stored session as a fresh seekable
session (:func:`_load_stored_session`), and indexing a ``--trace-source`` file
into the SessionStore (:func:`_register_trace_source`).  The server dispatch
loop and connection handler orchestrate these; they do not contain the replay
logic.
"""

from __future__ import annotations

import asyncio
import contextlib
import time
from typing import TYPE_CHECKING
from uuid import NAMESPACE_URL, uuid5

import structlog
import websockets.exceptions

from grackle import protocol

if TYPE_CHECKING:
    from pathlib import Path

    from websockets.asyncio.server import ServerConnection

    from grackle.adapters.base import TraceEvent
    from grackle.python_runtime.aggregates import TraceAggregates
    from grackle.python_runtime.jsonl_index import JsonlIndex
    from grackle.session_store import SessionStore

log = structlog.get_logger()

# Maximum inter-event sleep during paced replay.  Keeps total replay time
# bounded even for traces with large idle gaps between calls.
_MAX_GAP_S = 0.25


class _SeekableSession:
    """A trace session the server can seek into and aggregate over.

    Pairs a :class:`JsonlIndex` (random-access windows) with a
    :class:`TraceAggregates` (cumulative heat / coverage / top-k) for one
    session id.  Both are built from a single file scan via ``build_seekable``.
    """

    __slots__ = ("aggregates", "index")

    def __init__(self, index: JsonlIndex, aggregates: TraceAggregates) -> None:
        self.index = index
        self.aggregates = aggregates


async def _replay_trace(
    ws: ServerConnection,
    trace_source: Path,
    pace: bool,
    session_id: str,
    *,
    seekable: bool = False,
    total_events: int = 0,
) -> None:
    """Stream a trace file to one connection as a session_start / events / session_end sequence.

    ``pace=True`` reproduces the original inter-event timing with each gap
    clamped to ``_MAX_GAP_S`` so long idle stretches don't stall the replay.
    ``pace=False`` pushes all events as fast as the network allows (for tests).

    When ``seekable=True`` the replay runs in **window-only mode**: only the
    ``trace_session_start`` and ``trace_session_end`` markers are sent — no
    individual ``trace_event`` messages are streamed.  The browser fetches
    event windows on demand via ``trace_seek_request``.  ``total_events``
    must be ``len(file_index)`` from the caller so the session_end payload
    reports the correct count without a second full-file scan.

    When ``seekable=False`` (default) all events are streamed as before.

    Load or parse failure → logs a warning, emits an empty session
    (event_count=0), then returns.  The server continues running.
    """
    started_ns = time.monotonic_ns()
    try:
        await ws.send(protocol.make_trace_session_start(session_id, started_ns, seekable=seekable))
    except websockets.exceptions.ConnectionClosed:
        return

    if seekable:
        # Window-only mode: no event stream.  The browser uses trace_seek_request
        # to fetch event windows.  total_events is pre-computed from the
        # JsonlIndex so the file is not scanned a second time here.
        with contextlib.suppress(websockets.exceptions.ConnectionClosed):
            await ws.send(
                protocol.make_trace_session_end(session_id, time.monotonic_ns(), total_events)
            )
        return

    # Non-seekable streaming path: load the full trace and stream every event.
    from grackle.python_runtime.writer import read_jsonl

    events: list[TraceEvent]
    try:
        events = read_jsonl(trace_source)
    except Exception as exc:
        log.warning("trace replay: failed to load", path=str(trace_source), error=str(exc))
        events = []

    prev_ts_ns: int | None = None
    for event in events:
        if pace and prev_ts_ns is not None:
            gap_s = (event["ts_ns"] - prev_ts_ns) / 1_000_000_000
            sleep_s = min(gap_s, _MAX_GAP_S)
            if sleep_s > 0:
                await asyncio.sleep(sleep_s)
        prev_ts_ns = event["ts_ns"]
        try:
            await ws.send(protocol.make_trace_event(event))
        except websockets.exceptions.ConnectionClosed:
            return

    try:
        await ws.send(protocol.make_trace_session_end(session_id, time.monotonic_ns(), len(events)))
    except websockets.exceptions.ConnectionClosed:
        return


async def _load_stored_session(
    ws: ServerConnection,
    path: Path,
    session_id: str,
    seekable_sessions: dict[str, _SeekableSession],
) -> None:
    """Replay a stored session as a seekable session to one connection.

    Builds (and registers) a :class:`_SeekableSession` so the loaded session
    gains full seek **and** aggregate-query support — identical to a
    ``--trace-source`` replay.  The build is cached in ``seekable_sessions`` so
    re-loading the same session reuses the index/aggregates.
    """
    from grackle.python_runtime.aggregates import build_seekable

    existing = seekable_sessions.get(session_id)
    if existing is not None:
        idx = existing.index
    else:
        try:
            loop = asyncio.get_running_loop()
            idx, agg = await loop.run_in_executor(None, build_seekable, path)
        except Exception as exc:
            log.warning("session load: build failed", path=str(path), error=str(exc))
            return
        seekable_sessions[session_id] = _SeekableSession(index=idx, aggregates=agg)
    await _replay_trace(ws, path, False, session_id, seekable=True, total_events=len(idx))


def _register_trace_source(
    store: SessionStore,
    trace_source: Path,
    index: JsonlIndex,
    root: Path,
) -> None:
    """Index a ``--trace-source`` file into the store so it appears in the library.

    Gives ``save_session`` a production caller and lets the trace be re-loaded
    after a restart (without ``--trace-source``).  The id is a stable uuid5 over
    the absolute path so re-serving the same file updates one row rather than
    accumulating duplicates.  ``source_path`` is the absolute local path read
    back via ``Path`` at load time.
    """
    from grackle.adapters import registry
    from grackle.session_store import SessionMeta

    abspath = str(trace_source.resolve())
    try:
        mtime_ns = trace_source.stat().st_mtime_ns
    except OSError:
        mtime_ns = 0
    try:
        detected = registry.detect(root)
        language = detected[0] if detected else "python"
    except Exception:
        language = "python"
    store.save_session(
        SessionMeta(
            id=str(uuid5(NAMESPACE_URL, abspath)),
            label=trace_source.name,
            started_ns=mtime_ns,
            ended_ns=mtime_ns,
            source_path=abspath,
            event_count=len(index),
            language=language,
        )
    )
