# Project-wide Acceptance Criteria

> Last updated during the Phase 7.H close.
> Three grids: whole-product definition-of-done + Phase 6 + Phase 7 acceptance.
> Each item is marked **automated** (CI / per-chunk gate / bench) or **manual** (recorded in the phase `*_SUMMARY.md`).

---

## A. Whole-product definition of done — "grackle is what it says it is"

| # | Criterion | Verification |
|---|---|---|
| 1 | **Tagline test.** Fresh clone → `pnpm install` + `uv sync` + `pnpm codegen` → `pnpm dev` against `fixtures/tiny-python-app` with a trace loaded → the browser shows a **live** visualization (static graph **plus** runtime overlay: timeline scrubs, nodes heat-map by call frequency) updating **mid-execution** in `--stream` mode — not a static one. | manual |
| 2 | **End-to-end pipeline.** `parse → trace → serve → visualize` works on (a) `tiny-python-app`, (b) `tiny-polyglot` (Python side traced), (c) grackle's own repo. | manual |
| 3 | **Polyglot static.** Python, TypeScript, Go, Rust adapters each emit a graph; `parse_all` merges them; HTTP-route + subprocess cross-language edges resolve on `tiny-polyglot`. | automated (`pytest`, `check-parity`) |
| 4 | **Local-first invariant.** Server binds only to `127.0.0.1` (warns otherwise); zero network egress, no telemetry, no cloud dependency; works fully offline. | automated (`test_server.py` bind assertion) + manual |
| 5 | **Cross-platform.** Identical node IDs and graphs on macOS / Linux / Windows (POSIX path discipline); CI matrix green on all three. | automated (CI matrix) |
| 6 | **Runtime overlay.** `sys.monitoring` tracer (Python 3.12+) tags events to static node IDs; pre-3.12 degrades to static-only with a clear capability message — never a crash. | automated (`test_adapter.py`, capability test) |
| 7 | **Performance.** Tracer overhead ≤10% on a 5s workload (including with the real-time `--stream` sink active); UI stays interactive during a real-time stream and a 50k-event replay (batched rAF ingest avoids quadratic accumulation). | bench (manual timing) + automated |
| 8 | **Determinism.** `grackle parse` and `grackle trace` (with `PYTHONHASHSEED=0`) are reproducible; golden fixtures stable across runs. | automated (golden fixture tests) |
| 9 | **Quality gates.** `pytest` + `mypy --strict` + `tsc` + `biome` + frontend tests + `check-parity` all green on the CI matrix; no skipped or disabled guards. | automated (CI + pre-push hooks) |
| 10 | **Documented architecture.** Every cross-cutting decision has an accepted ADR (17 total); each phase has a `*_SUMMARY.md` card; `CLAUDE.md` current. | manual |
| 11 | **Stable contracts.** JSON Schema is the single source of truth; generated TS/Py match (`check-parity`); message `type`, node/edge `kind`, trace `event` are open strings — unknown values ignored, never errors (ADR-0004). | automated (`check-parity`) |
| 12 | **Robustness.** Malformed input (bad source, missing/garbled trace, non-3.12 interpreter, script outside `--root`, oversized source) yields a clear error or graceful skip — never a crash or hang. | automated (server + CLI error tests) |

---

## B. Phase 6 (runtime overlay) acceptance grid

| # | Criterion | Status |
|---|---|---|
| 1 | `grackle trace … -o t.jsonl` emits valid JSONL; `node_id`s resolve to static-graph nodes incl. decorated functions. | **6.1 ✓** automated |
| 2 | Tracer correctness: depth correct under exceptions (`PY_UNWIND`); `SystemExit`/`KeyboardInterrupt` still flush; decorated funcs resolve; module frames → file node. | **6.1 ✓** automated |
| 3 | `grackle serve --trace-source t.jsonl` replays over WS: `session_start → N events → session_end`; paced default, `--no-pace` instant. | **6.2 ✓** automated |
| 4 | Live-attach: `grackle trace … --connect ws://…` streams a trace into a running server that fans out to all browsers; late joiners get ring-buffer history. | **6.2 ✓** automated |
| 5 | `pnpm dev` shows the Timeline panel (play/pause/scrub/speed/event-type filters + cumulative⇄sliding heat toggle), node heat-map by call frequency in the visible window, a Stats runtime line, and runtime coverage (touched/cold/hot). | **6.3 ✓** manual smoke |
| 6 | Schema parity for `trace` + `messages` confirmed by `check-parity`. | **6.2 ✓** automated |
| 7 | Cross-OS: Python 3.12+ on macOS + Windows produce equivalent traces; tracer capability-gated otherwise. | **6.1 / CI ✓** automated |
| 8 | Bench: tracer ≤10% overhead; a 30s replay paces correctly; UI stays responsive (heat restyles per frame, not per event). | **6.3 ✓** design + manual |
| 9 | Ship: tag `v0.6.0-phase-6`; write `PHASE_6_SUMMARY.md`; commit `PROJECT_ACCEPTANCE.md`; update `CLAUDE.md` (Phase 6 shipped). | **6.3 ✓** |

