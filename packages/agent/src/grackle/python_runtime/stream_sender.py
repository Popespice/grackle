"""Real-time trace streaming sender (Phase 7.2 — ADR-0016).

The ``TraceStreamSender`` runs a daemon thread that owns an asyncio event
loop and a websockets client.  The main thread (where ``sys.monitoring``
callbacks fire) enqueues trace events via :meth:`sink` — a call that
acquires a brief counter lock before enqueuing via
``queue.SimpleQueue.put_nowait`` (C-level, O(1)).
The daemon thread drains the queue and forwards events to the server.

Key design invariants (ADR-0016):
- **Hot path stays synchronous and non-blocking.**  ``sink()`` never calls
  ``await`` or raises a blocking exception — it acquires a lightweight
  counter lock (non-contended in the common case; ~50 ns on modern
  hardware), then enqueues or drops in O(1) time.
- **Drop-newest backpressure.**  When the ``_inflight`` counter exceeds
  ``max_inflight``, ``sink()`` increments a ``dropped`` counter and
  returns.  ``GRACKLE_STREAM_MAX_INFLIGHT`` (env var) overrides the
  default.
- **Sentinel-drain lifecycle.**  After the traced script finishes, the
  main thread enqueues ``_SENTINEL`` and joins the sender thread.  FIFO
  single-producer ordering means the sentinel cannot overtake earlier
  events, so the queue is fully drained before ``trace_session_end`` is
  sent (provided the connection stays open).  No tail loss on a live
  connection.
- **No pacing.**  Events are sent back-to-back as fast as the network
  allows.  Wall-clock *is* the pacing; this differs from the post-run
  replay path (``--connect`` without ``--stream``) which reproduces
  original inter-event timing.
- **Connection-lost handling.**  If the WebSocket closes mid-stream,
  ``connection_lost`` is set to ``True``, the queue is flushed so
  ``finish()`` can join without blocking, and a warning is logged.
  ``session_end`` delivery is best-effort; callers should check
  ``connection_lost`` after ``finish()`` returns.
"""

from __future__ import annotations

import contextlib
import os
import queue
import threading
import time
from typing import TYPE_CHECKING, Any

import structlog

from grackle import protocol

if TYPE_CHECKING:
    from grackle.adapters.base import TraceEvent

log = structlog.get_logger()

# Default inflight cap — approx. number of events queued but not yet sent.
_DEFAULT_MAX_INFLIGHT = 100_000
# Blocking timeout for queue.get() inside the sender thread.  Short enough
# that the sentinel is noticed promptly; long enough to avoid busy-spinning.
_POLL_S = 0.05

# Module-level sentinel; identity checked with ``is`` to avoid false matches.
_SENTINEL: object = object()


def _stream_max_inflight() -> int:
    """Return the inflight cap from ``GRACKLE_STREAM_MAX_INFLIGHT`` or the default."""
    raw = os.environ.get("GRACKLE_STREAM_MAX_INFLIGHT")
    if raw is not None:
        try:
            v = int(raw)
            return v if v >= 1 else _DEFAULT_MAX_INFLIGHT
        except (ValueError, TypeError):
            pass
    return _DEFAULT_MAX_INFLIGHT


