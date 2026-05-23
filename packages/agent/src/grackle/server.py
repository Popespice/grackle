from __future__ import annotations

import asyncio
import collections
import contextlib
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING
from uuid import uuid4

import structlog
import websockets.exceptions
from websockets.asyncio.server import ServerConnection
from websockets.asyncio.server import serve as _ws_serve

from grackle import protocol

if TYPE_CHECKING:
    from grackle.adapters.base import TraceEvent

log = structlog.get_logger()

_MAX_SOURCE_BYTES = 1 * 1024 * 1024  # 1 MiB
# Maximum inter-event sleep during paced replay.  Keeps total replay time
# bounded even for traces with large idle gaps between calls.
_MAX_GAP_S = 0.25
_DEFAULT_BUFFER_SECONDS = 60.0


def _trace_buffer_seconds() -> float:
    """Return the ring-buffer retention window from env or default."""
    raw = os.environ.get("GRACKLE_TRACE_BUFFER_SECONDS")
    if raw is not None:
        try:
            return float(raw)
        except (ValueError, TypeError):
            pass
    return _DEFAULT_BUFFER_SECONDS


def _allowed_origins() -> list[str]:
    env = os.environ.get("GRACKLE_ALLOWED_ORIGINS")
    if env:
        return [o.strip() for o in env.split(",")]
    return ["http://localhost:5173"]


def _read_source(root_real: Path, posix_path: str) -> tuple[str | None, str]:
    """Return (source, encoding) on success, or (None, reason) on failure."""
    if not posix_path or "\\" in posix_path:
        return (None, "forbidden")

    try:
        abs_path = (root_real / posix_path).resolve()
    except Exception:
        return (None, "not_found")

    try:
        common = os.path.commonpath([str(root_real), str(abs_path)])
    except ValueError:
        return (None, "forbidden")

    if common != str(root_real):
        return (None, "forbidden")

    if not abs_path.exists() or not abs_path.is_file():
        return (None, "not_found")

    try:
        size = abs_path.stat().st_size
    except OSError:
        return (None, "not_found")

    if size > _MAX_SOURCE_BYTES:
        return (None, "too_large")

    try:
        return (abs_path.read_text(encoding="utf-8"), "utf-8")
    except UnicodeDecodeError:
        return (None, "binary")
    except OSError:
        return (None, "not_found")


async def _push_static_graph(ws: ServerConnection, root: Path) -> None:
    """Detect language(s), parse the project, and push static_graph if supported."""
    from grackle.adapters import registry
    from grackle.adapters.base import ParseOptions

    detected = registry.detect(root)
    if not detected:
        return

    try:
        if len(detected) > 1:
            graph = registry.parse_all(root, ParseOptions())
        else:
            adapter = registry.get_static(detected[0])
            if adapter is None:
                return
            graph = adapter.parse(root, ParseOptions())
    except Exception as exc:
        log.warning("static graph parse failed", error=str(exc), root=str(root))
        return

    log.info(
        "static graph pushed",
        nodes=len(graph["nodes"]),
        edges=len(graph["edges"]),
        root=str(root),
    )
    await ws.send(protocol.make_static_graph(graph))


def _trim_ring_buffer(
    ring_buffer: collections.deque[tuple[int, str]],
    now_ns: int,
    buffer_seconds: float,
) -> None:
    """Discard entries older than buffer_seconds from the front of the ring-buffer."""
    cutoff_ns = now_ns - int(buffer_seconds * 1_000_000_000)
    while ring_buffer and ring_buffer[0][0] < cutoff_ns:
        ring_buffer.popleft()


async def _flush_ring_buffer(
    ws: ServerConnection,
    ring_buffer: collections.deque[tuple[int, str]],
) -> None:
    """Push all buffered live-ingest messages to a newly-joined consumer."""
    for _ts_ns, raw in ring_buffer:
        try:
            await ws.send(raw)
        except websockets.exceptions.ConnectionClosed:
            return


async def _broadcast(
    raw: str,
    connections: set[ServerConnection],
    exclude: ServerConnection | None = None,
) -> None:
    """Send raw to every registered connection except exclude.

    Per-connection ConnectionClosed is swallowed so one dead client cannot
    interrupt fan-out to the remaining consumers.
    """
    for ws in list(connections):
        if ws is exclude:
            continue
        with contextlib.suppress(websockets.exceptions.ConnectionClosed):
            await ws.send(raw)


