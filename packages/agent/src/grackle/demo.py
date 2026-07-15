"""Demo server — parses real project roots via AdapterRegistry.parse_all.

Phase 10.D sync: delegates parsing to server._build_static_graph (hub-score +
cycles enrichment) and trace replay to python_runtime.file_replay.replay_trace
— the same code paths production `grackle serve` uses — instead of
hand-rolled copies. Also seeds a real SessionStore from the golden-trace
fixtures (Phase 8.3 session library) and runs a canned watch-mode simulation
(Phase 10.6/10.7) that re-pushes a mutated graph variant through the exact
production `protocol.make_static_graph` envelope, driving the same
graph-diff animation a real file edit under `serve --watch` would.

Not part of the production server.py — this module is throwaway by design.
"""

from __future__ import annotations

import asyncio
import json
import os
import tempfile
import typing
from pathlib import Path
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog
import websockets.exceptions
from websockets.asyncio.server import ServerConnection
from websockets.asyncio.server import serve as _ws_serve

if TYPE_CHECKING:
    from grackle.python_runtime.file_replay import SeekableSession
    from grackle.session_store import SessionStore

log = structlog.get_logger()

# Fixture name that triggers the watch-mode diff-animation simulation (see
# _watch_sim_loop) instead of golden-trace replay.
_WATCH_SIM_FIXTURE = "watch"
_WATCH_SIM_INTERVAL_S = 4.0

# Mirrors server._MAX_SEEK_COUNT — bounds per-request I/O for trace_seek_request.
_MAX_SEEK_COUNT = 1000


def _allowed_origins() -> list[str]:
    env = os.environ.get("GRACKLE_ALLOWED_ORIGINS")
    if env:
        return [o.strip() for o in env.split(",")]
    return ["http://localhost:5173", "http://127.0.0.1:5173"]


def _empty_graph(error: str) -> dict[str, Any]:
    """Fallback graph payload when a fixture fails to load or parse."""
    return {
        "version": 1,
        "language": "python",
        "nodes": [],
        "edges": [],
        "metadata": {"parseWarnings": [], "parseErrors": [error]},
    }


def _trace_path_for(root: Path) -> Path | None:
    """Return the golden trace for *root* if present, else None."""
    candidate = root / "trace.golden.jsonl"
    return candidate if candidate.exists() else None


# Cosmetic label overrides for fixtures whose name.capitalize() reads oddly
# (e.g. "nn" -> "Nn").
_LABEL_OVERRIDES: dict[str, str] = {"nn": "NN"}


def _label_for(name: str) -> str:
    return _LABEL_OVERRIDES.get(name, name.capitalize())


def _mutate_graph_variant(graph: dict[str, Any]) -> dict[str, Any]:
    """Return a deep copy of *graph* with one node dropped and one added.

    Feeds the watch-mode simulation: the frontend's graph-diff animation
    (Phase 10.7) reacts to node/edge deltas, not to *how* the graph changed,
    so a synthetic mutation exercises the exact same pulse-in/fade-out path a
    real file edit under `serve --watch` would — no filesystem watcher
    needed. Deliberately generic (keys off whatever nodes are present) so it
    works for any project fixture, not just one hardcoded shape.
    """
    import copy

    variant = copy.deepcopy(graph)
    nodes: list[dict[str, Any]] = variant.get("nodes", [])
    edges: list[dict[str, Any]] = variant.get("edges", [])
    if not nodes:
        return variant

    removed_id = nodes[-1]["id"]
    variant["nodes"] = [n for n in nodes if n["id"] != removed_id]
    variant["edges"] = [e for e in edges if e["source"] != removed_id and e["target"] != removed_id]

    anchor = nodes[0]
    new_node = {
        "id": f"{anchor['path']}:_demo_watch_pulse",
        "kind": "function",
        "name": "_demo_watch_pulse",
        "path": anchor["path"],
    }
    variant["nodes"].append(new_node)
    variant["edges"].append({"source": anchor["id"], "target": new_node["id"], "kind": "call"})
    return variant


