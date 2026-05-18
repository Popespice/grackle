"""Demo server — parses real project roots via AdapterRegistry.parse_all.

Replaces hand-authored JSON fixtures from Phase 1. The pulse-loop preview of
the Phase 6/7 runtime overlay stays until those phases ship.

Not part of the production server.py — this module is throwaway by design.
"""

from __future__ import annotations

import asyncio
import json
import os
import random
import typing
from typing import TYPE_CHECKING, Any

import structlog
import websockets.exceptions
from websockets.asyncio.server import ServerConnection
from websockets.asyncio.server import serve as _ws_serve

if TYPE_CHECKING:
    from pathlib import Path

log = structlog.get_logger()


def _allowed_origins() -> list[str]:
    env = os.environ.get("GRACKLE_ALLOWED_ORIGINS")
    if env:
        return [o.strip() for o in env.split(",")]
    return ["http://localhost:5173", "http://127.0.0.1:5173"]


class _DemoServer:
    """Multi-fixture demo server backed by AdapterRegistry.parse_all.

    Each fixture is a project root path (Python, Go, or polyglot). Parses on
    first request and caches the result in-memory. The pulse loop samples node
    IDs from the currently-active parsed graph.
    """

    def __init__(self, fixture_roots: dict[str, Path], default: str, live: bool) -> None:
        if default not in fixture_roots:
            raise ValueError(f"default fixture {default!r} not in {list(fixture_roots)}")
        self._fixture_roots = fixture_roots
        self._default = default
        self._active = default
        self._live = live
        self._clients: set[ServerConnection] = set()
        self._cache: dict[str, dict[str, Any]] = {}
        self._pulse_interval_s = 1.5
        self._pulse_nodes_per_pulse = 3

    # ---- helpers ----

    def _parse(self, name: str) -> dict[str, Any]:
        if name not in self._cache:
            from grackle.adapters import registry
            from grackle.adapters.base import ParseOptions

            root = self._fixture_roots[name]
            log.info("demo: parsing fixture", name=name, root=str(root))
            try:
                parsed = registry.parse_all(root, ParseOptions())
                result: dict[str, Any] = typing.cast("dict[str, Any]", parsed)
            except Exception as exc:
                log.error("demo: parse failed", name=name, error=str(exc))
                result = {
                    "version": 1,
                    "language": "python",
                    "nodes": [],
                    "edges": [],
                    "metadata": {"parseWarnings": [], "parseErrors": [str(exc)]},
                }
            self._cache[name] = result
        return self._cache[name]

    def _current_node_ids(self) -> list[str]:
        graph = self._cache.get(self._active, {})
        return [n["id"] for n in graph.get("nodes", []) if isinstance(n, dict) and "id" in n]

    def _fixture_summary(self) -> list[dict[str, Any]]:
        out: list[dict[str, Any]] = []
        for name in self._fixture_roots:
            cached = self._cache.get(name)
            out.append(
                {
                    "name": name,
                    "label": name.capitalize(),
                    "nodeCount": len(cached["nodes"]) if cached else None,
                    "edgeCount": len(cached["edges"]) if cached else None,
                }
            )
        out.sort(key=lambda f: (f["nodeCount"] is None, f["name"]))
        return out

    async def _send(self, ws: ServerConnection, envelope: dict[str, Any]) -> None:
        try:
            await ws.send(json.dumps(envelope))
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

    # ---- core loops ----

    async def _pulse_loop(self) -> None:
        counter = 0
        rng = random.Random()
        while True:
            await asyncio.sleep(max(0.005, self._pulse_interval_s))
            if not self._clients:
                continue
            node_ids = self._current_node_ids()
            if not node_ids:
                continue
            upper = max(1, self._pulse_nodes_per_pulse)
            count = rng.randint(1, upper)
            sample = rng.sample(node_ids, min(count, len(node_ids)))
            counter += 1
            await self._broadcast(
                {
                    "id": f"pulse-{counter}",
                    "type": "pulse",
                    "payload": {"nodes": sample},
                }
            )

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
        elif etype == "set_pulse_rate":
            raw = envelope.get("payload")
            payload: dict[str, Any] = raw if isinstance(raw, dict) else {}
            interval_ms = payload.get("intervalMs")
            nodes_per_pulse = payload.get("nodesPerPulse")
            if isinstance(interval_ms, (int, float)) and interval_ms >= 5:
                self._pulse_interval_s = float(interval_ms) / 1000.0
            if isinstance(nodes_per_pulse, int) and nodes_per_pulse >= 1:
                self._pulse_nodes_per_pulse = nodes_per_pulse
            log.info(
                "set_pulse_rate",
                interval_ms=int(self._pulse_interval_s * 1000),
                nodes_per_pulse=self._pulse_nodes_per_pulse,
            )

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
        if origin and origin not in _allowed_origins():
            await ws.close(1008, "Origin not allowed")
            return

        log.info("demo client connected", remote=ws.remote_address)
        self._clients.add(ws)
        try:
            await self._send_active_graph(ws)
            await self._send(
                ws,
                {
                    "id": "agent-hello-1",
                    "type": "agent_hello",
                    "payload": {
                        "fixtures": self._fixture_summary(),
                        "active": self._active,
                        "live": self._live,
                        "pulseIntervalMs": int(self._pulse_interval_s * 1000),
                        "pulseNodesPerPulse": self._pulse_nodes_per_pulse,
                    },
                },
            )

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
                live=self._live,
            )
            tasks: list[asyncio.Task[None]] = []
            if self._live:
                tasks.append(asyncio.create_task(self._pulse_loop()))
            try:
                await asyncio.Future()
            finally:
                for t in tasks:
                    t.cancel()


async def serve_demo(
    host: str, port: int, fixture_roots: dict[str, Path], default: str, live: bool
) -> None:
    if not fixture_roots:
        raise ValueError("no fixture roots provided")
    if default not in fixture_roots:
        log.warning(
            "default fixture not found; falling back",
            requested=default,
            available=list(fixture_roots),
        )
        default = next(iter(fixture_roots))
    server = _DemoServer(fixture_roots, default=default, live=live)
    await server.serve(host, port)
