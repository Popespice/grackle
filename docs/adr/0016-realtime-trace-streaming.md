# ADR-0016: Real-time trace streaming via daemon sender thread

**Status:** Accepted  
**Date:** 2026-05-25  
**Supersedes:** ADR-0013 §2 ("no value in a queue for hot-path delivery")

---

## Context

Phase 6 shipped a working runtime overlay: `sys.monitoring` traces a script,
stores all events in a list, and — after the script finishes — streams them
to the browser via `--connect`.  The result is a *completed-trace replay*, not
a live view.

ADR-0013 §2 explicitly deferred real-time streaming:

> "The tracer could put events on a `queue.Queue` for a sender thread.
> We see no value in that today given the completed-trace use case…"

Phase 7 makes real-time the centerpiece.  The user runs:

```
grackle trace SCRIPT --connect ws://127.0.0.1:7878 --stream
```

and events appear in the browser *as the script executes*, not after.

---

## Decision

### Hot path: unchanged — no I/O, no `await`, no lock

ADR-0013's core constraint stands: `sys.monitoring` callbacks must remain
synchronous and non-blocking.  The only change to the hot path is the
introduction of an optional `sink: Callable[[TraceEvent], None]` on `Tracer`.

The default sink is `self._events.append` — **all existing behaviour preserved**.
The real-time sink calls `queue.SimpleQueue.put_nowait`, a C-level O(1)
operation that takes the GIL for one reference increment.  No syscall; no
user-space lock; no `await`.

### Sender thread: owns all I/O

A `TraceStreamSender` daemon thread runs `asyncio.run(_sender_main())`.
It:
1. Opens the WebSocket and sends `trace_session_start`.
2. Signals a `threading.Event` so `start()` on the main thread unblocks.
3. Drains the queue via
   `await loop.run_in_executor(None, queue.get, True, _POLL_S)`.
4. Forwards each event with `await ws.send(make_trace_event(ev))`.
5. On `_SENTINEL`, sends `trace_session_end` and exits.

The main thread never touches the WebSocket; the sender thread never touches
`sys.monitoring`.

### Why `SimpleQueue` over `Queue`

`Queue.put_nowait` acquires a `threading.Lock` + a `threading.Condition`.
`SimpleQueue.put_nowait` is a C-level linked-list append with one GIL
acquisition — no user-space synchronisation objects.  For a hot path that
fires on every Python function call/return, the difference matters.

Bounded mode (`Queue(maxsize=N)`) would block or raise on the producer
when full — both are forbidden on the hot path.  Backpressure is instead
enforced by the **drop-newest** mechanism (see below).

### Backpressure: drop-newest with an approximate inflight counter

The queue is unbounded (no blocking on overflow).  To prevent unbounded
memory growth under a fast producer + slow network, `sink()` checks an
approximate `_inflight` counter before enqueuing:

```python
if self._inflight >= self._max_inflight:
    self._dropped += 1
    return
self._inflight += 1
self._queue.put_nowait(event)
```

`_inflight` is decremented after each `ws.send()` in the drain loop.
There is **no lock** on the counter: in CPython, `int += 1` and `int -= 1`
are each individually atomic under the GIL.  The counter may transiently
over- or under-count by one between the increment and the enqueue, but
this only affects the drop threshold — it never causes data corruption.
The invariant we need is "drop before the queue grows arbitrarily large",
which the approximate check satisfies.

`GRACKLE_STREAM_MAX_INFLIGHT` (default 100 000) configures the threshold.

### Sentinel-drain lifecycle — no tail loss

```
main thread:  tracer.run(script) → sink(ev₁) … sink(evₙ) → finish()
                                                              └─ queue.put(_SENTINEL)
                                                              └─ thread.join()
sender thread:                   drain_loop … sees _SENTINEL → session_end → exit
```

Because the queue is FIFO and single-producer, `_SENTINEL` arrives *after*
all events.  `finish()` joins the thread, so `session_end` is guaranteed to
be sent — and sent only after the full event stream — before `finish()`
returns.

### No pacing

Real-time mode sends events back-to-back.  Wall-clock *is* the pacing.
This is the opposite of the completed-trace path (`--connect` without
`--stream`) which reproduces original inter-event timing with a `_MAX_GAP_S`
cap.  `--no-pace` is accepted but ignored when `--stream` is active.

### Incompatibility: `--stream` + `--output`

Combining live streaming with local file output requires a "tee" sink.
That is straightforward to add but deferred to Phase 8 to keep this chunk
small.  `grackle trace` raises `UsageError` if both are supplied.

### Server: unchanged

`_receive_loop` in `server.py` already handles inbound `trace_session_start`,
`trace_event`, and `trace_session_end` messages from any producer — it
broadcasts them and adds them to the ring buffer.  No server change is
needed for Phase 7.2.

---

## Consequences

**Positive:**
- Live mid-execution visualisation: Timeline and heat-map update as the
  script runs.
- Hot path overhead addition is one C-level `put_nowait` per event — within
  the ≤10 % overhead budget (ADR-0013 §3).
- Existing completed-trace path (`--connect` without `--stream`) is fully
  backward compatible.
- `--output` still works independently of streaming.

**Negative / trade-offs:**
- Drop-newest backpressure means real-time streams are not lossless under
  extreme load.  Lossless options remain: `--output` (file), `--connect`
  without `--stream` (post-run replay).
- `_inflight` is approximate; over/under by at most 1 is documented.
- `--stream + --output` tee is deferred (Phase 8).

---

## Alternatives considered

| Alternative | Reason rejected |
|---|---|
| `queue.Queue(maxsize=N)` | `put_nowait` raises `Full` on overflow — blocking the hot path is forbidden |
| `asyncio.Queue` from the main thread | `put_nowait` on a running loop from another thread is not safe without `call_soon_threadsafe` |
| `collections.deque` | No blocking `get()` — the sender would need to busy-spin or use a `Condition` |
| Thread-safe ring buffer (drop-oldest) | Drop-newest is simpler; for traces, the newest events are usually more valuable than the oldest |
| HTTP endpoint for events | Contradicts ADR-0002 (single WebSocket channel); adds latency and a separate port |
