# Phase 7 Summary — Runtime scale + real-time streaming

**Tag:** `v0.7.0-phase-7`
**Shipped:** 2026-05-27

## What shipped

### 7.1 — Batched trace ingest + count-bounded ring buffer

- **`addTraceEvents(batch: TraceEvent[])`** added to `useGraphStore`; single `concat` per batch eliminates O(n²) spread accumulation.
- **`useBufferedTraceEvents`** hook: coalesces `trace_event` WebSocket messages into a `useRef` pending array and flushes once per `requestAnimationFrame`. Force-flush on `trace_session_end` so no tail is dropped. jsdom guard (`typeof requestAnimationFrame !== "undefined"`) for test environments.
- **`App.tsx`** wired to `useBufferedTraceEvents`; per-event callback replaced by batched rAF path.
- **`GRACKLE_TRACE_BUFFER_MAX_EVENTS`** env var: `_trim_ring_buffer` now evicts oldest events beyond the count cap (in addition to the existing time window). Treat values `<1` as no cap.
- New tests: `useBufferedTraceEvents.test.ts` (N pushes → one `addTraceEvents` per rAF; session_end flush); `useGraphStore.test.ts` (`addTraceEvents` batch concat + order); agent `_trim_ring_buffer` count-cap unit tests.

### 7.2 — Real-time trace streaming (ADR-0016)

- **`python_runtime/stream_sender.py`**: `TraceStreamSender` — daemon thread owns an asyncio loop + `websockets` client. `sink(event)` is the hot-path callable: O(1) `put_nowait` into a `SimpleQueue`; drop-newest backpressure via approximate `_inflight` counter (`GRACKLE_STREAM_MAX_INFLIGHT`, default 100 000). `start()` connects and sends `trace_session_start`; connect failure surfaces via a `threading.Event` so the CLI fails fast. `finish(timeout)` enqueues `_SENTINEL`, joins the thread, returns sent count.
- **`python_runtime/tracer.py`** refactored: `Tracer.__init__(..., *, sink: Callable[[TraceEvent], None] | None = None)`. Default sink appends to internal list; real-time sink is `TraceStreamSender.sink`. `_emit` checks count cap then calls `_sink`. `run()` wraps `BaseException` so `SystemExit`/`KeyboardInterrupt` still flush the queue.
- **`python_runtime/adapter.py`**: `trace_streaming(script, root, options, sink)` for real-time callers; original `trace()` unchanged.
- **`cli.py`**: `--stream` flag on `grackle trace`. `--connect URL --stream` → real-time path; `--connect URL` alone → unchanged completed-trace path (backward compatible). `--stream + --output` is a `UsageError`. `--no-pace` is a no-op under `--stream` (wall-clock *is* the pacing).
- **Server**: no change — `_receive_loop` already recognises inbound `trace_*`, broadcasts, and ring-buffers.
- **ADR-0016**: documents hot-path non-blocking guarantee (one C-level enqueue), `SimpleQueue` choice over `queue.Queue`/`deque`, drop-newest backpressure semantics, sentinel-drain lifecycle (FIFO guarantee of no tail loss), no-pacing rationale. Supersedes ADR-0013 §2's "no value in a queue" justification.
- New tests: `test_stream_sender.py` (ordered delivery, no tail loss when script faster than drain, backpressure drop-newest, no pacing, session_end on exception, connect failure, no websockets internals in node_ids); `test_cli_trace.py` `--stream` e2e; `test_tracer.py` custom-sink + cap.

### 7.3 — Server-side trace seek (ADR-0017)

