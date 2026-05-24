# Project-wide Acceptance Criteria

> Committed as `PROJECT_ACCEPTANCE.md` during the Phase 6.3 close.
> Two grids: whole-product definition-of-done + Phase 6 runtime-overlay acceptance.
> Each item is marked **automated** (CI / per-chunk gate / bench) or **manual** (recorded in `PHASE_6_SUMMARY.md`).

---

## A. Whole-product definition of done — "grackle is what it says it is"

| # | Criterion | Verification |
|---|---|---|
| 1 | **Tagline test.** Fresh clone → `pnpm install` + `uv sync` + `pnpm codegen` → `pnpm dev` against `fixtures/tiny-python-app` with a trace loaded → the browser shows a **live** visualization (static graph **plus** runtime overlay: timeline scrubs, nodes heat-map by call frequency), not a static one. | manual |
| 2 | **End-to-end pipeline.** `parse → trace → serve → visualize` works on (a) `tiny-python-app`, (b) `tiny-polyglot` (Python side traced), (c) grackle's own repo. | manual |
| 3 | **Polyglot static.** Python, TypeScript, Go, Rust adapters each emit a graph; `parse_all` merges them; HTTP-route + subprocess cross-language edges resolve on `tiny-polyglot`. | automated (`pytest`, `check-parity`) |
| 4 | **Local-first invariant.** Server binds only to `127.0.0.1` (warns otherwise); zero network egress, no telemetry, no cloud dependency; works fully offline. | automated (`test_server.py` bind assertion) + manual |
| 5 | **Cross-platform.** Identical node IDs and graphs on macOS / Linux / Windows (POSIX path discipline); CI matrix green on all three. | automated (CI matrix) |
| 6 | **Runtime overlay.** `sys.monitoring` tracer (Python 3.12+) tags events to static node IDs; pre-3.12 degrades to static-only with a clear capability message — never a crash. | automated (`test_adapter.py`, capability test) |
| 7 | **Performance.** Tracer overhead ≤10% on a 5s workload; UI stays interactive (pan/zoom) on the `stress-2k` fixture and during a 30s paced replay. | bench (manual timing) |
| 8 | **Determinism.** `grackle parse` and `grackle trace` (with `PYTHONHASHSEED=0`) are reproducible; golden fixtures stable across runs. | automated (golden fixture tests) |
| 9 | **Quality gates.** `pytest` + `mypy --strict` + `tsc` + `biome` + frontend tests + `check-parity` all green on the CI matrix; no skipped or disabled guards. | automated (CI + pre-push hooks) |
| 10 | **Documented architecture.** Every cross-cutting decision has an accepted ADR (15 total); each phase has a `*_SUMMARY.md` card; `CLAUDE.md` current. | manual |
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

## Verifying the criteria themselves

Each whole-product item maps to either an automated check (CI matrix, per-chunk gate, bench script) or a documented manual smoke recorded in `PHASE_6_SUMMARY.md`. Items are marked accordingly.

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
