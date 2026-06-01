"""Minimal Chrome DevTools Protocol (CDP) client over the existing websockets dep.

ADR-0022: the Node/V8 runtime adapter drives Node over the V8 Inspector (CDP) on a
``127.0.0.1`` socket. CDP is JSON-RPC over a WebSocket:

- Command:   ``{"id": N, "method": "Domain.method", "params": {...}}``
- Response:  ``{"id": N, "result": {...}}``  or  ``{"id": N, "error": {...}}``
- Event:     ``{"method": "Domain.event", "params": {...}}``  (no ``id``)

This client sends commands and awaits their matching responses by ``id``, and
dispatches events to registered listeners. It adds **no new dependency** —
``websockets`` is already a runtime dependency for the trace transport (ADR-0014).

A background receive task is the single reader of the socket; :meth:`send` registers
a future keyed by command id and awaits it. The 1 MiB default inbound message cap is
disabled because ``Profiler.stop`` returns the full CPU profile in one frame.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable


class CDPError(RuntimeError):
    """A CDP command returned an ``{"error": ...}`` response, or the socket closed."""


@asynccontextmanager
async def connect(
    url: str,
    *,
    open_timeout: float = 10.0,
    default_timeout: float | None = 30.0,
) -> AsyncIterator[CDPClient]:
    """Open a CDP client to *url* (an inspector ``ws://127.0.0.1:.../<uuid>`` URL).

    Yields a started :class:`CDPClient`; closes the socket and the receive task on
    exit. ``max_size=None`` lifts the inbound cap so a large ``Profiler.stop``
    profile is not truncated. *default_timeout* bounds every command that does not
    pass its own ``timeout`` (including the attach-phase commands) so a half-open
    socket can never hang an ``await`` forever.
    """
    from websockets.asyncio.client import connect as ws_connect

    async with ws_connect(url, max_size=None, open_timeout=open_timeout) as ws:
        client = CDPClient(ws, default_timeout=default_timeout)
        client._start()
        try:
            yield client
        finally:
            await client._stop()


class CDPClient:
    """A CDP message channel over an open WebSocket.

    Constructed by :func:`connect`; not used directly. Use :meth:`send` for
    request/response commands and :meth:`on` to subscribe to events.
    """

    def __init__(self, ws: Any, *, default_timeout: float | None = None) -> None:
        self._ws = ws
        self._default_timeout = default_timeout
        self._next_id = 0
        self._pending: dict[int, asyncio.Future[dict[str, Any]]] = {}
        self._listeners: dict[str, list[Callable[[dict[str, Any]], None]]] = {}
        self._recv_task: asyncio.Task[None] | None = None

    # ------------------------------------------------------------------
    # Public API
    # ------------------------------------------------------------------

    async def send(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        *,
        timeout: float | None = None,
    ) -> dict[str, Any]:
        """Send a CDP command and await its result.

        Args:
            method: CDP method name (``"Domain.method"``).
            params: Command parameters.
            timeout: Optional per-command deadline overriding the client's
                ``default_timeout``. Bounding matters most for commands issued
                *after* user code is running (e.g. ``Profiler.stop``,
                ``takePreciseCoverage``): a synchronously-wedged V8 isolate never
                services the inspector, so without a bound the await would hang
                forever even though the socket stays open. When neither this nor
                ``default_timeout`` is set, the command waits indefinitely.

        Raises:
            CDPError: if the command returns an error, the socket closes before a
                response arrives, or the effective timeout elapses.
        """
        self._next_id += 1
        message_id = self._next_id
        loop = asyncio.get_running_loop()
        future: asyncio.Future[dict[str, Any]] = loop.create_future()
        self._pending[message_id] = future
        payload = {"id": message_id, "method": method, "params": params or {}}
        try:
            await self._ws.send(json.dumps(payload))
        except Exception as exc:  # send failed — don't leak the pending future
            self._pending.pop(message_id, None)
            raise CDPError(f"failed to send {method}: {exc}") from exc
        effective_timeout = timeout if timeout is not None else self._default_timeout
        if effective_timeout is None:
            return await future
        try:
            return await asyncio.wait_for(future, effective_timeout)
        except TimeoutError as exc:
            self._pending.pop(message_id, None)
            raise CDPError(f"{method} timed out after {effective_timeout:.0f}s") from exc

    def on(self, method: str, callback: Callable[[dict[str, Any]], None]) -> None:
        """Register *callback* for CDP event *method* (called with its ``params``).

        Multiple callbacks per method are allowed; a callback that raises is
        isolated so it cannot break the receive loop or other listeners.
        """
        self._listeners.setdefault(method, []).append(callback)

    # ------------------------------------------------------------------
    # Lifecycle (driven by connect())
    # ------------------------------------------------------------------

    def _start(self) -> None:
        self._recv_task = asyncio.ensure_future(self._recv_loop())

    async def _stop(self) -> None:
        if self._recv_task is not None:
            self._recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await self._recv_task
        self._fail_pending(CDPError("CDP client closed"))

    async def _recv_loop(self) -> None:
        from websockets.exceptions import ConnectionClosed

        try:
            async for raw in self._ws:
                self._dispatch(raw)
        except ConnectionClosed:
            pass
        finally:
            # Unblock any awaiters so the launcher never hangs on a dead socket.
            self._fail_pending(CDPError("CDP connection closed"))

    def _dispatch(self, raw: str | bytes) -> None:
        try:
            message: dict[str, Any] = json.loads(raw)
        except (ValueError, TypeError):
            return
        message_id = message.get("id")
        if message_id is not None:
            future = self._pending.pop(message_id, None)
            if future is None or future.done():
                return
            if "error" in message:
                future.set_exception(CDPError(str(message["error"])))
            else:
                future.set_result(message.get("result", {}))
            return
        method = message.get("method")
        if not method:
            return
        params = message.get("params", {})
        for callback in self._listeners.get(method, []):
            # A listener must not be able to break the reader for other commands.
            with contextlib.suppress(Exception):
                callback(params)

    def _fail_pending(self, error: CDPError) -> None:
        for future in self._pending.values():
            if not future.done():
                future.set_exception(error)
        self._pending.clear()