def _seed_session_store(
    fixture_roots: dict[str, Path], trace_overrides: dict[str, Path]
) -> SessionStore:
    """Seed a real SessionStore from every fixture with a golden trace.

    Backed by a fresh temp-dir sqlite file (this whole module is throwaway —
    nothing here should touch the repo tree). Lets the demo exercise the real
    Session Library panel (Phase 8.3: list + seekable load_stored_session)
    against real data instead of a hand-rolled canned response.
    """
    from grackle.python_runtime.file_replay import detect_language
    from grackle.python_runtime.writer import read_jsonl
    from grackle.session_store import SessionMeta, SessionStore

    db_path = Path(tempfile.mkdtemp(prefix="grackle-demo-")) / "sessions.db"
    store = SessionStore.open(db_path)
    for name, root in fixture_roots.items():
        trace_path = trace_overrides.get(name) or _trace_path_for(root)
        if trace_path is None or not trace_path.exists():
            continue
        try:
            events = read_jsonl(trace_path)
        except Exception as exc:
            log.warning("demo: session seed skipped", name=name, error=str(exc))
            continue
        if not events:
            continue
        store.save_session(
            SessionMeta(
                id=str(uuid4()),
                label=_label_for(name),
                started_ns=events[0]["ts_ns"],
                ended_ns=events[-1]["ts_ns"],
                source_path=str(trace_path.resolve()),
                event_count=len(events),
                language=detect_language(root),
            )
        )
    return store


