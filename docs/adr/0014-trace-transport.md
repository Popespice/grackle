# ADR-0014 — Trace transport: WebSocket file replay + live-attach

**Status:** accepted

**Context:**

Phase 6.1 shipped a `sys.monitoring`-based tracer that runs a Python script and collects `TraceEvent` dicts into a JSONL file. The events live on disk; nothing shows them yet. Phase 6.2 makes the "live" half of the tagline real by delivering trace events to the browser over the existing 127.0.0.1 WebSocket.

Two delivery modes are needed:

1. **File replay** — `grackle serve --trace-source trace.jsonl` replays a pre-recorded trace to every new browser connection. Each connection sees the full file from the beginning. Deterministic, unit-testable, no shared mutable state.
2. **Live-attach** — `grackle trace SCRIPT --connect ws://…` runs the tracer in a separate process, then streams the _completed_ event list into a running server, which fans it out to all connected browsers. A ring-buffer lets browsers that join mid-stream see recent history.

**Decision:**

### Protocol messages

Three new WebSocket envelope types (added to `messages.schema.json` and `messages.ts`):

| type | direction | purpose |
|---|---|---|
| `trace_session_start` | server → browser | start of a trace stream; carries `session_id`, `started_ns`, `source` (`"replay"` or `"live"`) |
| `trace_event` | server → browser | single `TraceEvent` payload |
| `trace_session_end` | server → browser | end of stream; carries `session_id`, `ended_ns`, `event_count` |

The `type` strings are open strings per ADR-0004. `KNOWN_MESSAGE_TYPES` (6 → 9) is exported from `messages.ts` for IDE autocomplete; it is not a gatekeeper.

### Race ordering

`static_graph` is guaranteed to arrive before `trace_session_start` because the connection handler `await`s `_push_static_graph()` sequentially before creating the replay task. The browser can therefore always relate node IDs in trace events to nodes in the already-received graph.

### File replay per-connection

`_replay_trace(ws, trace_source, pace, session_id)` runs as an `asyncio.Task` per connection. It reads the trace file via `read_jsonl()`, emits `session_start`, streams events (paced or instant), emits `session_end`. Each new connection gets its own replay from byte 0. Load or parse failure logs a warning and emits an empty session (`event_count=0`); the server stays up.

**Pacing:** inter-event gaps are reproduced using `time.monotonic_ns()` deltas, clamped to `_MAX_GAP_S = 0.25s` so long idle stretches don't stall the replay. `--no-pace` (CLI) / `pace=False` (Python API) skips all sleeping — essential for CI and tests.

### Live-attach producer/consumer model

"Live" in Phase 6.2 means a _completed-trace stream_ from a separate process, not real-time event streaming during execution. The 6.1 tracer is synchronous: it runs the script, collects all events, then returns. True real-time streaming would require async/threading in the `sys.monitoring` callback hot path, which ADR-0013 explicitly forbids.

The `--connect` client opens a WebSocket to the running server and pushes `session_start → trace_event* → session_end`. The server's `_receive_loop` recognises these inbound message types, appends each raw JSON string to the ring-buffer, and calls `_broadcast()` to all other connections.

### Connection registry + ring-buffer

A closure-scoped `set[ServerConnection]` tracks every live connection. `_broadcast(raw, connections, exclude=producer_ws)` iterates the set, swallowing per-connection `ConnectionClosed` so one dead client cannot interrupt the fan-out.

A `collections.deque[tuple[int, str]]` stores `(received_ns, raw_msg)` tuples. The buffer window defaults to 60 seconds and is configurable via `GRACKLE_TRACE_BUFFER_SECONDS`. `_trim_ring_buffer()` runs before every append to drop stale entries. Late-joining consumers receive the full ring-buffer before entering the normal message stream. The ring-buffer is populated **only** by live ingest, not by file replay (each replay connection re-reads the file from the start).

### Task lifecycle + clean teardown

Per connection the handler creates tasks for `_receive_loop` and optionally `_replay_trace`, gathers them, and on exit (`finally`) cancels all tasks and re-gathers with `return_exceptions=True` to reap without leaked tasks. `ConnectionClosed` from `asyncio.gather` is caught and treated as normal termination.

### Stale Phase-1 TraceEvent removed

`adapters.schema.json` carried a Phase-1 skeleton `TraceEvent` (id/timestamp/type/payload) that was always annotated "full event schema defined in phase 6". With the real `TraceEvent` now in `trace.schema.json` and `messages.ts`, the placeholder was removed to eliminate the duplicate export. The authoritative Python `TraceEvent` TypedDict remains in `grackle/adapters/base.py`.

**Consequences:**

- **Positive:** Browsers connected to `grackle serve --trace-source` see a reproducible event stream immediately on connect. `--connect` enables live-attach without changing the tracer hot path.
- **Positive:** No shared mutable state in file-replay mode — each connection is fully independent.
- **Positive:** Ring-buffer late-joiner support is automatic; late browsers see recent context.
- **Limitation:** Live mode streams a _completed_ trace, not events in real time during execution. Real-time streaming (tracer → async queue → WebSocket) is a follow-up and requires relaxing the ADR-0013 "no async in hot path" constraint.
- **Limitation:** The ring-buffer is unbounded in event count within the time window. Very high-frequency traces could use significant memory. A future `GRACKLE_TRACE_BUFFER_MAX_EVENTS` option would bound this.
- **Follow-up:** Phase 6.3 renders these events in a Timeline panel and a heat-map overlay. The `traceEvents` array accumulated in `useGraphStore` is append-only for now; 6.3 will add batched append for render performance.

**Cross-references:** ADR-0002 (WebSocket transport choice), ADR-0013 (tracer hot-path constraint).
