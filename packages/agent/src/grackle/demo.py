"""Demo server — parses real project roots via AdapterRegistry.parse_all.

Phase 5+6 sync: real golden-trace replay replaces the Phase 4 pulse-loop mock.
The demo emits genuine trace_session_start / trace_event* / trace_session_end
envelopes consumed by the real Phase 6.3 Timeline + heat-map overlay.

Not part of the production server.py — this module is throwaway by design.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import typing
from typing import TYPE_CHECKING, Any
from uuid import uuid4

import structlog
import websockets.exceptions
from websockets.asyncio.server import ServerConnection
from websockets.asyncio.server import serve as _ws_serve

if TYPE_CHECKING:
    from pathlib import Path

log = structlog.get_logger()

# Mirror server._MAX_GAP_S: clamp inter-event sleep so long pauses don't stall replay.
_MAX_GAP_S = 0.25


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


class _DemoServer:
    """Multi-fixture demo server backed by AdapterRegistry.parse_all.

    Each fixture is a project root path (Python, Go, Rust, or polyglot).
    Parses on first request and caches the result in-memory.

    On connect (and after load_fixture) the server replays the fixture's
    golden trace file (if present) as real trace_session_start / trace_event*
    / trace_session_end envelopes — the same protocol the production server
    emits for ``grackle serve --trace-source``.  The frontend's Timeline panel
    and node heat-map consume these transparently.

    Fixtures without a golden trace (Rust, Go, polyglot) render as static-only.
    """

    def __init__(
        self,
        fixture_roots: dict[str, Path],
        default: str,
        loop_trace: bool = False,
        pace: bool = True,
    ) -> None:
        if default not in fixture_roots:
            raise ValueError(f"default fixture {default!r} not in {list(fixture_roots)}")
        self._fixture_roots = fixture_roots
        self._default = default
        self._active = default
        self._loop_trace = loop_trace
        self._pace = pace
        self._clients: set[ServerConnection] = set()
        self._cache: dict[str, dict[str, Any]] = {}
        # Per-connection in-flight replay task; cancelled on disconnect / fixture switch.
        self._replay_tasks: dict[ServerConnection, asyncio.Task[None]] = {}

    # ---- helpers ----

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
                from grackle.adapters import registry
                from grackle.adapters.base import ParseOptions

                log.info("demo: parsing fixture", name=name, root=str(root))
                try:
                    parsed = registry.parse_all(root, ParseOptions())
                    result = typing.cast("dict[str, Any]", parsed)
                except Exception as exc:
                    log.error("demo: parse failed", name=name, error=str(exc))
                    result = _empty_graph(str(exc))
            self._cache[name] = result
        return self._cache[name]

    def _fixture_summary(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for name in self._fixture_roots:
            # Parse/load eagerly so the switcher dropdown can show real node and
            # edge counts up front (the whole point of the size-tier presets).
            # Results are cached, so this is a one-time cost per fixture.
            graph = self._parse(name)
            trace_path = _trace_path_for(self._fixture_roots[name])
            out.append(
                {
                    "name": name,
                    "label": name.capitalize(),
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

    async def _broadcast(self, envelope: dict[str, Any]) -> None:
        msg = json.dumps(envelope)
        dead: list[ServerConnection] = []
        for client in self._clients:
            try:
                await client.send(msg)
            except websockets.exceptions.ConnectionClosed:
                dead.append(client)
        for d in dead:
            self._clients.discard(d)

    # ---- trace replay ----

    async def _replay_once(self, ws: ServerConnection, root: Path, session_id: str) -> None:
        """Replay root/trace.golden.jsonl to ws as one real trace session.

        Mirrors server._replay_trace: paced using ts_ns deltas clamped to
        _MAX_GAP_S, then session_end.  No-ops (cleanly) if no trace file.
        """
        from grackle import protocol
        from grackle.python_runtime.writer import read_jsonl

        trace_path = _trace_path_for(root)
        if trace_path is None:
            return

        try:
            events = read_jsonl(trace_path)
        except Exception as exc:
            log.warning("demo: trace read failed", path=str(trace_path), error=str(exc))
            return

        started_ns = time.monotonic_ns()
        try:
            await ws.send(protocol.make_trace_session_start(session_id, started_ns, source="demo"))
        except websockets.exceptions.ConnectionClosed:
            return

        prev_ts_ns: int | None = None
        for event in events:
            if self._pace and prev_ts_ns is not None:
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
            await ws.send(
                protocol.make_trace_session_end(session_id, time.monotonic_ns(), len(events))
            )
        except websockets.exceptions.ConnectionClosed:
            return

    async def _replay_loop(self, ws: ServerConnection, root: Path) -> None:
        """Replay trace to ws; repeat if --loop was given."""
        try:
            while True:
                await self._replay_once(ws, root, str(uuid4()))
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
        root = self._fixture_roots[self._active]
        if _trace_path_for(root) is None:
            return  # static-only fixture — no replay
        task = asyncio.create_task(self._replay_loop(ws, root))
        self._replay_tasks[ws] = task

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
        elif etype == "read_source":
            await self._handle_read_source(ws, envelope)

    async def _handle_read_source(self, ws: ServerConnection, envelope: dict[str, Any]) -> None:
        """Serve a node's source for the active fixture.

        Real project fixtures read the file off disk (reusing the production
        path-safety check).  Synthetic size-tier presets have no backing files,
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

    async def _send_active_graph(self, ws: ServerConnection) -> None:
        graph = self._parse(self._active)
        node_count = len(graph.get("nodes", []))
        edge_count = len(graph.get("edges", []))
        log.info("demo: pushing graph", name=self._active, nodes=node_count, edges=edge_count)
        await self._send(
            ws,
            {
                "id": f"graph-{self._active}",
                "type": "static_graph",
                "payload": graph,
            },
        )

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
    server = _DemoServer(fixture_roots, default=default, loop_trace=loop_trace, pace=pace)
    await server.serve(host, port)