class _DemoServer:
    """Multi-fixture demo server backed by AdapterRegistry.parse_all.

    Each fixture is a project root path (Python, TypeScript, Go, Rust, or
    polyglot) or a pre-built synthetic graph JSON. Parses on first request
    and caches the result in-memory.

    On connect (and after load_fixture) the server replays the fixture's
    golden trace file (if present) as real trace_session_start / trace_event*
    / trace_session_end envelopes — the same protocol the production server
    emits for ``grackle serve --trace-source``. Fixtures without a golden
    trace render as static-only, except the dedicated `watch` fixture, which
    runs a canned graph-diff simulation instead (see _watch_sim_loop).
    """

    def __init__(
        self,
        fixture_roots: dict[str, Path],
        default: str,
        store: SessionStore,
        loop_trace: bool = False,
        pace: bool = True,
        trace_overrides: dict[str, Path] | None = None,
    ) -> None:
        if default not in fixture_roots:
            raise ValueError(f"default fixture {default!r} not in {list(fixture_roots)}")
        self._fixture_roots = fixture_roots
        self._default = default
        self._active = default
        self._loop_trace = loop_trace
        self._pace = pace
        self._store = store
        self._trace_overrides = trace_overrides or {}
        self._clients: set[ServerConnection] = set()
        self._cache: dict[str, dict[str, Any]] = {}
        self._meta_cache: dict[tuple[int, int, int], dict[str, Any]] = {}
        self._seekable_sessions: dict[str, SeekableSession] = {}
        # Per-connection in-flight replay task; cancelled on disconnect / fixture switch.
        self._replay_tasks: dict[ServerConnection, asyncio.Task[None]] = {}
        # Server-wide (not per-connection) watch-mode simulation task — mirrors
        # production watch mode, which broadcasts to every connected client.
        self._watch_sim_task: asyncio.Task[None] | None = None

    # ---- helpers ----

    def _trace_for(self, name: str) -> Path | None:
        """Golden trace for fixture *name* — an override if registered, else co-located."""
        override = self._trace_overrides.get(name)
        if override is not None:
            return override if override.exists() else None
        return _trace_path_for(self._fixture_roots[name])

    def _parse(self, name: str) -> dict[str, Any]:
        if name not in self._cache:
            root = self._fixture_roots[name]
            result: dict[str, Any]
            if root.suffix == ".json":
                # Pre-built synthetic graph fixture (the size-tier presets) —
                # load the graph JSON directly; nothing to parse. Static-only
                # (no golden trace), so it exercises the visualization at scale.
                log.info("demo: loading graph fixture", name=name, path=str(root))
                try:
                    result = json.loads(root.read_text(encoding="utf-8"))
                except Exception as exc:
                    log.error("demo: graph load failed", name=name, error=str(exc))
                    result = _empty_graph(str(exc))
            else:
                from grackle.server import _build_static_graph

                log.info("demo: parsing fixture", name=name, root=str(root))
                graph = _build_static_graph(root, self._meta_cache)
                result = (
                    typing.cast("dict[str, Any]", graph)
                    if graph is not None
                    else _empty_graph("no language detected or parse failed")
                )
            self._cache[name] = result
        return self._cache[name]

    def _fixture_summary(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for name in self._fixture_roots:
            # Parse/load eagerly so the switcher dropdown can show real node and
            # edge counts up front (the whole point of the size-tier presets).
            # Results are cached, so this is a one-time cost per fixture.
            graph = self._parse(name)
            trace_path = self._trace_for(name)
            out.append(
                {
                    "name": name,
                    "label": _label_for(name),
                    "nodeCount": len(graph.get("nodes", [])),
                    "edgeCount": len(graph.get("edges", [])),
                    "hasTrace": trace_path is not None,
                }
            )
        out.sort(key=lambda f: f["nodeCount"])
        return out

    async def _send(self, ws: ServerConnection, envelope: dict[str, Any]) -> None:
        try:
            await ws.send(json.dumps(envelope))
        except websockets.exceptions.ConnectionClosed:
            self._clients.discard(ws)

    async def _send_raw(self, ws: ServerConnection, message: str) -> None:
        """Send a pre-serialized envelope string (from protocol.make_*)."""
        try:
            await ws.send(message)
        except websockets.exceptions.ConnectionClosed:
            self._clients.discard(ws)

    async def _broadcast_raw(self, message: str) -> None:
        """Send a pre-serialized envelope string to every connected client."""
        dead: list[ServerConnection] = []
        for client in self._clients:
            try:
                await client.send(message)
            except websockets.exceptions.ConnectionClosed:
                dead.append(client)
        for d in dead:
            self._clients.discard(d)

    # ---- trace replay ----

    async def _replay_loop(self, ws: ServerConnection, trace_path: Path) -> None:
        """Replay trace_path to ws; repeat if --loop was given.

        Delegates to the production non-seekable replay path
        (python_runtime.file_replay.replay_trace) instead of a hand-rolled
        copy, so the demo tracks any future changes to pacing / the
        session envelope for free.
        """
        from grackle.python_runtime.file_replay import replay_trace

        try:
            while True:
                await replay_trace(ws, trace_path, self._pace, str(uuid4()))
                if not self._loop_trace:
                    break
                # brief pause between loop iterations
                await asyncio.sleep(2.0)
        except asyncio.CancelledError:
            pass

    def _cancel_replay(self, ws: ServerConnection) -> None:
        task = self._replay_tasks.pop(ws, None)
        if task is not None and not task.done():
            task.cancel()

    def _start_replay(self, ws: ServerConnection) -> None:
        """Cancel any existing replay for ws, start a fresh one for the active fixture."""
        self._cancel_replay(ws)
        trace_path = self._trace_for(self._active)
        if trace_path is None:
            return  # static-only fixture — no replay
        task = asyncio.create_task(self._replay_loop(ws, trace_path))
        self._replay_tasks[ws] = task

    # ---- watch-mode simulation (Phase 10.6/10.7 preview) ----

    async def _watch_sim_loop(self) -> None:
        """Periodically re-push a mutated graph variant for the active fixture.

        No filesystem watcher — visitor "edits" aren't real file changes, so
        this replays a precomputed mutation instead. The envelope itself
        (protocol.make_static_graph) is byte-for-byte what a real
        `serve --watch` re-push sends, so the frontend's graph-diff animation
        (applyGraphDiff + the enter/exit pulse) runs exactly as it would for
        a genuine edit. See DEMO_BRANCH.md's mock table.
        """
        from grackle import protocol

        base = self._parse(self._active)
        toggled = False
        try:
            while True:
                await asyncio.sleep(_WATCH_SIM_INTERVAL_S)
                toggled = not toggled
                graph = _mutate_graph_variant(base) if toggled else base
                await self._broadcast_raw(protocol.make_static_graph(typing.cast("Any", graph)))
        except asyncio.CancelledError:
            pass

    def _start_watch_sim(self) -> None:
        if self._active != _WATCH_SIM_FIXTURE:
            self._cancel_watch_sim()
            return
        if self._watch_sim_task is not None and not self._watch_sim_task.done():
            return  # already running — a new connection joins the shared simulation
        self._watch_sim_task = asyncio.create_task(self._watch_sim_loop())

    def _cancel_watch_sim(self) -> None:
        if self._watch_sim_task is not None and not self._watch_sim_task.done():
            self._watch_sim_task.cancel()
        self._watch_sim_task = None

    # ---- message handling ----

    async def _handle_envelope(self, ws: ServerConnection, envelope: dict[str, Any]) -> None:
        etype = envelope.get("type")
        if etype == "load_fixture":
            name = envelope.get("payload", {}).get("name")
            if not isinstance(name, str) or name not in self._fixture_roots:
                log.warning("load_fixture: unknown fixture", requested=name)
                return
            self._active = name
            log.info("load_fixture", name=name)
            await self._send_active_graph(ws)
            self._start_replay(ws)
            self._start_watch_sim()
        elif etype == "read_source":
            await self._handle_read_source(ws, envelope)
        elif etype == "session_list_request":
            await self._handle_session_list(ws, envelope)
        elif etype == "session_load_request":
            await self._handle_session_load(ws, envelope)
        elif etype == "trace_seek_request":
            await self._handle_trace_seek(ws, envelope)

    async def _handle_read_source(self, ws: ServerConnection, envelope: dict[str, Any]) -> None:
        """Serve a node's source for the active fixture.

        Real project fixtures read the file off disk (reusing the production
        path-safety check). Synthetic size-tier presets have no backing files,
        so they answer with a clean source_error instead of letting the
        frontend's read_source request time out.
        """
        from grackle import protocol
        from grackle.server import _read_source

        request_id = envelope.get("id", "")
        path = envelope.get("payload", {}).get("path", "")
        if not isinstance(request_id, str) or not isinstance(path, str):
            return

        root = self._fixture_roots[self._active]
        if root.suffix == ".json":
            await self._send_raw(
                ws,
                protocol.make_source_error(
                    request_id, path, "synthetic demo fixture — no source files"
                ),
            )
            return

        source, enc_or_reason = _read_source(root.resolve(), path)
        if source is not None:
            await self._send_raw(
                ws,
                protocol.make_source_response(request_id, path, source, enc_or_reason),
            )
        else:
            await self._send_raw(ws, protocol.make_source_error(request_id, path, enc_or_reason))

    async def _handle_session_list(self, ws: ServerConnection, envelope: dict[str, Any]) -> None:
        """List the SessionStore seeded from the golden-trace fixtures (Phase 8.3)."""
        from grackle import protocol

        request_id = envelope.get("id", "")
        if not isinstance(request_id, str):
            return
        metas = self._store.list_sessions()
        sessions_data = [
            {
                "id": m.id,
                "label": m.label,
                "started_ns": m.started_ns,
                "ended_ns": m.ended_ns,
                "source_path": m.source_path,
                "event_count": m.event_count,
                "language": m.language,
            }
            for m in metas
        ]
        await self._send_raw(ws, protocol.make_session_list_response(request_id, sessions_data))

    async def _handle_session_load(self, ws: ServerConnection, envelope: dict[str, Any]) -> None:
        """Load a stored session as a seekable replay — the real production path."""
        from grackle.python_runtime.file_replay import load_stored_session

        load_sid = envelope.get("payload", {}).get("session_id", "")
        if not isinstance(load_sid, str) or not load_sid:
            return
        meta = self._store.get_session(load_sid)
        if meta is None:
            log.warning("demo: session load: unknown session id", session_id=load_sid)
            return
        load_path = Path(meta.source_path)
        if not load_path.exists():
            log.warning(
                "demo: session load: source file missing",
                session_id=load_sid,
                path=str(load_path),
            )
            return
        asyncio.create_task(load_stored_session(ws, load_path, load_sid, self._seekable_sessions))

    async def _handle_trace_seek(self, ws: ServerConnection, envelope: dict[str, Any]) -> None:
        """Serve a window of events from a seekable session (Phase 7.3).

        Mirrors server.py's trace_seek_request handling — without this a
        session loaded via session_load_request has no way to actually
        deliver its events, since replay_trace(seekable=True) sends only the
        start/end markers, never trace_event.
        """
        from grackle import protocol

        request_id = envelope.get("id", "")
        payload = envelope.get("payload", {})
        seek_sid = payload.get("session_id", "")
        if not isinstance(request_id, str) or not isinstance(seek_sid, str):
            return

        sess = self._seekable_sessions.get(seek_sid)
        if sess is None:
            await self._send_raw(
                ws, protocol.make_trace_seek_error(request_id, seek_sid, "session not found")
            )
            return

        start_raw = payload.get("start_index", 0)
        count_raw = payload.get("count", 0)
        if not isinstance(start_raw, int) or not isinstance(count_raw, int):
            await self._send_raw(
                ws,
                protocol.make_trace_seek_error(
                    request_id, seek_sid, "invalid start_index or count"
                ),
            )
            return

        count_capped = min(max(0, count_raw), _MAX_SEEK_COUNT)
        trace_index = sess.index
        total = len(trace_index)
        clamped_start = max(0, min(start_raw, total))
        try:
            loop = asyncio.get_running_loop()
            seek_events = await loop.run_in_executor(
                None, trace_index.read_window, start_raw, count_capped
            )
        except Exception as exc:
            log.warning("demo: trace seek read_window failed", error=str(exc))
            await self._send_raw(
                ws, protocol.make_trace_seek_error(request_id, seek_sid, "read error")
            )
            return

        await self._send_raw(
            ws,
            protocol.make_trace_window(request_id, seek_sid, clamped_start, seek_events, total),
        )

    async def _send_active_graph(self, ws: ServerConnection) -> None:
        from grackle import protocol

        graph = self._parse(self._active)
        node_count = len(graph.get("nodes", []))
        edge_count = len(graph.get("edges", []))
        log.info("demo: pushing graph", name=self._active, nodes=node_count, edges=edge_count)
        await self._send_raw(ws, protocol.make_static_graph(typing.cast("Any", graph)))

    async def _handler(self, ws: ServerConnection) -> None:
        origin = ws.request.headers.get("Origin", "") if ws.request is not None else ""
        if not origin or origin not in _allowed_origins():
            await ws.close(1008, "Origin not allowed")
            return

        log.info("demo client connected", remote=ws.remote_address)
        self._clients.add(ws)
        try:
            # ADR-0014 race guarantee: static_graph must arrive before trace_session_start.
            await self._send_active_graph(ws)
            await self._send(
                ws,
                {
                    "id": "agent-hello-1",
                    "type": "agent_hello",
                    "payload": {
                        "fixtures": self._fixture_summary(),
                        "active": self._active,
                    },
                },
            )
            self._start_replay(ws)
            self._start_watch_sim()

            async for raw in ws:
                try:
                    msg = raw.decode() if isinstance(raw, bytes) else raw
                except UnicodeDecodeError:
                    continue
                try:
                    envelope = json.loads(msg)
                except (json.JSONDecodeError, TypeError):
                    continue
                if isinstance(envelope, dict):
                    await self._handle_envelope(ws, envelope)
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            self._cancel_replay(ws)
            self._clients.discard(ws)
            log.info("demo client disconnected", remote=ws.remote_address)
            if not self._clients:
                self._cancel_watch_sim()

    async def serve(self, host: str, port: int) -> None:
        async with _ws_serve(self._handler, host, port):
            log.info(
                "demo agent listening",
                host=host,
                port=port,
                fixtures=list(self._fixture_roots),
                default=self._default,
                loop=self._loop_trace,
                pace=self._pace,
            )
            await asyncio.Future()


async def serve_demo(
    host: str,
    port: int,
    fixture_roots: dict[str, Path],
    default: str,
    loop_trace: bool = False,
    pace: bool = True,
    trace_overrides: dict[str, Path] | None = None,
) -> None:
    """Entry point called by ``grackle demo``."""
    if not fixture_roots:
        raise ValueError("no fixture roots provided")
    if default not in fixture_roots:
        log.warning(
            "default fixture not found; falling back",
            requested=default,
            available=list(fixture_roots),
        )
        default = next(iter(fixture_roots))
    trace_overrides = trace_overrides or {}
    store = _seed_session_store(fixture_roots, trace_overrides)
    try:
        server = _DemoServer(
            fixture_roots,
            default=default,
            store=store,
            loop_trace=loop_trace,
            pace=pace,
            trace_overrides=trace_overrides,
        )
        await server.serve(host, port)
    finally:
        store.close()