- **`python_runtime/jsonl_index.py`**: `JsonlIndex.build(path)` — one-pass byte-offset scan (`list[int]`). `__len__` = total event count. `read_window(start, count) -> list[TraceEvent]` — O(1) seek to offset, O(count) read; clamps both bounds; skips blank/malformed lines. Uses binary `split(b"\n")` to match `read_jsonl`'s `str.split("\n")` semantics (avoids U+0085/U+2028/U+2029 divergence).
- **Schema** (`messages.schema.json`): three new message types — `trace_seek_request {session_id, start_index, count}`, `trace_window {session_id, start_index, events, total}`, `trace_seek_error {session_id, reason}`; optional `seekable: boolean` on `trace_session_start.payload`. Codegen regenerated (`pnpm codegen`); `messages.ts` hand-updated with interfaces + `KNOWN_MESSAGE_TYPES` 9→12.
- **`protocol.py`**: `make_trace_window`, `make_trace_seek_error`, `seekable` kwarg on `make_trace_session_start`.
- **`server.py`**: stable `file_session_id` per process (not per connection) so reconnects can seek with the same id. One `JsonlIndex` built at startup. `_replay_trace` in **window-only seekable mode** — sends `session_start(seekable=True)` + `session_end(event_count=N)` **without streaming individual events**; the browser fetches windows on demand. Seek handler in `_receive_loop` wrapped in `try/except`; count capped at `_MAX_SEEK_COUNT = 1000`; `start_index` clamped and echoed back as clamped value; `read_window` offloaded via `run_in_executor` (no blocking I/O on the event loop).
- **`ws/client.ts`**: `requestTraceWindow(sessionId, start, count): Promise<TraceWindowMessage>` — mirrors `sendReadSource` (pending-map keyed by envelope `id`, 5 s timeout). `_pendingTraceWindow` cleared on `trace_session_start` so stale promises from a previous session can't resolve against a new session.
- **`useGraphStore.ts`**: `traceSeekable`, `traceTotal`, `traceWindowStart` fields; `setTraceWindow(startIndex, events, total)` action. `setPlayhead` clamps to `traceTotal` (not window length) in seekable mode. `play` uses `traceTotal` as "at end" bound. `setTraceSeekable(boolean)` exposes the fallback path.
- **`heatmap.ts`**: `computeHeat(..., windowStart = 0)` — translates absolute `tracePlayhead` to window-relative position before slicing the event array.
- **`useHeatmap.ts`**: passes `traceWindowStart` as 6th arg to `computeHeat`.
- **`useTracePlayback.ts`**: uses `traceTotal` as the stop bound in seekable mode.
- **`TimelinePanel.tsx`**: scrubber sized off `traceTotal`; auto-fetches initial window on session start; 150 ms debounced `requestTraceWindow` on scrub. `traceWindowSize` captured via `useRef` and removed from initial-fetch `useEffect` deps (prevents re-seek on every slider change). Initial-seek failure calls `setTraceSeekable(false)` to unfreeze the scrubber.
- **`StatsPanel.tsx`**: shows `traceTotal` (not `traceEvents.length`) in seekable mode.
- **ADR-0017**: documents WS request/response transport choice (vs HTTP; see ADR-0002), stable session_id, window-only mode rationale, `_MAX_SEEK_COUNT` cap, MVP scope (scrubber + sliding heat; cumulative/coverage remain on buffered path).
- New tests: `test_jsonl_index.py` (17 cases — build/len/read_window/clamping/blank-lines/data-integrity); `test_server_trace_seek.py` (11 cases — seekable flag, stable id, seek/window/error, clamped echo, count cap, no events streamed in seekable mode); `client.test.ts` (3 new + 1 pending-map clear case); `useGraphStore.test.ts` (8 new seekable cases); `heatmap.test.ts` (3 new windowStart cases).

---

## Code-review fixes (post-7.3, pre-ship)

A maximum-effort review of PR #29 surfaced 14 findings, all fixed before merge:

| # | Issue | Fix |
|---|---|---|
| 1 | Double-writer race: replay streamed events AND `setTraceWindow` wrote `traceEvents` | Window-only mode: `_replay_trace` skips event streaming when `seekable=True` |
| 2 | `traceWindowStart` had zero readers | `computeHeat` subtracts it; `useTracePlayback` reads it for bound |
| 3 | `setPlayhead` clamped to window length, not `traceTotal` | Clamp to `traceSeekable ? traceTotal : traceEvents.length` |
| 4 | `setTraceWindow` re-clamped playhead to window size, erasing absolute position | Clamp only to `total` |
| 5 | `computeHeat` used absolute playhead as window-relative index | Added `windowStart` param; `useHeatmap` passes `traceWindowStart` |
| 6 | `useTracePlayback` stopped at `traceEvents.length` (~200) not `traceTotal` | Use `traceTotal` as bound in seekable mode |
| 7 | Initial-seek error → `traceTotal=0`, scrubber frozen at 0 | Failure path calls `setTraceSeekable(false)` |
| 8 | `StatsPanel` showed window count instead of `traceTotal` | Conditional `traceSeekable ? traceTotal : traceEvents.length` |
| 9 | `traceWindowSize` in initial-fetch deps re-fired seek on every slider change | Removed from deps; use `traceWindowSizeRef` |
| 10 | `read_window` blocking I/O on event loop; unbounded `count` | `run_in_executor`; `_MAX_SEEK_COUNT = 1000` |
| 11 | Seek handler body outside try/except — exceptions propagated up | Wrapped in `try/except`, returns `trace_seek_error` on failure |
| 12 | `JsonlIndex.build` used binary `\n` split; `read_jsonl` used `splitlines()` — diverge on U+0085/U+2028/U+2029 | `read_jsonl` changed to `split("\n")` |
| 13 | `_pendingTraceWindow` not cleared on session restart — stale reply could resolve new session's promise | `_pendingTraceWindow.clear()` on `trace_session_start` in client |
| 14 | `make_trace_window` echoed raw unclamped `start_index` | Echo `clamped_start = max(0, min(start_raw, total))` |

