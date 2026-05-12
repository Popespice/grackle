# 0002 — Trace transport: WebSocket vs alternatives

**Status**: accepted

## Context

The grackle agent runs a server process on the user's machine and needs to push
a continuous stream of trace events (function calls, returns, exceptions) to a
browser UI in real time. We need a transport that is:

- Low-latency (events arrive within ~50 ms of emission)
- Bidirectional (browser can send control messages: pause, resume, seek)
- Simple to implement in both Python and the browser
- Local-only safe (no accidental exposure to the network)

## Decision

Use WebSockets (Python `websockets` 16.x asyncio API, browser `WebSocket` API).

- Bind to `127.0.0.1` only — never `0.0.0.0`. The server is not reachable from
  other machines on the network.
- Default origin allowlist: `["http://localhost:5173"]`. Configurable via
  `GRACKLE_ALLOWED_ORIGINS`.
- Message framing: JSON envelopes `{ id, type, payload }` — human-readable,
  trivially inspectable with browser DevTools.

**Why not Server-Sent Events (SSE)**: SSE is server-push only. When we add
replay scrubbing (phase 8), the browser needs to send a seek position to the
agent. SSE cannot carry that signal without a separate HTTP channel.

**Why not HTTP polling**: latency is hostile to live visualization. A 500 ms
poll interval means the graph lags a half-second behind execution — disorienting
for anything faster than a tight loop.

**Why not gRPC-Web**: gRPC's binary framing and code-generation pipeline add
toolchain complexity that gives us nothing at local single-user scale. The
schema and type-safety we need are already handled by the JSON Schema → codegen
pipeline (ADR-0001).

## Consequences

- A WebSocket connection per browser tab. In practice this is always one tab.
- The `websockets` 16.x asyncio API is the current stable path; the legacy API
  is deprecated and will be removed by 2030.
- Phase 3 adds a trace firehose on top of this same transport. The envelope
  schema (`WsEnvelope`) must remain backward-compatible — unknown `type` values
  are ignored, not errored.
- Origin allowlist must be updated when the frontend port changes (e.g., a
  custom Vite port). Documented in README.
