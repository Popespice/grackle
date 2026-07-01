# Project-wide Acceptance Criteria

> Last updated during the Phase 9.H close.
> Five grids: whole-product definition-of-done + Phase 6 + Phase 7 + Phase 8 + Phase 9 acceptance.
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
| 6 | **Runtime overlay.** Four runtime adapters tag events to static node IDs on the shared `TraceEvent` schema: `sys.monitoring` (Python 3.12+), the V8 Inspector (TypeScript/Node ≥ 22.6, ADR-0022), and coverage-instrumentation adapters for Go (ADR-0023) and Rust (ADR-0024) that ship `trace()`-only exact-count coarse events. A missing/old toolchain degrades to static-only with a clear capability message — never a crash. | automated (`test_adapter.py`, capability test, Node/Go/Rust-gated e2e) |
| 7 | **Performance.** Tracer overhead ≤10% on a 5s workload (including with the real-time `--stream` sink active); UI stays interactive during a real-time stream and a 50k-event replay (batched rAF ingest avoids quadratic accumulation). | bench (manual timing) + automated |
| 8 | **Determinism.** `grackle parse` and `grackle trace` (with `PYTHONHASHSEED=0`) are reproducible; golden fixtures stable across runs. | automated (golden fixture tests) |
| 9 | **Quality gates.** `pytest` + `mypy --strict` + `tsc` + `biome` + frontend tests + `check-parity` all green on the CI matrix; no skipped or disabled guards. | automated (CI + pre-push hooks) |
| 10 | **Documented architecture.** Every cross-cutting decision has an accepted ADR (24 total); each phase has a `*_SUMMARY.md` card; `CLAUDE.md` current. | manual |
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

## D. Phase 8 (analysis platform) acceptance grid

| # | Criterion | Status |
|---|---|---|
| 1 | **Tee sink.** `grackle trace --stream --connect URL --output FILE` writes a lossless JSONL file **and** streams live; `file_count >= streamed_count`; on cap, the prefix is written before re-raise. | **✓** automated |
| 2 | **Call-tree.** `buildCallTree` reconstructs `call`/`return` with depth-driven implicit-close recovery; `aggregateCallTree` is per-thread; `hotPath` returns the heaviest chain. | **8.2 ✓** automated |
| 3 | **Flame graph.** `FlameGraphPanel` renders the call tree on canvas with click-to-focus; pure `layoutFlame`/`hitTest`/`maxDepth` are jsdom-tested; speedscope + Chrome-trace export round-trip on import. | **8.2 ✓** automated + manual |
| 4 | **Aggregation engine.** `TraceAggregates` builds per-node sorted hits in one scan; `cumulative_heat` / `coverage_count` / `top_k` are `bisect`-based; `build_seekable` builds index + aggregates in a single pass. | **8.3 ✓** automated |
| 5 | **Session store.** `serve --store PATH` persists session metadata (sqlite WAL, lock-guarded); `--trace-source` is indexed at startup with a stable id and survives restart. | **8.3 ✓** automated |
| 6 | **Query / list / load over the wire.** `trace_query_request` → `trace_query_response`, `session_list_request` → `session_list_response`, `session_load_request`; loaded sessions are queryable. `KNOWN_MESSAGE_TYPES` 12 → 17, all open strings. | **8.3 ✓** automated |
| 7 | **Differential analysis.** `diff.py` computes trace-vs-static (dead/cold) and trace-vs-trace (new/gone/hotter/colder); `grackle diff A B` prints text/JSON, honours `--only`, and **exits 1 on regression**. | **8.4 ✓** automated |
| 8 | **Diff UI is non-destructive.** `DiffPanel` summary always shows; the graph overlay is opt-in (default off) and never suppresses the heat-map; a stale baseline is cleared on graph swap. | **8.4 ✓** automated |
| 9 | **Polyglot runtime.** `grackle trace app.ts -o t.jsonl` drives Node over the V8 Inspector on `127.0.0.1`, resolves frames to TS static-graph node IDs, and emits the same `TraceEvent` schema; replay / heat / flame / `grackle diff` work on the file. | **8.5 ✓** automated (Node-gated e2e) |
| 10 | **Two-channel fidelity.** Sampling channel (`trace()`) gives faithful `call`/`return` + `frame_depth` + `ts_ns`; coverage channel (`trace_streaming()`) gives coarse live counts (`metadata.count`). Asymmetry documented in ADR-0022 + `--help`. | **8.5 ✓** automated + design |
| 11 | **Capability gate.** Node adapter registers unconditionally (visible in `grackle languages`); absent/old Node → clean remediation, never a traceback; `.tsx`/`.jsx` → clear "Phase 9" error; `.pyw`/extension-less → Python. | **8.5 ✓** automated |
| 12 | **No wire change for 8.5/8.6.** `KNOWN_MESSAGE_TYPES` unchanged after 8.3; `check-parity` a no-op for 8.5 and 8.6; the A1 parity guard asserts schema ≡ `messages.ts` ≡ builders. | **8.5 / 8.6 ✓** automated |
| 13 | **Tech-debt sweep is behavior-preserving.** `server.py` decomposed into `live_buffer.py` + `file_replay.py`; shared `RuntimeResolver` base; `new_trace_event`/`enforce_event_cap` factories; full gate green with no behavior change. | **8.6 ✓** automated |
| 14 | **Cross-OS.** All of the above green on the Ubuntu + Windows CI matrix; Node e2e run on the Node-22 job; capability-gated otherwise. | **CI ✓** automated |
| 15 | **Ship.** ADRs 0018–0022 accepted; `PHASE_8_SUMMARY.md`; `PROJECT_ACCEPTANCE.md` §D grid (22 ADRs); `CLAUDE.md` (Phase 8 shipped, Phase 9 planned); version 0.8.0; tag `v0.8.0-phase-8`. | **8.H ✓** |

