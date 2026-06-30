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
from grackle.python_runtime.file_replay import (
    SeekableSession,
    load_stored_session,
    register_trace_source,
    replay_trace,
)
from grackle.python_runtime.live_buffer import (
    broadcast,
    flush_ring_buffer,
    trace_buffer_max_events,
    trace_buffer_seconds,
    trim_ring_buffer,
)
from grackle.python_runtime.recording_sink import RecordingSink, sweep_orphaned_recordings

if TYPE_CHECKING:
    from grackle.adapters.base import StaticGraph, TraceEvent
    from grackle.session_store import SessionStore

log = structlog.get_logger()

# Maximum bytes returned by a read_source request.  1 MiB is far larger than any
# hand-written source file but bounds memory + a single WS frame, so a pathological
# or generated multi-megabyte file cannot be slurped into RAM and pushed to the UI.
_MAX_SOURCE_BYTES = 1 * 1024 * 1024  # 1 MiB
# Maximum events returned per trace_seek_request.  Bounds per-request I/O and
# prevents a single malicious/buggy client from issuing a count=2**31 seek that
# reads the whole file synchronously on the event loop.
_MAX_SEEK_COUNT = 1000


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


def _graph_signature(graph: StaticGraph) -> tuple[int, int, int]:
    """Cheap order-independent signature of a graph's topology.

    Used to cache agent-side analysis (hub-score + cycles) across connects:
    re-parsing the same unchanged project yields the same signature, so the
    expensive Tarjan SCC pass runs once instead of once per browser tab.  An
    edit that changes the topology changes the signature, so live-reparse still
    refreshes the analysis.
    """
    checksum = 0
    for e in graph["edges"]:
        checksum ^= hash((e["source"], e["target"], e["kind"]))
    return (len(graph["nodes"]), len(graph["edges"]), checksum)


async def _push_static_graph(
    ws: ServerConnection,
    root: Path,
    meta_cache: dict[tuple[int, int, int], dict[str, Any]],
) -> None:
    """Detect language(s), parse the project, and push static_graph if supported.

    Agent-side analysis (hub-score + cycles) is injected into ``graph.metadata``
    via :func:`enrich_metadata`, memoized by ``meta_cache`` so identical graphs
    across connects do not recompute Tarjan SCC.
    """
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

    sig = _graph_signature(graph)
    cached = meta_cache.get(sig)
    if cached is not None:
        graph.setdefault("metadata", {}).update(cached)
    else:
        from grackle.graph_analysis import enrich_metadata

        enrich_metadata(graph)
        meta_cache[sig] = {
            "hub_score": graph["metadata"]["hub_score"],
            "cycles": graph["metadata"]["cycles"],
        }

    log.info(
        "static graph pushed",
        nodes=len(graph["nodes"]),
        edges=len(graph["edges"]),
        root=str(root),
    )
    await ws.send(protocol.make_static_graph(graph))


