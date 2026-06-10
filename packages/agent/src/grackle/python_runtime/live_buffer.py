"""Live-attach ring buffer: late-joiner history + fan-out for streaming ingest.

In live-attach mode (no ``--trace-source``) a producer process streams
``trace_session_start`` / ``trace_event`` / ``trace_session_end`` messages into
the server.  This module owns the bounded ring buffer that retains recent
messages for late-joining consumers, the age+count eviction policy, the
buffer-flush to a newly-joined consumer, and the fan-out broadcast.  The server
WS dispatch loop calls these; it does not own the eviction logic.
"""

from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING

import websockets.exceptions

if TYPE_CHECKING:
    import collections

    from websockets.asyncio.server import ServerConnection

# Default ring-buffer retention window (seconds).  60 s comfortably covers a
# human reconnecting a browser tab mid-session (refresh / network blip) so a
# late joiner still receives recent history; older events are assumed already
# rendered or no longer interesting.  Overridable via GRACKLE_TRACE_BUFFER_SECONDS.
_DEFAULT_BUFFER_SECONDS = 60.0


def trace_buffer_seconds() -> float:
    """Return the ring-buffer retention window from env or default."""
    raw = os.environ.get("GRACKLE_TRACE_BUFFER_SECONDS")
    if raw is not None:
        try:
            return float(raw)
        except (ValueError, TypeError):
            pass
    return _DEFAULT_BUFFER_SECONDS


def trace_buffer_max_events() -> int | None:
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


def trim_ring_buffer(
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


async def flush_ring_buffer(
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


async def broadcast(
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