---

## E. Phase 9 (native runtime adapters) acceptance grid

| # | Criterion | Status |
|---|---|---|
| 1 | **Go runtime adapter.** `grackle trace app.go -o t.jsonl` drives Go via `go build -cover` → `GOCOVERDIR` → `go tool covdata textfmt` → pure-Python parse → one `call` per executed function (`metadata.count`, `frame_depth: 0`), resolved to Go static-graph node IDs; replay / heat / `grackle diff` work on the file. | **9.1 ✓** automated (Go-gated e2e) |
| 2 | **Go correctness traps.** covdata import-path prefix stripped via `go.mod` module read + `to_posix`; block-start-line ≠ decl-line handled by decl-line bisect in `RuntimeResolver`; `GOWORK=off`; toolchain errors → typed `GoRuntimeError`. | **9.1 ✓** automated |
| 3 | **Count weighting.** `TraceAggregates` and `diff.py` weight by `metadata.count` (default 1 → byte-identical output for Python/Node traces). | **9.1 ✓** automated |
| 4 | **Rust runtime adapter.** `grackle trace app.rs -o t.jsonl` drives Rust via `RUSTFLAGS=-Cinstrument-coverage` → `llvm-profdata merge` → `llvm-cov export` → pure-Python parse → one `call` per executed function (`metadata.count`, `frame_depth: 0`), resolved to Rust node IDs. | **9.2 ✓** automated (Rust-gated e2e) |
| 5 | **Rust correctness traps.** absolute-path normalization via `to_posix`; monomorphised generics SUM counts by `node_id`; sysroot binary discovery via `rustc --print sysroot` + host triple; macro-expansion regions (fileID>0) filtered; workspace-root-as-package fallback. | **9.2 ✓** automated |
| 6 | **Channel contract (Go/Rust).** Both ship `trace()` only — exact-count coarse events per the ADR-0022 asymmetric-fidelity precedent; `trace_streaming()` raises a clean typed error with remediation. | **9.1 / 9.2 ✓** automated + design |
| 7 | **Capability gate (Go/Rust).** Both register unconditionally (visible in `grackle languages`); missing toolchain (Go ≥ 1.20; Rust cargo + rustc + `llvm-tools-preview`) → clean typed remediation, never a traceback. | **9.1 / 9.2 ✓** automated |
| 8 | **Runtime-adapter matrix complete.** Python (`sys.monitoring`), TypeScript/Node (V8 Inspector), Go (coverage), Rust (LLVM coverage) — all four language runtimes now register a `RuntimeAdapter`. | **9.1 / 9.2 ✓** automated |
| 9 | **Live-stream recording sink.** `serve --store PATH` tees inbound producer sessions to `<store>/recordings/<id>.jsonl` and registers them (loadable + seekable); finalize fires on clean `session_end`, producer disconnect, and server shutdown; binary-mode atomic write with single-`truncate` salvage; empty/broken sessions never registered. | **9.3 ✓** automated |
| 10 | **Recording-sink safety.** `is_safe_session_id` allow-list blocks path traversal; exclusive-create guards a colliding session id; a recording failure never harms the live fan-out or crashes the receive loop; startup sweep clears orphaned `.part` files. | **9.3 ✓** automated |
| 11 | **Diff-baseline persistence.** `DiffPanel` "Set as baseline" persists to `sessionStorage` keyed by `graphCacheKey`; restored on reload (same project → restore, different project → hash miss); cleared via the existing affordance; overlay auto-enable only on first restore per mount; empty/negative baselines rejected. | **9.3 ✓** automated |
| 12 | **ADR discipline.** ADR-0023 (Go) + ADR-0024 (Rust) accepted; ADR-0020 + ADR-0021 amended (recording sink; baseline persistence) — no new ADRs in 9.3, count stays 24. | **9.1 / 9.2 / 9.3 ✓** manual |
| 13 | **No wire-schema change; cross-OS.** `KNOWN_MESSAGE_TYPES` unchanged all phase, `check-parity` a no-op for every chunk; all green on the Ubuntu + Windows CI matrix (Go/Rust e2e capability-gated). | **9.1 / 9.2 / 9.3 / CI ✓** automated |
| 14 | **Ship.** ADRs 0023–0024 accepted (0020 + 0021 amended); `PHASE_9_SUMMARY.md`; `PROJECT_ACCEPTANCE.md` §E grid (24 ADRs); `CLAUDE.md` (Phase 9 shipped, Phase 10 planned); version 0.9.0; tag `v0.9.0-phase-9`. | **9.H ✓** |

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
