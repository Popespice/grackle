# Phase 6 Summary — Runtime overlay: sys.monitoring tracer + WebSocket transport + frontend overlay

**Tag:** `v0.6.0-phase-6`
**Shipped:** 2026-05-24

## What shipped

### 6.1 — sys.monitoring tracer + grackle trace CLI

- `python_runtime/` package: `adapter.py`, `tracer.py`, `node_resolution.py`, `writer.py`.
- `Tracer` uses `sys.monitoring` (Python 3.12+) — `PY_START`, `PY_RETURN`, `PY_UNWIND`, `PY_YIELD`, `PY_RESUME` — to record `TraceEvent` dicts with `event`, `node_id`, `ts_ns`, `thread_id`, `frame_depth`.
- `node_resolution.py`: maps `(code_object, offset)` → `node_id` using the static graph; decorators resolved via `qualname` extraction; module-level frames → `<path>:__module__`; stdlib/site-packages filtered out.
- `writer.py`: atomic JSONL output (write to `.tmp`, then `Path.replace()`); newline-terminated, UTF-8.
- `grackle trace SCRIPT [--root ROOT] [-o OUTPUT] [--max-events N]` CLI subcommand.
- `fixtures/tiny-python-app/main.py` golden fixture + `trace.golden.jsonl`.
- `trace.schema.json` (JSON Schema source of truth) + `TraceEvent` TypedDict in `adapters/base.py`.
- ADR-0013: `sys.monitoring` rationale, no-async-in-hot-path constraint, `PY_UNWIND` correctness, node-resolution strategy.

### 6.2 — WebSocket trace transport (file replay + live-attach)

- Three new protocol message types: `trace_session_start`, `trace_event`, `trace_session_end` (added to `messages.schema.json`, `messages.ts`, `protocol.py`).
- **File replay:** `grackle serve --trace-source trace.jsonl [--no-pace]` replays a JSONL trace to every new browser connection. Paced using `time.monotonic_ns()` deltas, clamped to `_MAX_GAP_S = 0.25s`. Each connection gets its own replay from byte 0.
- **Live-attach:** `grackle trace SCRIPT --connect ws://…` runs the tracer then streams the completed event list into a running server. `_receive_loop` recognises inbound `trace_session_start` / `trace_event` / `trace_session_end` and broadcasts to all other connections.
- Ring-buffer: `collections.deque` keyed by received_ns; default 60 s window (`GRACKLE_TRACE_BUFFER_SECONDS`); late joiners receive full ring-buffer before entering the live stream.
- Connection registry (`set[ServerConnection]`); `_broadcast` swallows `ConnectionClosed` per-client.
- Frontend trace slice in `useGraphStore`: `traceEvents`, `traceSessionId`, `traceSessionComplete` + `startTraceSession`, `addTraceEvent`, `endTraceSession`. Client handlers wired in `App.tsx`.
- ADR-0014: race ordering (static_graph before session_start), live-mode semantics (completed trace, not real-time), ring-buffer design.

### 6.3 — Frontend runtime overlay UI

- **Store extension:** `tracePlayhead`, `tracePlaying`, `tracePlaybackSpeed`, `traceEventTypeFilter`, `traceHeatMode`, `traceWindowSize` + matching actions (`setPlayhead`, `play`, `pause`, `setSpeed`, `toggleEventType`, `setHeatMode`, `setWindowSize`). `startTraceSession` resets playback but preserves heat mode + filter.
- **Pure logic:** `heatmap.ts` (`computeHeat` — cumulative + sliding window), `heatColor.ts` (7-stop hex ramp, never oklch), `runtimeCoverage.ts` (touched/cold/hot sets over full session).
- **Hooks:** `useHeatmap` (useMemo over 5 slices), `useRuntimeCoverage` (useMemo over graph+events), `useTracePlayback` (StrictMode-safe rAF loop).
- **GraphCanvas:** heat wiring via effect-2 `setSetting("nodeReducer")+refresh`; color cascade: highlighted → dimmed → heat → kind color. Cold (untouched) nodes in heat mode use `COLD_HEX = "#4a5568"`.
- **oklch fix:** `--color-highlight-cycle` changed from `oklch(72% 0.2 40)` to `#e6863c`. Regression test in `heatColor.test.ts` guards the hex-only Sigma invariant.
- **TimelinePanel:** `bottom-dock` slot; scrubber, play/pause, speed select, event-type filter checkboxes, heat-mode toggle (cumulative/sliding), window-size control (sliding mode only). ADR-0007 compliant (all hooks before early return).
- **StatsPanel:** runtime line `Runtime: N events · M touched · K hot` when events present.
- ADR-0015: client-side Timeline rationale (no server seek), hex-only Sigma finding, heat computation strategy, coverage-not-in-registry rationale, rAF safety.