---

## C. Phase 7 (runtime scale + real-time streaming) acceptance grid

| # | Criterion | Status |
|---|---|---|
| 1 | **Batched ingest.** `addTraceEvents(batch)` exists; live ingest coalesces to one append per rAF; heat/coverage/playhead identical to per-event ingest. | **7.1 ✓** automated |
| 2 | **Count-bounded ring buffer.** `GRACKLE_TRACE_BUFFER_MAX_EVENTS=N` evicts oldest beyond N (in addition to the time window); a late joiner receives ≤N events. | **7.1 ✓** automated |
| 3 | **Real-time stream.** `grackle trace SCRIPT --connect ws://… --stream` emits `session_start → event* → session_end` while the script runs; a consumer sees events mid-execution. | **7.2 ✓** automated |
| 4 | **Hot path stays cheap.** Callback only enqueues (no I/O/await/lock); tracer overhead ≤10% on the 5 s workload with the real-time sink active. | **7.2 ✓** design + automated |
| 5 | **Exit correctness.** `session_end` always sent and queue fully drained (no tail loss) even on script exception / `sys.exit()` / KeyboardInterrupt. | **7.2 ✓** automated |
| 6 | **Backpressure.** With small `GRACKLE_STREAM_MAX_INFLIGHT` + a flooding script: memory bounded; `dropped + received == produced`; `session_end.event_count == received`. | **7.2 ✓** automated |
| 7 | **No artificial pacing.** Real-time events sent back-to-back regardless of `ts_ns` gaps; `--stream` rejects `--output`; `--no-pace` is a guarded no-op. | **7.2 ✓** automated |
| 8 | **Server seek.** `serve --trace-source` advertises `seekable:true`; `trace_seek_request{start,count}` → `trace_window` (correct slice + `total`, echoes clamped id); unknown session → `trace_seek_error`. | **7.3 ✓** automated |
| 9 | **Feature-detect.** Seekable sessions request windows (scrubber + sliding heat); non-seekable (live/real-time) keep the buffered path. Cumulative-heat/coverage limitation documented. | **7.3 ✓** automated + manual |
| 10 | **Schema parity.** The 3 new message types pass `check-parity`; all `type` values stay open strings (ADR-0004). | **7.3 ✓** automated |
| 11 | **Cross-OS.** Real-time streaming (thread + asyncio + websockets) works on Python 3.12+ macOS + Windows; capability-gated otherwise. | **7.2 / CI ✓** automated |
| 12 | **Ship.** ADR-0016 + 0017 accepted; `PHASE_7_SUMMARY.md`; `PROJECT_ACCEPTANCE.md` updated (17 ADRs, Phase 7 grid); `CLAUDE.md`; version 0.7.0; tag `v0.7.0-phase-7`. | **7.H ✓** |

---

## Verifying the criteria themselves

Each whole-product item maps to either an automated check (CI matrix, per-chunk gate, bench script) or a documented manual smoke recorded in the phase `*_SUMMARY.md`. Items are marked accordingly.

The per-chunk gate used throughout Phase 6 development:

```bash
pnpm --filter @grackle/frontend test --run && \
pnpm --filter @grackle/frontend typecheck && \
pnpm lint && \
pnpm typecheck && \
pnpm check-parity
```

Full pre-push gate (run by Lefthook):

```bash
pnpm test          # all packages, parallel
pnpm typecheck     # tsc -b across workspace
pnpm lint          # biome ci .
(cd packages/agent && uv run mypy --strict src tests)
(cd packages/agent && uv run pytest -q)
```
