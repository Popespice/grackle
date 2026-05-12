import asyncio
import os

import structlog
from websockets.asyncio.server import ServerConnection
from websockets.asyncio.server import serve as _ws_serve

from grackle import protocol

log = structlog.get_logger()


def _allowed_origins() -> list[str]:
    env = os.environ.get("GRACKLE_ALLOWED_ORIGINS")
    if env:
        return [o.strip() for o in env.split(",")]
    return ["http://localhost:5173"]


async def _handler(ws: ServerConnection) -> None:
    origin = ws.request.headers.get("Origin", "") if ws.request is not None else ""
    if origin and origin not in _allowed_origins():
        await ws.close(1008, "Origin not allowed")
        return

    log.info("client connected", remote=ws.remote_address)
    try:
        async for raw in ws:
            msg = raw.decode() if isinstance(raw, bytes) else raw
            try:
                envelope = protocol.parse_envelope(msg)
            except protocol.InvalidEnvelope:
                continue
            if envelope["type"] == "ping":
                await ws.send(protocol.make_pong(envelope["id"]))
    finally:
        log.info("client disconnected", remote=ws.remote_address)


async def serve(host: str, port: int) -> None:
    """Start the WebSocket server and run until cancelled."""
    async with _ws_serve(_handler, host, port):
        log.info("agent listening", host=host, port=port)
        await asyncio.Future()  # run until cancelled
