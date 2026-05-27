# ADR-0017 — Server-side trace seek (Phase 7.3)

**Status:** accepted  
**Date:** 2026-05-27  
**Deciders:** Connor Allen

---

## Context

Phase 6 buffered the entire trace in the browser (`traceEvents: TraceEvent[]`).
For short scripts this is fine, but a 30-minute profiling session at 10 k
events/s yields ~18 M events — well past what the browser can hold without
stalling.  The root cause is that the scrubber (`TimelinePanel`) sizes itself
from `traceEvents.length`, forcing the browser to accumulate everything before
the full trace duration is knowable.

Phase 7.3 fixes this for the stored-file case (`grackle serve --trace-source`):
the server pre-indexes the JSONL file once at startup, and the browser can
request arbitrary windows via a new request/response message pair
(`trace_seek_request` / `trace_window`), mirroring the existing
`read_source` / `source_response` pattern (ADR-0002).

---

## Decision

### 1. Byte-offset index on the agent

`python_runtime/jsonl_index.py` implements `JsonlIndex`:

- **One-pass scan** on construction: record the byte offset (`int`) of every
  non-blank line.  Memory overhead: 8 bytes/event (~80 MiB at 10 M events).
- `__len__()` returns the total event count.
- `read_window(start, count)` seeks directly to `self._offsets[start]` and
  reads `count` lines — O(1) seek, O(count) read.  Indices are clamped
  (negative → 0, past end → partial), never raising.
- A sparse index (offset every K lines) is deferred to Phase 8 if the 80 MiB
  upper bound proves unacceptable in practice.

### 2. Stable file_session_id across connections

Previously, `_replay_trace` generated a fresh `uuid4()` per browser connection.
With seeks, the browser sends `trace_seek_request.session_id` to identify which
session to serve.  A per-connection UUID would require the browser to
re-discover the session_id every reconnect.

**Fix:** `serve()` generates one `file_session_id = str(uuid4())` at startup
(when `trace_source` is set) and passes it to every call of `_replay_trace`.
All connections from the same server process share the same session_id.

### 3. New message types (open strings, ADR-0004 compliant)

Three new types in `messages.schema.json`:

| type | direction | purpose |
|---|---|---|
| `trace_seek_request` | browser → agent | request a window by (session_id, start_index, count) |
| `trace_window` | agent → browser | the window + total, echoing request id |
| `trace_seek_error` | agent → browser | error for unknown/non-seekable session |

`trace_session_start.payload` gains an optional `seekable: boolean` field.
When `seekable: true`, the browser knows it may send `trace_seek_request`.

### 4. Transport: WebSocket request/response (not HTTP)

ADR-0002 chose WebSocket for all communication "so seek signals need no
separate HTTP channel."  This pattern is already proven by `read_source` /
`source_response`.  `requestTraceWindow` in `client.ts` follows the exact same
pending-map + timeout pattern as `sendReadSource`:

- `_pendingTraceWindow: Map<string, resolver>` keyed by envelope `id`.
- 5 s timeout; rejects with a `"trace_seek_request timeout"` error.
- `trace_seek_error` rejects the promise with the `reason` string.

### 5. Feature detection via seekable flag

`startTraceSession(sessionId, seekable?)` now accepts a `seekable` parameter
(default `false`).  `App.tsx` reads `msg.payload.seekable === true` from
`trace_session_start`.

When `traceSeekable` is `true` in the store:
- `TimelinePanel` sizes the scrubber off `traceTotal` (from the first window
  response) rather than `traceEvents.length`.
- Scrubber moves trigger a debounced (150 ms) `requestTraceWindow` call that
  loads a window of `traceWindowSize` events centred on the new position.
- `setTraceWindow(start, events, total)` replaces `traceEvents` with the window.

Non-seekable sessions (live / real-time streaming) keep the buffered path
unchanged — the scrubber grows as events arrive.

### 6. Documented limitations (MVP cut-line)

- **Replay still streams all events** — the browser still receives the full
  stream via `_replay_trace`; seekability is an orthogonal "jump ahead"
  channel, not a replacement for streaming.
- **Cumulative heat and coverage** remain on the fully-buffered path; they
  require prefix aggregates (Phase 8).
- **Seek is only advertised for file-replay sessions.**  Live-attach and
  real-time streaming sessions never set `seekable: true`; seek requests for
  their session IDs return `trace_seek_error`.

---

## Consequences

**Positive:**
- Very long stored traces no longer require the browser to buffer everything
  before the scrubber reaches the end.
- Pattern is symmetric with `read_source` — no new transport machinery.
- `JsonlIndex` is a pure data structure; testable in isolation.

**Negative / trade-offs:**
- `JsonlIndex` memory is proportional to total events (~80 MiB at 10 M events).
- `_replay_trace` still calls `read_jsonl` for the stream, so the file is
  scanned twice at startup (once for the index, once for streaming).
  Acceptable for Phase 7.3; Phase 8 can use the index for streaming too.
- Scrubber shows the total only after the first seek response arrives; there is
  a brief moment where `traceTotal` is 0 and the scrubber is invisible.
