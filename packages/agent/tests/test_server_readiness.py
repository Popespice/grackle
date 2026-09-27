"""serve() readiness: callers must be able to wait for the listening socket.

Test campaign T5-2 (docs/test-campaigns/phase-12.md). Every server test used
to start the server with ``create_task(serve(...))`` and then
``await asyncio.sleep(0.05)``, hoping the socket was listening by then.
Nothing awaitable exists between task creation and the bind, and a
store-backed server does strictly more pre-listen work (recordings mkdir,
the orphan sweep, a ``detect_language`` filesystem walk) — so a slow CI
runner occasionally lost the race and the first ``connect`` failed with
``[WinError 1225]`` (``ERROR_CONNECTION_REFUSED``: nothing was listening).
That was ``test_two_sessions_back_to_back``'s Windows flake. A second window
sat alongside it: the ``free_port`` fixture closes its probe socket before
``serve()`` rebinds the number, so another process can take the port in
between.

Server tests now start servers through the ``start_server`` fixture
(``conftest.py``), which waits on ``serve(ready=...)`` and binds port 0.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from typing import TYPE_CHECKING, Any

from websockets.asyncio.client import connect
from websockets.asyncio.server import serve as real_ws_serve

from grackle import server as server_mod

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

    import pytest


async def test_serve_signals_readiness_even_when_pre_bind_work_is_slow(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """Pre-bind work that outlasts any fixed sleep a caller could pick must
    not matter: serve() reports when — and on which port — it is actually
    listening, and a client connecting on that report always gets through.
    Port 0 lets the OS pick the port at bind time, closing free_port's
    probe-then-rebind window as well."""

    @contextlib.asynccontextmanager
    async def _slow_ws_serve(*args: Any, **kwargs: Any) -> AsyncIterator[Any]:
        await asyncio.sleep(0.3)  # six times the 0.05s every test used to sleep
        async with real_ws_serve(*args, **kwargs) as ws_server:
            yield ws_server

    monkeypatch.setattr(server_mod, "_ws_serve", _slow_ws_serve)

    ready: asyncio.Future[int] = asyncio.get_running_loop().create_future()
    task = asyncio.create_task(server_mod.serve("127.0.0.1", 0, root=tmp_path, ready=ready))
    try:
        either: set[asyncio.Future[Any]] = {ready, task}
        await asyncio.wait(either, timeout=10.0, return_when=asyncio.FIRST_COMPLETED)
        if task.done():
            task.result()  # serve() died before listening: surface its error
        port = ready.result()
        async with connect(f"ws://127.0.0.1:{port}") as ws:
            await ws.send(json.dumps({"id": "r1", "type": "ping", "payload": {}}))
            reply = json.loads(await asyncio.wait_for(ws.recv(), timeout=5.0))
        assert reply["type"] == "pong"
    finally:
        task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await task
