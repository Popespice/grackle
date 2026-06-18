# Phase 8 Summary — Analysis platform

**Tag:** `v0.8.0-phase-8`
**Shipped:** 2026-06-18

Phase 8 turns the raw trace stream of Phases 6–7 into an analysis platform: call-tree
reconstruction + flame graph, a server-side aggregation engine with persisted sessions,
differential analysis, and a second runtime language (TypeScript/Node via the V8 Inspector)
— all on the **unchanged** `TraceEvent` schema. Five ADRs (0018–0022) were accepted.

## Chunk numbering note (retired "8.1")

The tee sink (`--stream + --output`) shipped first under the informal label **"8.1"** (its
commit / PR #30 / tag carry that label). That label is **retired**: the source plan
(`~/.claude/plans/outline-phase-8-analysis-platform.md`) puts the tee sink inside **8.3 /
ADR-0020** (the `MultiSink` for `--stream --output`), and the plan's real **8.1 is the
server-side aggregation engine (ADR-0018)**, which shipped as part of 8.3. The forward
roadmap uses the plan's canonical numbering. There is no 8.1 chunk in this summary; the tee
sink is described first below under its own heading.

## What shipped

### Tee sink — `--stream + --output` (PR #30)

- `grackle trace --stream --connect URL --output FILE` now **tees**: simultaneously streams
  to the WS server **and** writes a lossless JSONL file. A down-payment on 8.3's `MultiSink`
  (ADR-0020), pulled forward and shipped standalone.
- Hot path uses a plain `list` closure (`_buf.append`) — single-threaded tracer callbacks
  make `SimpleQueue` unnecessary. The file is a lossless superset of server-received events
  (the WS sender may drop under backpressure, the file never does → `file_count >=
  streamed_count`).
- On `TraceCapExceeded`: the captured prefix is written **first**, then the exception is
  re-raised — the user gets both the file and the cap error. `write_jsonl` is wrapped so I/O
  errors surface as a `ClickException`, not a bare traceback.

### 8.2 — Call-tree reconstruction + flame graph (PR #31, ADR-0019)

Pure-frontend; **no new wire message** (`KNOWN_MESSAGE_TYPES` unchanged; `check-parity`
no-op).

- `graph/callTree.ts` — `buildCallTree` (depth-driven reconstruction with implicit-close
  recovery), `aggregateCallTree` (per-thread), `hotPath`. The real tracer emits only
  `call`/`return`/`exception`/`line` — no `unwind`, no `yield`/`resume` — so a `call` can have
  no matching `return`; recovery is depth-driven. `call` and its matching `return` carry the
  **same** `frame_depth` (the frame's own depth); display depth is tree position, not
  `frame_depth` (windowed streams).
- `graph/flameLayout.ts` — pure `layoutFlame` / `hitTest` / `maxDepth` / `frameColor` (canvas
  geometry extracted for jsdom testability).
- `export/speedscope.ts` + `export/chromeTrace.ts` — export **and** round-trip import.
- `panels/FlameGraphPanel.tsx` — canvas, click-to-focus via `selectNode`, `fetchFullTrace.ts`
  paged bridge for seekable sessions; `useCallTree.ts` hook (mirrors `useHeatmap`).

### 8.3 — Aggregation engine + trace persistence + session library (PR #32, ADR-0018 + ADR-0020)

Combined chunk; one codegen pass. **`KNOWN_MESSAGE_TYPES` 12 → 17**: `trace_query_request` /
`trace_query_response`, `session_list_request` / `session_list_response`, `session_load_request`.

- `python_runtime/aggregates.py` — `TraceAggregates`: one-pass JSONL scan → per-node sorted
  hit lists + first-seen index; `bisect`-based `cumulative_heat` / `coverage_count` / `top_k`.
  `build_seekable(path)` builds the `JsonlIndex` **and** aggregates in a single scan.
- `session_store.py` — stdlib `sqlite3` WAL store, `threading.Lock`-guarded, metadata-only;
  exposed via `serve --store PATH`.
- `graph_analysis.py` — `enrich_metadata(graph)` injects agent-side hub-score (top-50) +
  iterative Tarjan SCC cycles into `graph.metadata` (memoized by topology signature).
- `server.py` — per-session `seekable_sessions` registry (seek + query resolve against it);
  `--trace-source` indexed into the store at startup (stable `uuid5` id; survives restart).
- Frontend — `SessionLibraryPanel`, `requestTraceQuery` / `requestSessionList` /
  `sendSessionLoad`, an `agentHeat` store, `useHeatmap` using `agentHeat` in
  seekable+cumulative mode, `analysis/index.ts` reading `graph.metadata`.

### 8.4 — Differential analysis (PR #33, ADR-0021)

- `diff.py` — pure `diff_trace_vs_static` + `diff_trace_vs_trace` + `has_regression`; statuses
  `touched / cold / new / gone / hotter / colder / same`. `TraceAggregates.node_ids` property
  (`frozenset[str]`).
- `grackle diff A.jsonl B.jsonl` CLI — text / JSON output, `--only STATUS`, **exits 1 on
  regression** (CI-usable).
- Frontend `graph/diff.ts` (all pure) + `DiffPanel` — trace-vs-static by default; "Set as
  baseline" → trace-vs-trace with a regression banner. Graph overlay is **opt-in** via a
  "Show overlay" toggle (default off) so it never silently suppresses the Phase-6 heat-map;
  overlay store-writes debounced 150 ms; `setGraph` clears a stale baseline.

### 8.5 — Polyglot Node/V8 runtime (PR #35, ADR-0022)

`NodeRuntimeAdapter` (language `"typescript"`, in `packages/agent/src/grackle/node_runtime/`)
drives Node over the V8 Inspector (CDP) on `127.0.0.1`, emits the **same** `TraceEvent` schema
resolved to TS static-graph node IDs, and reuses the entire Phase 6–8 transport/UI — **no wire
change**, **no new deps** (a minimal CDP client over the existing `websockets`).

- **Hybrid two-channel mechanism** (the two channels measure different things, so they go to
  different channels — no double-count, no frontend change):
  - `trace()` = CPU **sampling** profiler → `profile_reconstruct.reconstruct` (pure;
    stack-diff over samples by V8 tree-node identity → faithful `call` / `return` with real
    `frame_depth` + `ts_ns`) → `--connect` replay / `-o`.
  - `trace_streaming()` = **precise-coverage** polling → `coverage_poll` (pure) → one coarse
    live `call` per active function per poll (`frame_depth: 0`, `metadata.live: true`, exact
    per-poll delta in `metadata.count`) → `--stream`.
- Node-ID resolution via **type-stripping** (Node ≥ 22.6; `--experimental-strip-types`
  replaces annotations with whitespace → line numbers preserved, URL stays `.ts`).
- Capability-gated (`node` present + version ≥ 22.6, cached); `.tsx` / `.jsx` give a clean
  "Phase 9" error; `.pyw` / extension-less → Python. Pure reconstruction / coverage / resolver
  tests run with **no Node installed**; 5 Node-gated e2e run in CI (Node 22 job).

### 8.6 — Tech-debt sweep (PR #36, no ADR)

Behavior-preserving; **no wire/schema change**. One bundled chunk = Tier-A register **plus**
the deferred 8.5 `node_runtime/` refactor pass.

- **Tier-A:** A1 schema↔`messages.ts` parity guard (`verify-parity.mjs` canonical pass +
  Python-side `test_protocol.py`); A2 generic `pendingRequest` collapses the four `client.ts`
  request/response copies; A6 `server.py` decomposition → `python_runtime/live_buffer.py` (ring
  buffer) + `python_runtime/file_replay.py` (seekable sessions / replay / stored-session load /
  trace-source registration), leaving `server.py` a WS-dispatch shell; A7 magic-number
  rationale comments; A9 `test_protocol.py`. **A8 (registration decorator) skipped** — net
  churn + conflicts ADR-0003 (Protocols, not ABCs).
- **8.5 refactor:** shared `RuntimeResolver` base (`adapters/runtime_resolution.py`);
  `new_trace_event` / `enforce_event_cap` factories (`adapters/base.py`);
  `registry.build_static_graph` + `runtime_extensions`; adapter-owned `extensions` +
  `runtime_unavailable_reason` gate (CLI dispatch now registry-driven); dead CDP-listener
  removal; `_NodeSession` `asyncio.Event` → reusable `Future`; fused one-pass coverage poll.

---

## Code-review fixes (per chunk, pre-merge)

Each chunk went through an adversarial `/code-review` pass; the actionable findings were fixed
in a second commit folded into the squash before merge. The highest-signal fixes:

| Chunk | Representative fixes |
|---|---|
| 8.2 | Per-thread aggregation merge; flame width via callback-ref measurement (was 0-wide for post-mount sessions); export disabled when windowed; import disabled while live-streaming. |
| 8.3 | Hub-score shape mismatch (`{node_id}` vs `{node}`) crashed `StatsPanel` on every graph → `index.ts` rehydrates; loaded sessions get aggregates via per-session `build_seekable`; `--store --trace-source` indexes at startup; `SessionStore` lock + executor offload + close-on-exit. |
| 8.4 | **[HIGH]** DiffPanel auto-overlay silently suppressed the heat-map → graph overlay made opt-in; live overlay debounced; stale baseline cleared on graph swap. |
| 8.5 | **[HIGH]** coverage `--stream` exception event had `ts_ns = 0` (mis-sorted to front → ~115-day spans) → stamp a real ts; async errors after top-level `await` now reported; coverage `int()` parsing guarded; CDP attach-phase timeouts; multi-byte stderr decode across chunk boundaries. |
| 8.6 | `verify-parity.mjs` regex word-boundary; launcher poll busy-spin guard; production-dead coverage helpers moved to test oracle; `RuntimeResolver` made a proper ABC. |

---

## Acceptance grid — Phase 8

| # | Criterion | Status |
|---|---|---|
| 1 | **Tee sink.** `grackle trace --stream --connect URL --output FILE` writes a lossless JSONL file **and** streams live; `file_count >= streamed_count`; on cap, the prefix is written before re-raise. | **✓** automated |
| 2 | **Call-tree.** `buildCallTree` reconstructs `call`/`return` with depth-driven implicit-close recovery; `aggregateCallTree` is per-thread; `hotPath` returns the heaviest chain. | **8.2 ✓** automated |
| 3 | **Flame graph.** `FlameGraphPanel` renders the call tree on canvas with click-to-focus; pure `layoutFlame`/`hitTest`/`maxDepth` are jsdom-tested; speedscope + Chrome-trace export round-trip on import. | **8.2 ✓** automated + manual |
| 4 | **Aggregation engine.** `TraceAggregates` builds per-node sorted hits in one scan; `cumulative_heat` / `coverage_count` / `top_k` are `bisect`-based; `build_seekable` builds index + aggregates in a single pass. | **8.3 ✓** automated |
| 5 | **Session store.** `serve --store PATH` persists session metadata (sqlite WAL, lock-guarded); `--trace-source` is indexed at startup with a stable id and survives restart. | **8.3 ✓** automated |
| 6 | **Query / list / load over the wire.** `trace_query_request` → `trace_query_response`, `session_list_request` → `session_list_response`, `session_load_request`; loaded sessions are queryable (cumulative heat works). `KNOWN_MESSAGE_TYPES` 12 → 17, all open strings. | **8.3 ✓** automated |
| 7 | **Differential analysis.** `diff.py` computes trace-vs-static (dead/cold) and trace-vs-trace (new/gone/hotter/colder); `grackle diff A B` prints text/JSON, honours `--only`, and **exits 1 on regression**. | **8.4 ✓** automated |
| 8 | **Diff UI is non-destructive.** `DiffPanel` summary always shows; the graph overlay is opt-in (default off) and never suppresses the heat-map; a stale baseline is cleared on graph swap. | **8.4 ✓** automated |
| 9 | **Polyglot runtime.** `grackle trace app.ts -o t.jsonl` drives Node over the V8 Inspector on `127.0.0.1`, resolves frames to TS static-graph node IDs, and emits the same `TraceEvent` schema; replay / heat / flame / `grackle diff` work on the file. | **8.5 ✓** automated (Node-gated e2e) |
| 10 | **Two-channel fidelity.** Sampling channel (`trace()`) gives faithful `call`/`return` + `frame_depth` + `ts_ns`; coverage channel (`trace_streaming()`) gives coarse live counts (`metadata.count`). The asymmetry is documented in ADR-0022 + `--help`. | **8.5 ✓** automated + design |
| 11 | **Capability gate.** Node adapter registers unconditionally (visible in `grackle languages`); absent/old Node → clean remediation, never a traceback; `.tsx`/`.jsx` → clear "Phase 9" error; `.pyw`/extension-less → Python. | **8.5 ✓** automated |
| 12 | **No wire change for 8.5/8.6.** `KNOWN_MESSAGE_TYPES` unchanged after 8.3; `check-parity` a no-op for 8.5 and 8.6; the A1 parity guard asserts schema ≡ `messages.ts` ≡ builders. | **8.5 / 8.6 ✓** automated |
| 13 | **Tech-debt sweep is behavior-preserving.** `server.py` decomposed into `live_buffer.py` + `file_replay.py`; shared `RuntimeResolver` base; `new_trace_event`/`enforce_event_cap` factories; full gate green with no behavior change. | **8.6 ✓** automated |
| 14 | **Cross-OS.** All of the above green on the Ubuntu + Windows CI matrix; Node e2e run on the Node-22 job; capability-gated otherwise. | **CI ✓** automated |
| 15 | **Ship.** ADRs 0018–0022 accepted; `PHASE_8_SUMMARY.md`; `PROJECT_ACCEPTANCE.md` §D grid (22 ADRs); `CLAUDE.md` (Phase 8 shipped, Phase 9 planned); version 0.8.0; tag `v0.8.0-phase-8`. | **8.H ✓** |

---

## Known limitations

- **Node `--stream` heat is activity-coarse, not magnitude-faithful.** Live consumers count
  events and ignore `metadata.count`; magnitude-faithful heat and `grackle diff` input come
  from the **sampling** channel. Count-weighting in agent-side aggregation is a Phase-9
  fast-follow (rides with the Go adapter, which needs it).
- **Node runtime is type-stripping only.** Compiled `.js` / bundles (sourcemaps), `.tsx`/`.jsx`
  (external loaders), and worker threads (separate inspector targets) are **Phase 9+**.
- **Go and Rust remain static-only.** Their runtime trace adapters are **Phase 9** — today only
  Python (`sys.monitoring`) and TypeScript/Node (V8 Inspector) register a `RuntimeAdapter`.
- **Live `--stream` sessions are not auto-recorded.** Only replay / `--trace-source` files are
  indexed into the session store today; a live-stream recording sink is a Phase-9 small debt.
- **`JsonlIndex` is dense** (~8 bytes/event offset; ~80 MB at 10 M events); a sparse index
  stays re-parked (dense is acceptable to ~10 M events per its own docstring).

---

## Phase 9 preview

Phase 9 closes the runtime-adapter gap and pays down two debts (full plan:
`~/.claude/plans/i-want-to-get-glistening-eich.md`):

- **9.1 — Go runtime adapter** (ADR-0023): `go build -cover` → `GOCOVERDIR` → `go tool covdata
  textfmt` → pure-Python parse → count-carrying `call` events; includes `metadata.count`
  weighting in `aggregates.py` + `diff.py`.
- **9.2 — Rust runtime adapter** (ADR-0024): `RUSTFLAGS=-Cinstrument-coverage` → `llvm-profdata
  merge` + `llvm-cov export --format=json` → counts by `(path, line)`. Flagged high-slip-risk;
  9.1 alone is a complete shippable story.
- **9.3 — small debts:** live-stream recording sink (ADR-0020 amend) + diff-baseline
  `sessionStorage` persistence (ADR-0021 amend).

Go/Rust ship `trace()` only — exact-count coarse events (`frame_depth: 0`, `metadata.count`),
per the ADR-0022 asymmetric-fidelity precedent; `trace_streaming()` raises clean typed errors.
No wire-schema change anywhere in the phase.