async def _receive_loop(
    ws: ServerConnection,
    root_real: Path,
    connections: set[ServerConnection],
    ring_buffer: collections.deque[tuple[int, str]],
    buffer_seconds: float,
    max_events: int | None = None,
    *,
    seekable_sessions: dict[str, SeekableSession] | None = None,
    store: SessionStore | None = None,
    recordings_dir: Path | None = None,
    recording_language: str = "python",
    file_session_id: str | None = None,
) -> None:
    """Process inbound messages from one connection.

    Handles ping/pong, read_source, live-ingest trace messages,
    ``trace_seek_request`` for server-side seek, ``trace_query_request`` for
    aggregate queries, ``session_list_request``, and ``session_load_request``.

    Live-ingest messages (trace_session_start / trace_event /
    trace_session_end) are appended to the ring-buffer and broadcast to
    all other connected consumers.  When ``recordings_dir`` is set (i.e. the
    server has a session store), each live-ingest session is also tee'd to a
    JSONL recording and registered in the store on session end, producer
    disconnect, or server shutdown (Phase 9.3, ADR-0020 amendment).

    Seek and query requests are answered only for session ids present in
    ``seekable_sessions`` — the file-replay session plus any session loaded from
    the store this server run.  Unknown ids get an error reply.
    """
    sessions = seekable_sessions if seekable_sessions is not None else {}
    recorder: RecordingSink | None = None
    try:
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
                # Server-side seek (Phase 7.3).  Works for any session registered in
                # seekable_sessions (file-replay + store-loaded); live-attach is not.
                seek_sid = envelope["payload"].get("session_id", "")
                if not isinstance(seek_sid, str):
                    continue
                sess = sessions.get(seek_sid)
                if sess is None:
                    await ws.send(
                        protocol.make_trace_seek_error(
                            envelope["id"], seek_sid, "session not found"
                        )
                    )
                    continue
                trace_index = sess.index
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
                sess = sessions.get(qsid) if isinstance(qsid, str) else None
                if sess is None:
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
                aggregates = sess.aggregates
                at_index = max(0, min(at_raw, len(aggregates)))
                try:
                    loop = asyncio.get_running_loop()
                    data: dict[str, Any]
                    if kind == "cumulative_heat":
                        data = await loop.run_in_executor(
                            None, aggregates.cumulative_heat_all, at_index
                        )
                    elif kind == "coverage":
                        count = await loop.run_in_executor(
                            None, aggregates.coverage_count, at_index
                        )
                        data = {"count": count}
                    elif kind == "top_k":
                        k = int(envelope["payload"].get("k", 20))
                        entries = await loop.run_in_executor(None, aggregates.top_k, k, at_index)
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
                        protocol.make_trace_query_response(
                            envelope["id"], qsid, kind, at_index, data
                        )
                    )
                except Exception as exc:
                    log.warning("trace query error", kind=kind, error=str(exc))
                    await ws.send(
                        protocol.make_trace_query_response(
                            envelope["id"], qsid, kind, at_index, {}, error="query error"
                        )
                    )
            elif etype == "session_list_request":
                if store is None:
                    sessions_data: list[dict[str, Any]] = []
                else:
                    loop = asyncio.get_running_loop()
                    metas = await loop.run_in_executor(None, store.list_sessions)
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
                        for s in metas
                    ]
                await ws.send(protocol.make_session_list_response(envelope["id"], sessions_data))
            elif etype == "session_load_request":
                load_sid = envelope["payload"].get("session_id", "")
                if store is None:
                    log.warning("session load ignored: server has no --store", session_id=load_sid)
                    continue
                loop = asyncio.get_running_loop()
                meta = await loop.run_in_executor(None, store.get_session, load_sid)
                if meta is None:
                    log.warning("session load: unknown session id", session_id=load_sid)
                    continue
                load_path = Path(meta.source_path)
                if not load_path.exists():
                    log.warning(
                        "session load: source file missing",
                        session_id=load_sid,
                        path=str(load_path),
                    )
                    continue
                asyncio.create_task(load_stored_session(ws, load_path, load_sid, sessions))
            elif etype in ("trace_session_start", "trace_event", "trace_session_end"):
                # Live-ingest path: a producer process is streaming events into
                # this server.  Buffer each message and broadcast to all consumers.
                # Append before trim so the count cap is enforced immediately after
                # each message lands — the buffer never exceeds max_events by more
                # than 0 (vs. trim-before-append which allows a transient +1).
                now_ns = time.monotonic_ns()
                ring_buffer.append((now_ns, msg))
                trim_ring_buffer(ring_buffer, now_ns, buffer_seconds, max_events)
                await broadcast(msg, connections, exclude=ws)

                # Recording sink (Phase 9.3, ADR-0020 amendment): tee live
                # sessions to JSONL + register in the store, when enabled.
                # recordings_dir is only ever set (in serve()) when store is
                # also set, but both are independent Optional params here, so
                # narrow on both for mypy --strict.
                if recordings_dir is not None and store is not None:
                    if etype == "trace_session_start":
                        if recorder is not None:
                            # Defensive: a new session started without a
                            # matching end for the previous one — finalize it
                            # first so its file/row are not left dangling.
                            await recorder.finalize()
                            recorder = None
                        sid = envelope["payload"].get("session_id")
                        if isinstance(sid, str) and sid and sid != file_session_id:
                            recorder = RecordingSink(recordings_dir, sid, store, recording_language)
                    elif etype == "trace_event":
                        if recorder is not None:
                            payload = envelope["payload"]
                            if isinstance(payload, dict):
                                recorder.write(payload)
                    elif etype == "trace_session_end":
                        if recorder is not None:
                            await recorder.finalize()
                            recorder = None
    finally:
        if recorder is not None:
            # Shield so a finalize triggered by the outer cancellation (producer
            # disconnect or server shutdown) still completes the close+rename+
            # save_session sequence rather than being torn mid-await.
            await asyncio.shield(recorder.finalize())


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
                      ``file_replay._MAX_GAP_S``).  When False events are pushed
                      as fast as the socket allows — useful for tests and CI.
        store:        Optional session library.  When set, the ``--trace-source``
                      file (if any) is indexed into it and ``session_list`` /
                      ``session_load`` requests are served from it.  Closed when
                      ``serve`` exits.
    """
    root_real = root.resolve()

    # Closure-scoped connection registry — every consumer connection lives here.
    connections: set[ServerConnection] = set()
    # Ring-buffer for live-attach late joiners.  Populated only by live ingest;
    # not used in file-replay mode (each connection re-reads the file).
    ring_buffer: collections.deque[tuple[int, str]] = collections.deque()
    buffer_seconds = trace_buffer_seconds()
    max_events = trace_buffer_max_events()

    # Agent-side analysis cache (hub-score + cycles), shared across connects so
    # the Tarjan SCC pass runs once per distinct graph topology, not per tab.
    meta_cache: dict[tuple[int, int, int], dict[str, Any]] = {}

    # Registry of seekable/queryable sessions, keyed by session id.  Shared
    # across connections so a session loaded by one tab is queryable by all.
    seekable_sessions: dict[str, SeekableSession] = {}

    # Live-stream recording sink (Phase 9.3, ADR-0020 amendment): when a
    # session store is present, inbound producer sessions are tee'd to
    # <db_dir>/recordings/<session_id>.jsonl and registered in the store.
    # Language is detected once (root is fixed for the server's lifetime)
    # rather than per-session on the event loop.
    recordings_dir: Path | None = None
    recording_language = "python"
    if store is not None:
        from grackle.adapters import registry

        recordings_dir = store.db_path.parent / "recordings"
        recordings_dir.mkdir(parents=True, exist_ok=True)
        sweep_orphaned_recordings(recordings_dir)
        try:
            detected = registry.detect(root_real)
            recording_language = detected[0] if detected else "python"
        except Exception:
            recording_language = "python"

    # File-replay mode: build the index + aggregates once in a single pass so
    # every connection can seek and query without re-scanning.  The session_id
    # is stable across all connections — the browser receives the same id on
    # reconnect and can continue using it in seek/query requests.
    file_session_id: str | None = None
    if trace_source is not None:
        from grackle.python_runtime.aggregates import build_seekable

        file_session_id = str(uuid4())
        try:
            idx, agg = build_seekable(trace_source)
            seekable_sessions[file_session_id] = SeekableSession(index=idx, aggregates=agg)
            log.info("trace index + aggregates built", path=str(trace_source), events=len(idx))
            if store is not None:
                register_trace_source(store, trace_source, idx, root_real)
        except Exception as exc:
            log.warning(
                "trace index/aggregates build failed — seek + query disabled",
                path=str(trace_source),
                error=str(exc),
            )

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
            await _push_static_graph(ws, root_real, meta_cache)

            # Flush ring-buffer history to late joiners (live mode only).
            if trace_source is None:
                await flush_ring_buffer(ws, ring_buffer)

            # Receive loop handles ping, read_source, live-ingest, seek, query,
            # and session library requests.
            receive_task: asyncio.Task[None] = asyncio.create_task(
                _receive_loop(
                    ws,
                    root_real,
                    connections,
                    ring_buffer,
                    buffer_seconds,
                    max_events,
                    seekable_sessions=seekable_sessions,
                    store=store,
                    recordings_dir=recordings_dir,
                    recording_language=recording_language,
                    file_session_id=file_session_id,
                )
            )
            tasks.append(receive_task)

            # File-replay task (one per connection, independent of live ingest).
            # Uses the stable file_session_id and advertises seekable=True so the
            # browser can send trace_seek_request / trace_query_request messages.
            if trace_source is not None and file_session_id is not None:
                file_session = seekable_sessions.get(file_session_id)
                seekable = file_session is not None
                total = len(file_session.index) if file_session is not None else 0
                replay_task: asyncio.Task[None] = asyncio.create_task(
                    replay_trace(
                        ws,
                        trace_source,
                        pace,
                        file_session_id,
                        seekable=seekable,
                        total_events=total,
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
    try:
        async with _ws_serve(_handler, host, port):
            log.info("agent listening", host=host, port=port, root=str(root_real))
            await asyncio.Future()  # run until cancelled
    finally:
        if store is not None:
            store.close()