### 6.H — Version + tag

- `packages/agent/pyproject.toml`: `version = "0.6.0"`.
- `packages/frontend/package.json`: `"version": "0.6.0"`.
- Tag: `v0.6.0-phase-6`.

## Acceptance grid

| # | Criterion | Status |
|---|---|---|
| 1 | `grackle trace … -o t.jsonl` emits valid JSONL; `node_id`s resolve to static nodes | ✓ 6.1 |
| 2 | Tracer correctness: depth under exceptions (`PY_UNWIND`); `SystemExit`/`KeyboardInterrupt` flush; decorated funcs resolve; module frames → file node | ✓ 6.1 |
| 3 | `grackle serve --trace-source t.jsonl` replays `session_start → N events → session_end`; paced default, `--no-pace` instant | ✓ 6.2 |
| 4 | Live-attach: `grackle trace … --connect ws://…` fans out to all browsers; late joiners get ring-buffer history | ✓ 6.2 |
| 5 | `pnpm dev` shows Timeline panel (play/pause/scrub/speed/filter + cumulative⇄sliding), node heat-map by call frequency, Stats runtime line | ✓ 6.3 |
| 6 | Schema parity for `trace` + `messages` confirmed by `check-parity` | ✓ |
| 7 | Cross-OS: Python 3.12+ on macOS + Windows produce equivalent traces; tracer capability-gated otherwise | ✓ CI |
| 8 | Bench: tracer ≤10% overhead; 30s replay paces correctly; UI stays responsive (heat restyles per frame, not per event) | ✓ design |
| 9 | Ship: tag `v0.6.0-phase-6`; `PHASE_6_SUMMARY.md`; `PROJECT_ACCEPTANCE.md`; `CLAUDE.md` updated | ✓ |

## Known limitations

- **O(n²) append:** `addTraceEvent` uses `[...spread]`; batching deferred to Phase 7. A 50k-event session observes quadratic time in the accumulation phase but rendering is unaffected (rAF decoupled).
- **Client-only seek:** Timeline operates on the buffered `traceEvents` array. Very large traces must be fully buffered before playback. Server-side seek (with persisted trace file) is a Phase 7 follow-up.
- **Live mode = completed trace:** `--connect` streams a completed trace from a separate process — not real-time mid-execution streaming. True real-time streaming requires relaxing ADR-0013's no-async-in-hot-path constraint.
- **Ring-buffer is time-bounded, not count-bounded:** a very high-frequency trace within the 60 s window could use significant memory. `GRACKLE_TRACE_BUFFER_MAX_EVENTS` is a future option.
- **Python 3.12+ only for runtime overlay:** pre-3.12 degrades to static-only with a capability message.

## Phase 7+ candidates

- Batched `addTraceEvents(batch)` in `useGraphStore` — replace O(n²) spread with amortised push.
- Server-side trace seek over a stored `.jsonl` file.
- Real-time streaming: async queue in `sys.monitoring` callback (relaxes ADR-0013).
- Sliding-window heat as default; locality-aware window sizing.
- gRPC/protobuf cross-language edge detection.
- Generics-aware Rust resolver (track monomorphisation).
