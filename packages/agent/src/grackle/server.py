from __future__ import annotations

import asyncio
import os
from pathlib import Path

import structlog
import websockets.exceptions
from websockets.asyncio.server import ServerConnection
from websockets.asyncio.server import serve as _ws_serve

from grackle import protocol

log = structlog.get_logger()

_MAX_SOURCE_BYTES = 1 * 1024 * 1024  # 1 MiB


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


async def serve(host: str, port: int, root: Path = Path()) -> None:
    """Start the WebSocket server and run until cancelled."""
    root_real = root.resolve()

    async def _handler(ws: ServerConnection) -> None:
        origin = ws.request.headers.get("Origin", "") if ws.request is not None else ""
        if origin and origin not in _allowed_origins():
            await ws.close(1008, "Origin not allowed")
            return

        log.info("client connected", remote=ws.remote_address)
        await _push_static_graph(ws, root_real)

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
        except websockets.exceptions.ConnectionClosed:
            pass
        finally:
            log.info("client disconnected", remote=ws.remote_address)

    if host not in ("127.0.0.1", "localhost", "::1"):
        log.warning("binding to non-loopback address — agent reachable from network", host=host)
    async with _ws_serve(_handler, host, port):
        log.info("agent listening", host=host, port=port, root=str(root_real))
        await asyncio.Future()  # run until cancelled