class TraceStreamSender:
    """Stream trace events to a grackle server in real time.

    Typical lifecycle::

        sender = TraceStreamSender(url, session_id)
        sender.start()          # blocks until WebSocket connected + session_start sent
        tracer.run(script)      # hot path calls sender.sink(event) per frame
        sent = sender.finish()  # drains queue, sends session_end, joins thread

    Args:
        url:          WebSocket URL of the grackle server
                      (e.g. ``"ws://127.0.0.1:7878"``).
        session_id:   Unique identifier for this trace session.
        max_inflight: Drop-newest threshold.  When approximately this many
                      events are queued-but-unsent, new events are dropped.
                      Defaults to ``GRACKLE_STREAM_MAX_INFLIGHT`` env var
                      or ``_DEFAULT_MAX_INFLIGHT``.
    """

    def __init__(
        self,
        url: str,
        session_id: str,
        *,
        max_inflight: int | None = None,
    ) -> None:
        self._url = url
        self._session_id = session_id
        self._max_inflight = max_inflight if max_inflight is not None else _stream_max_inflight()

        # Counter lock — protects _inflight, _dropped, _sent.
        # _inflight is incremented by sink() (main thread) and decremented by
        # _drain_loop() (sender thread).  Without a lock the non-atomic RMW can
        # lose decrements and permanently wedge sink() at low caps.
        # The lock is non-contended in the common case and held for ~50 ns.
        self._counter_lock = threading.Lock()
        self._inflight: int = 0
        self._dropped: int = 0
        self._sent: int = 0

        # Set True when the WebSocket closes before all events are sent.
        self._connection_lost: bool = False

        self._queue: queue.SimpleQueue[object] = queue.SimpleQueue()
        # Set by the sender thread once the WebSocket is open and session_start
        # has been sent.  main thread blocks on this in start().
        self._connected = threading.Event()
        # Holds any connection-time exception so start() can surface it.
        self._connect_error: BaseException | None = None

        self._thread = threading.Thread(
            target=self._thread_main,
            name="grackle-stream-sender",
            daemon=True,
        )

    # ------------------------------------------------------------------
    # Hot-path API — called from sys.monitoring callbacks on main thread
    # ------------------------------------------------------------------

    def sink(self, event: TraceEvent) -> None:
        """Enqueue *event* for streaming.  Non-blocking; acquires a brief counter lock.

        If the inflight count exceeds ``max_inflight``, the event is silently
        dropped and the ``dropped`` counter is incremented.
        """
        with self._counter_lock:
            if self._inflight >= self._max_inflight:
                self._dropped += 1
                return
            self._inflight += 1
        self._queue.put_nowait(event)

    # ------------------------------------------------------------------
    # Lifecycle API — called from the main thread
    # ------------------------------------------------------------------

    def start(self, connect_timeout: float = 10.0) -> None:
        """Start the sender thread and block until the WebSocket is connected.

        Note: ``_connected`` is set after ``session_start`` is sent.  TCP
        write-buffering means the channel may appear open on a half-open
        connection; the first reliable confirmation comes from a subsequent
        receive.

        Args:
            connect_timeout: How long (seconds) to wait for a connection
                             before raising ``ConnectionError``.

        Raises:
            ConnectionError: if the connection cannot be established within
                             *connect_timeout* seconds.
        """
        self._thread.start()
        if not self._connected.wait(timeout=connect_timeout):
            raise ConnectionError(
                f"timed out waiting for WebSocket connection to {self._url!r} "
                f"after {connect_timeout:.1f} s"
            )
        if self._connect_error is not None:
            raise ConnectionError(
                f"failed to connect to {self._url!r}: {self._connect_error}"
            ) from self._connect_error

    def finish(self, timeout: float = 30.0) -> int:
        """Drain the queue, send ``trace_session_end``, and join the sender thread.

        Enqueues the ``_SENTINEL`` to signal end-of-stream.  Because the queue
        is FIFO and single-producer, the sentinel cannot pass any earlier
        events — the queue is fully drained before ``trace_session_end`` is
        sent (provided the connection stays open).

        If the connection was lost mid-stream the queue is still flushed so
        this method can join promptly; ``connection_lost`` will be ``True``
        and the final ``trace_session_end`` will not have been sent.

        Args:
            timeout: Maximum seconds to wait for the sender thread to finish.

        Returns:
            Number of events successfully sent to the server.
        """
        self._queue.put_nowait(_SENTINEL)
        self._thread.join(timeout=timeout)
        if self._thread.is_alive():
            log.warning(
                "stream sender thread did not finish within timeout",
                timeout=timeout,
                sent=self._sent,
                dropped=self._dropped,
            )
        if self._dropped:
            log.warning(
                "stream sender dropped events (backpressure)",
                dropped=self._dropped,
                sent=self._sent,
                max_inflight=self._max_inflight,
            )
        return self._sent

    @property
    def dropped(self) -> int:
        """Number of events dropped due to backpressure."""
        return self._dropped

    @property
    def connection_lost(self) -> bool:
        """``True`` if the WebSocket closed before all events were sent.

        Only meaningful after :meth:`finish` returns.
        """
        return self._connection_lost

    # ------------------------------------------------------------------
    # Sender-thread internals
    # ------------------------------------------------------------------

    def _thread_main(self) -> None:
        """Entry point for the daemon sender thread."""
        import asyncio

        try:
            asyncio.run(self._sender_main())
        except BaseException as exc:
            log.error("stream sender thread error", error=str(exc))
            # Unblock start() if it's still waiting.
            if not self._connected.is_set():
                self._connect_error = exc
                self._connected.set()

    async def _sender_main(self) -> None:
        """Open WebSocket, send ``session_start``, drain queue, send ``session_end``."""
        from websockets.asyncio.client import connect as _ws_connect

        try:
            async with _ws_connect(self._url) as ws:
                started_ns = time.monotonic_ns()
                await ws.send(
                    protocol.make_trace_session_start(self._session_id, started_ns, "live")
                )
                # Signal start() that we're ready.
                self._connected.set()
                await self._drain_loop(ws)
        except BaseException as exc:
            if not self._connected.is_set():
                self._connect_error = exc
                self._connected.set()
            raise

    async def _recv_drain(self, ws: Any) -> None:
        """Drain inbound server frames to prevent receive-buffer overflow.

        The sender never needs inbound data, but websockets accumulates server
        frames (pings, broadcast messages) in a receive queue.  On a busy
        multi-client server the buffer can grow without bound and trigger
        flow-control or a ping-timeout disconnect.  This coroutine runs
        concurrently with :meth:`_drain_loop` and discards every received frame.
        """
        from websockets.exceptions import ConnectionClosed

        try:
            async for _ in ws:
                pass  # discard inbound frames
        except ConnectionClosed:
            pass

    async def _drain_loop(self, ws: Any) -> None:
        """Drain the queue and forward events until ``_SENTINEL`` is received."""
        import asyncio

        from websockets.exceptions import ConnectionClosed

        loop = asyncio.get_running_loop()

        # Run recv-drain concurrently so the receive buffer never backs up.
        recv_task: asyncio.Task[None] = asyncio.ensure_future(self._recv_drain(ws))
        try:
            while True:
                # Block up to _POLL_S seconds waiting for an item.  run_in_executor
                # lets asyncio's event loop remain responsive during the wait.
                try:
                    item = await loop.run_in_executor(None, self._queue.get, True, _POLL_S)
                except queue.Empty:
                    continue

                if item is _SENTINEL:
                    break

                try:
                    await ws.send(protocol.make_trace_event(item))  # type: ignore[arg-type]
                    with self._counter_lock:
                        self._sent += 1
                        self._inflight -= 1
                except Exception as exc:
                    self._connection_lost = True
                    if isinstance(exc, ConnectionClosed):
                        log.warning(
                            "stream sender: WebSocket closed during drain",
                            sent=self._sent,
                        )
                    else:
                        log.warning(
                            "stream sender: error sending event",
                            error=str(exc),
                            sent=self._sent,
                        )
                    # Flush queue to sentinel so finish() can join without blocking.
                    await self._flush_to_sentinel(loop)
                    return

            # All events sent — transmit session_end.
            try:
                await ws.send(
                    protocol.make_trace_session_end(
                        self._session_id, time.monotonic_ns(), self._sent
                    )
                )
            except Exception as exc:
                self._connection_lost = True
                if isinstance(exc, ConnectionClosed):
                    log.warning(
                        "stream sender: WebSocket closed before session_end",
                        sent=self._sent,
                    )
                else:
                    log.warning(
                        "stream sender: error sending session_end",
                        error=str(exc),
                        sent=self._sent,
                    )
        finally:
            recv_task.cancel()
            with contextlib.suppress(asyncio.CancelledError, Exception):
                await recv_task

    async def _flush_to_sentinel(self, loop: Any) -> None:
        """Drain queue items until ``_SENTINEL`` is consumed.

        Called when the WebSocket closes mid-drain so that ``finish()`` can
        join the sender thread without blocking on the queue.
        """
        while True:
            try:
                item = await loop.run_in_executor(None, self._queue.get, True, _POLL_S)
            except queue.Empty:
                continue
            if item is _SENTINEL:
                return
