from __future__ import annotations

import asyncio
import collections
import contextlib
import os
import time
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog
import websockets.exceptions
from websockets.asyncio.server import ServerConnection
from websockets.asyncio.server import serve as _ws_serve

from grackle import protocol

if TYPE_CHECKING:
    from grackle.adapters.base import TraceEvent
    from grackle.python_runtime.aggregates import TraceAggregates
    from grackle.python_runtime.jsonl_index import JsonlIndex
    from grackle.session_store import SessionStore

log = structlog.get_logger()

_MAX_SOURCE_BYTES = 1 * 1024 * 1024  # 1 MiB
# Maximum inter-event sleep during paced replay.  Keeps total replay time
# bounded even for traces with large idle gaps between calls.
_MAX_GAP_S = 0.25
_DEFAULT_BUFFER_SECONDS = 60.0
# Maximum events returned per trace_seek_request.  Bounds per-request I/O and
# prevents a single malicious/buggy client from issuing a count=2**31 seek that
# reads the whole file synchronously on the event loop.
_MAX_SEEK_COUNT = 1000


def _trace_buffer_seconds() -> float:
    """Return the ring-buffer retention window from env or default."""
    raw = os.environ.get("GRACKLE_TRACE_BUFFER_SECONDS")
    if raw is not None:
        try:
            return float(raw)
        except (ValueError, TypeError):
            pass
    return _DEFAULT_BUFFER_SECONDS


def _trace_buffer_max_events() -> int | None:
    """Return the ring-buffer event count cap from env, or None (unbounded).

    Set ``GRACKLE_TRACE_BUFFER_MAX_EVENTS`` to a positive integer to evict the
    oldest events when the buffer exceeds that count.  Values < 1 and
    non-integer strings are treated as None (unbounded).
    """
    raw = os.environ.get("GRACKLE_TRACE_BUFFER_MAX_EVENTS")
    if raw is not None:
        try:
            v = int(raw)
            return v if v >= 1 else None
        except (ValueError, TypeError):
            pass
    return None


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

    from grackle.graph_analysis import enrich_metadata

    enrich_metadata(graph)

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
    max_events: int | None = None,
) -> None:
    """Discard entries from the front of the ring-buffer.

    Two independent eviction passes (both run each call):

    1. **Age trim** — entries whose timestamp is older than ``buffer_seconds``
       are evicted from the front.
    2. **Count cap** — if ``max_events`` is not None and the buffer still
       exceeds that count after the age trim, the oldest entries are evicted
       until ``len(ring_buffer) <= max_events``.

    Applying the count cap *after* the age trim means a narrow time window
    with a high event count is bounded, but a small time window that happens
    to be quiet is not artificially inflated.
    """
    cutoff_ns = now_ns - int(buffer_seconds * 1_000_000_000)
    while ring_buffer and ring_buffer[0][0] < cutoff_ns:
        ring_buffer.popleft()
    if max_events is not None:
        while len(ring_buffer) > max_events:
            ring_buffer.popleft()


async def _flush_ring_buffer(
    ws: ServerConnection,
    ring_buffer: collections.deque[tuple[int, str]],
) -> None:
    """Push all buffered live-ingest messages to a newly-joined consumer.

    Takes a snapshot of the ring-buffer at call time (``list(ring_buffer)``)
    so that concurrent producer appends or trim-evictions in ``_receive_loop``
    cannot mutate the deque mid-iteration and raise
    ``RuntimeError: deque mutated during iteration``.
    """
    for _ts_ns, raw in list(ring_buffer):
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


def _compute_cumulative_heat(agg: TraceAggregates, at_index: int) -> dict[str, int]:
    """Return {node_id: count} for all nodes with count > 0 at at_index."""
    result: dict[str, int] = {}
    for node_id in agg._node_ids():
        count = agg.cumulative_heat(node_id, at_index)
        if count > 0:
            result[node_id] = count
    return result