async def _receive_loop(
    ws: ServerConnection,
    root_real: Path,
    connections: set[ServerConnection],
    ring_buffer: collections.deque[tuple[int, str]],
    buffer_seconds: float,
) -> None:
    """Process inbound messages from one connection.

    Handles ping/pong, read_source, and live-ingest trace messages.
    Live-ingest messages (trace_session_start / trace_event /
    trace_session_end) are appended to the ring-buffer and broadcast to
    all other connected consumers.
    """
    async for raw in ws:
        try:
            msg = raw.decode() if isinstance(raw, bytes) else raw
        except UnicodeDecodeError:
            continue
        try:
            envelope = protocol.parse_envelope(msg)
        except protocol.InvalidEnvelope:
            continue

        etype = envelope["type"]
        if etype == "ping":
            await ws.send(protocol.make_pong(envelope["id"]))
        elif etype == "read_source":
            path_val = envelope["payload"].get("path", "")
            if not isinstance(path_val, str):
                continue
            source, enc_or_reason = _read_source(root_real, path_val)
            if source is not None:
                reply = protocol.make_source_response(
                    envelope["id"], path_val, source, enc_or_reason
                )
            else:
                reply = protocol.make_source_error(envelope["id"], path_val, enc_or_reason)
            await ws.send(reply)
        elif etype in ("trace_session_start", "trace_event", "trace_session_end"):
            # Live-ingest path: a producer process is streaming events into
            # this server.  Buffer each message and broadcast to all consumers.
            now_ns = time.monotonic_ns()
            _trim_ring_buffer(ring_buffer, now_ns, buffer_seconds)
            ring_buffer.append((now_ns, msg))
            await _broadcast(msg, connections, exclude=ws)


async def _replay_trace(
    ws: ServerConnection,
    trace_source: Path,
    pace: bool,
    session_id: str,
) -> None:
    """Stream a trace file to one connection as a session_start / events / session_end sequence.

    ``pace=True`` reproduces the original inter-event timing with each gap
    clamped to ``_MAX_GAP_S`` so long idle stretches don't stall the replay.
    ``pace=False`` pushes all events as fast as the network allows (for tests).

    Load or parse failure → logs a warning, emits an empty session
    (event_count=0), then returns.  The server continues running.
    """
    from grackle.python_runtime.writer import read_jsonl

    events: list[TraceEvent]
    try:
        events = read_jsonl(trace_source)
    except Exception as exc:
        log.warning("trace replay: failed to load", path=str(trace_source), error=str(exc))
        events = []

    started_ns = time.monotonic_ns()
    try:
        await ws.send(protocol.make_trace_session_start(session_id, started_ns))
    except websockets.exceptions.ConnectionClosed:
        return

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


async def serve(
    host: str,
    port: int,
    root: Path = Path(),
    trace_source: Path | None = None,
    pace: bool = True,
) -> None:
    """Start the WebSocket server and run until cancelled.

    Args:
        host:         Bind address (must be loopback in production).
        port:         WebSocket port.
        root:         Project root — parsed on each client connect for the
                      static_graph push.
        trace_source: If set, replay this JSONL trace file to every new
                      client after the static_graph push.  Each connection
                      gets its own replay from the start of the file.
        pace:         When True (default) replay is deadline-scheduled to
                      reproduce original inter-event timing (gap clamped to
                      ``_MAX_GAP_S``).  When False events are pushed as fast
                      as the socket allows — useful for tests and CI.
    """
    root_real = root.resolve()

    # Closure-scoped connection registry — every consumer connection lives here.
    connections: set[ServerConnection] = set()
    # Ring-buffer for live-attach late joiners.  Populated only by live ingest;
    # not used in file-replay mode (each connection re-reads the file).
    ring_buffer: collections.deque[tuple[int, str]] = collections.deque()
    buffer_seconds = _trace_buffer_seconds()

    async def _handler(ws: ServerConnection) -> None:
        origin = ws.request.headers.get("Origin", "") if ws.request is not None else ""
        if origin and origin not in _allowed_origins():
            await ws.close(1008, "Origin not allowed")
            return

        log.info("client connected", remote=ws.remote_address)
        connections.add(ws)

        tasks: list[asyncio.Task[None]] = []
        try:
            # Static graph first — guaranteed to arrive before any trace messages.
            await _push_static_graph(ws, root_real)

            # Flush ring-buffer history to late joiners (live mode only).
            if trace_source is None:
                await _flush_ring_buffer(ws, ring_buffer)

            # Receive loop handles ping, read_source, and live-ingest.
            receive_task: asyncio.Task[None] = asyncio.create_task(
                _receive_loop(ws, root_real, connections, ring_buffer, buffer_seconds)
            )
            tasks.append(receive_task)

            # File-replay task (one per connection, independent of live ingest).
            if trace_source is not None:
                replay_task: asyncio.Task[None] = asyncio.create_task(
                    _replay_trace(ws, trace_source, pace, str(uuid4()))
                )
                tasks.append(replay_task)

            with contextlib.suppress(websockets.exceptions.ConnectionClosed):
                await asyncio.gather(*tasks)
        finally:
            # Reap tasks — cancel any still-running, then await to completion
            # with return_exceptions=True so CancelledError is collected rather
            # than re-raised.
            for t in tasks:
                t.cancel()
            if tasks:
                await asyncio.gather(*tasks, return_exceptions=True)
            connections.discard(ws)
            log.info("client disconnected", remote=ws.remote_address)

    if host not in ("127.0.0.1", "localhost", "::1"):
        log.warning("binding to non-loopback address — agent reachable from network", host=host)
    async with _ws_serve(_handler, host, port):
        log.info("agent listening", host=host, port=port, root=str(root_real))
        await asyncio.Future()  # run until cancelled