---

## Acceptance grid — Phase 7

| # | Criterion | Status |
|---|---|---|
| 1 | **Batched ingest.** `addTraceEvents(batch)` exists; live ingest coalesces to one append per rAF; heat/coverage/playhead identical to per-event ingest. | **7.1 ✓** automated |
| 2 | **Count-bounded ring buffer.** `GRACKLE_TRACE_BUFFER_MAX_EVENTS=N` evicts oldest beyond N; a late joiner receives ≤N events. | **7.1 ✓** automated |
| 3 | **Real-time stream.** `grackle trace SCRIPT --connect ws://… --stream` emits `session_start → event* → session_end` while the script runs; a consumer sees events mid-execution. | **7.2 ✓** automated |
| 4 | **Hot path stays cheap.** Callback only enqueues (no I/O/await/lock); tracer overhead ≤10% on the 5 s workload with the real-time sink active. | **7.2 ✓** design + automated |
| 5 | **Exit correctness.** `session_end` always sent and queue fully drained (no tail loss) even on script exception / `sys.exit()` / KeyboardInterrupt. | **7.2 ✓** automated |
| 6 | **Backpressure.** With small `GRACKLE_STREAM_MAX_INFLIGHT` + a flooding script: memory bounded; `dropped + received == produced`; `session_end.event_count == received`. | **7.2 ✓** automated |
| 7 | **No artificial pacing.** Real-time events sent back-to-back; `--stream` rejects `--output`; `--no-pace` is a guarded no-op. | **7.2 ✓** automated |
| 8 | **Server seek.** `serve --trace-source` advertises `seekable:true`; `trace_seek_request{start,count}` → `trace_window` (correct slice + `total`, echoes clamped id); unknown session → `trace_seek_error`. | **7.3 ✓** automated |
| 9 | **Feature-detect.** Seekable sessions request windows (scrubber + sliding heat); non-seekable (live/real-time) keep the buffered path. Cumulative-heat/coverage limitation documented. | **7.3 ✓** automated + manual |
| 10 | **Schema parity.** The 3 new message types pass `check-parity`; all `type` values stay open strings (ADR-0004). | **7.3 ✓** automated |
| 11 | **Cross-OS.** Real-time streaming (thread + asyncio + websockets) works on Python 3.12+ macOS + Windows; capability-gated otherwise. | **7.2 / CI ✓** automated |
| 12 | **Ship.** ADR-0016 + 0017 accepted; `PHASE_7_SUMMARY.md`; `PROJECT_ACCEPTANCE.md` updated (17 ADRs, Phase 7 grid); `CLAUDE.md`; version 0.7.0; tag `v0.7.0-phase-7`. | **7.H ✓** |

---

## Known limitations

- **Real-time backpressure drops newest** under flood; lossless modes remain: file replay, `--output`, completed `--connect` (without `--stream`).
- **Server-seek MVP scope**: scrubber + sliding-window heat only; cumulative heat and runtime coverage remain on the fully-buffered path. Cumulative-over-windows requires server-side prefix aggregates → Phase 8.
- **`JsonlIndex` memory** ≈ 8 bytes/event offset (~80 MB at 10 M events); sparse index is a Phase 8 option.
- **`_inflight` counter** is an approximate cross-thread `int` (documented race; gates only a drop heuristic; correctness-safe).
- Runtime overlay still requires Python 3.12+ (tracer capability gate unchanged).

---

## Phase 8 candidates

- **Tee sink**: `--stream + --output` simultaneously — lossless file capture + live browser feed.
- **Server-side prefix aggregates**: cumulative heat and full-session coverage over seek windows without full buffering.
- **Polyglot static depth**: gRPC/protobuf cross-language edges; generics-aware Rust resolver (monomorphisation); re-export chasing (TS barrel files, Rust `pub use` chains).
- **Sparse `JsonlIndex`**: offset every K lines + intra-block scan; reduces memory from O(N) to O(N/K).
- **Agent-side cycle detection**: move Tarjan SCC off the frontend if profiling shows >100 ms on ≥10k-node graphs (ADR-0008 reserved this path).