async def _receive_loop(
    ws: ServerConnection,
    root_real: Path,
    connections: set[ServerConnection],
    ring_buffer: collections.deque[tuple[int, str]],
    buffer_seconds: float,
    max_events: int | None = None,
    *,
    trace_index: JsonlIndex | None = None,
    file_session_id: str | None = None,
    trace_aggregates: TraceAggregates | None = None,
    store: SessionStore | None = None,
) -> None:
    """Process inbound messages from one connection.

    Handles ping/pong, read_source, live-ingest trace messages,
    ``trace_seek_request`` for server-side seek, ``trace_query_request`` for
    aggregate queries, ``session_list_request``, and ``session_load_request``.

    Live-ingest messages (trace_session_start / trace_event /
    trace_session_end) are appended to the ring-buffer and broadcast to
    all other connected consumers.

    ``trace_seek_request`` is answered directly to the requesting client only:
    ``trace_window`` on success, ``trace_seek_error`` if the session ID does
    not match or no index is loaded.
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
        elif etype == "trace_seek_request":
            # Server-side seek (Phase 7.3).  Only file-replay mode supports
            # this; live-attach sessions are not seekable.
            seek_sid = envelope["payload"].get("session_id", "")
            if not isinstance(seek_sid, str):
                continue
            if trace_index is None or seek_sid != file_session_id:
                await ws.send(
                    protocol.make_trace_seek_error(envelope["id"], seek_sid, "session not found")
                )
                continue
            start_raw = envelope["payload"].get("start_index", 0)
            count_raw = envelope["payload"].get("count", 0)
            if not isinstance(start_raw, int) or not isinstance(count_raw, int):
                await ws.send(
                    protocol.make_trace_seek_error(
                        envelope["id"], seek_sid, "invalid start_index or count"
                    )
                )
                continue
            # Cap count to bound per-request I/O.  Compute clamped start here
            # (mirrors read_window's own clamping) so the response payload
            # echoes the actual start index rather than the raw unclamped value.
            count_capped = min(max(0, count_raw), _MAX_SEEK_COUNT)
            total = len(trace_index)
            clamped_start = max(0, min(start_raw, total))
            try:
                loop = asyncio.get_running_loop()
                seek_events: list[TraceEvent] = await loop.run_in_executor(
                    None, trace_index.read_window, start_raw, count_capped
                )
            except Exception as exc:
                log.warning("trace seek: read_window failed", error=str(exc))
                await ws.send(
                    protocol.make_trace_seek_error(envelope["id"], seek_sid, "read error")
                )
                continue
            await ws.send(
                protocol.make_trace_window(
                    envelope["id"], seek_sid, clamped_start, seek_events, total
                )
            )
        elif etype == "trace_query_request":
            qsid = envelope["payload"].get("session_id", "")
            kind = envelope["payload"].get("kind", "")
            at_raw = envelope["payload"].get("at_index", 0)
            if trace_aggregates is None or qsid != file_session_id:
                await ws.send(
                    protocol.make_trace_query_response(
                        envelope["id"], qsid, kind, 0, {}, error="session not found"
                    )
                )
                continue
            if not isinstance(at_raw, int):
                await ws.send(
                    protocol.make_trace_query_response(
                        envelope["id"], qsid, kind, 0, {}, error="invalid at_index"
                    )
                )
                continue
            at_index = max(0, min(at_raw, len(trace_aggregates)))
            try:
                loop = asyncio.get_running_loop()
                data: dict[str, Any]
                if kind == "cumulative_heat":
                    data = await loop.run_in_executor(
                        None, _compute_cumulative_heat, trace_aggregates, at_index
                    )
                elif kind == "coverage":
                    count = await loop.run_in_executor(
                        None, trace_aggregates.coverage_count, at_index
                    )
                    data = {"count": count}
                elif kind == "top_k":
                    k = int(envelope["payload"].get("k", 20))
                    entries = await loop.run_in_executor(None, trace_aggregates.top_k, k, at_index)
                    data = {"entries": [{"node_id": nid, "count": cnt} for nid, cnt in entries]}
                else:
                    await ws.send(
                        protocol.make_trace_query_response(
                            envelope["id"],
                            qsid,
                            kind,
                            at_index,
                            {},
                            error=f"unknown kind: {kind!r}",
                        )
                    )
                    continue
                await ws.send(
                    protocol.make_trace_query_response(envelope["id"], qsid, kind, at_index, data)
                )
            except Exception as exc:
                log.warning("trace query error", kind=kind, error=str(exc))
                await ws.send(
                    protocol.make_trace_query_response(
                        envelope["id"], qsid, kind, at_raw, {}, error="query error"
                    )
                )
        elif etype == "session_list_request":
            if store is None:
                sessions_data: list[dict[str, Any]] = []
            else:
                sessions_data = [
                    {
                        "id": s.id,
                        "label": s.label,
                        "started_ns": s.started_ns,
                        "ended_ns": s.ended_ns,
                        "source_path": s.source_path,
                        "event_count": s.event_count,
                        "language": s.language,
                    }
                    for s in store.list_sessions()
                ]
            await ws.send(protocol.make_session_list_response(envelope["id"], sessions_data))
        elif etype == "session_load_request":
            load_sid = envelope["payload"].get("session_id", "")
            if store is None:
                continue
            meta = store.get_session(load_sid)
            if meta is None:
                continue
            load_path = Path(meta.source_path)
            if not load_path.exists():
                continue
            asyncio.create_task(_load_stored_session(ws, load_path, load_sid))
        elif etype in ("trace_session_start", "trace_event", "trace_session_end"):
            # Live-ingest path: a producer process is streaming events into
            # this server.  Buffer each message and broadcast to all consumers.
            # Append before trim so the count cap is enforced immediately after
            # each message lands — the buffer never exceeds max_events by more
            # than 0 (vs. trim-before-append which allows a transient +1).
            now_ns = time.monotonic_ns()
            ring_buffer.append((now_ns, msg))
            _trim_ring_buffer(ring_buffer, now_ns, buffer_seconds, max_events)
            await _broadcast(msg, connections, exclude=ws)


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


async def _load_stored_session(ws: ServerConnection, path: Path, session_id: str) -> None:
    """Replay a stored session as a seekable session to one connection."""
    from grackle.python_runtime.jsonl_index import JsonlIndex as _JsonlIndex

    try:
        idx = _JsonlIndex.build(path)
    except Exception as exc:
        log.warning("session load: index failed", path=str(path), error=str(exc))
        return
    await _replay_trace(ws, path, False, session_id, seekable=True, total_events=len(idx))


async def serve(
    host: str,
    port: int,
    root: Path = Path(),
    trace_source: Path | None = None,
    pace: bool = True,
    store: SessionStore | None = None,
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
    max_events = _trace_buffer_max_events()

    # File-replay mode: build the byte-offset index once at startup so every
    # connection can seek into the file without re-scanning it.  The session_id
    # is also stable across all connections — the browser receives the same id
    # on reconnect and can continue using it in seek requests.
    file_index: JsonlIndex | None = None
    file_session_id: str | None = None
    file_aggregates: TraceAggregates | None = None
    if trace_source is not None:
        from grackle.python_runtime.aggregates import TraceAggregates as _TraceAggregates
        from grackle.python_runtime.jsonl_index import JsonlIndex as _JsonlIndex

        try:
            file_index = _JsonlIndex.build(trace_source)
            log.info(
                "trace index built",
                path=str(trace_source),
                events=len(file_index),
            )
        except Exception as exc:
            log.warning(
                "trace index build failed — seek disabled",
                path=str(trace_source),
                error=str(exc),
            )
        try:
            file_aggregates = _TraceAggregates.build(trace_source)
            log.info(
                "trace aggregates built",
                path=str(trace_source),
                events=len(file_aggregates),
            )
        except Exception as exc:
            log.warning(
                "trace aggregates build failed",
                path=str(trace_source),
                error=str(exc),
            )
        file_session_id = str(uuid4())

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

            # Receive loop handles ping, read_source, live-ingest, and seek.
            receive_task: asyncio.Task[None] = asyncio.create_task(
                _receive_loop(
                    ws,
                    root_real,
                    connections,
                    ring_buffer,
                    buffer_seconds,
                    max_events,
                    trace_index=file_index,
                    file_session_id=file_session_id,
                    trace_aggregates=file_aggregates,
                    store=store,
                )
            )
            tasks.append(receive_task)

            # File-replay task (one per connection, independent of live ingest).
            # Uses the stable file_session_id and advertises seekable=True so
            # the browser can send trace_seek_request messages.
            # total_events is pre-computed from the index so _replay_trace does
            # not need to scan the file a second time in seekable mode.
            if trace_source is not None:
                assert file_session_id is not None  # set above when trace_source is set
                replay_task: asyncio.Task[None] = asyncio.create_task(
                    _replay_trace(
                        ws,
                        trace_source,
                        pace,
                        file_session_id,
                        seekable=file_index is not None,
                        total_events=len(file_index) if file_index is not None else 0,
                    )
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
