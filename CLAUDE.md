# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

grackle is a local-first live code visualizer for Python: static graph from `ast`, runtime overlay via `sys.monitoring` (Python 3.12+) over a `127.0.0.1` WebSocket, React + Sigma.js frontend.

Status: **active solo development, contributions closed** (see `CONTRIBUTING.md`). Treat external PRs and issues as out of scope.

## Repo shape

pnpm workspace monorepo with four packages plus shared schema:

- `packages/agent/` — Python WebSocket server + adapters (`uv`-managed, hatchling build, `grackle` CLI entry point)
- `packages/frontend/` — React 19 + Vite + Sigma.js + Zustand
- `packages/shared-types/` — JSON Schema is the **single source of truth**; codegen emits TS interfaces *and* Python TypedDicts
- `packages/nn/` — `grackle-nn`, a standalone (`uv`-managed) from-scratch numpy MLP designed to be legible to grackle's own tracer (Phase 11, "watch it learn"). `grackle` is an editable **dev-only** dependency of `packages/nn` (for its traceability tests); the reverse never holds — the agent's hard dependencies stay numpy-free.

JSON Schema → TS + Python codegen is the seam that prevents protocol drift. Generated files (`packages/shared-types/src/generated/`, `packages/agent/src/grackle/_generated/`) are **gitignored** — run `pnpm codegen` after a fresh clone or schema change. The hand-written `packages/shared-types/src/messages.ts` is the canonical public API; the generated TS is a sanity-check artifact and is not re-exported.

## Common commands

Run from the repo root unless noted.

```bash
# Bootstrap
pnpm install
(cd packages/agent && uv sync)
pnpm codegen                              # required after fresh clone or schema change

# Dev (agent + frontend together)
pnpm dev                                  # agent on :7878, frontend on :5173

# Full check (matches pre-push + CI)
pnpm lint                                 # biome ci .
pnpm typecheck                            # tsc -b across workspace
pnpm test                                 # all packages, parallel
pnpm check-parity                         # diff fresh codegen against committed generated

# Agent-only (Python)
(cd packages/agent && uv run pytest -q)
(cd packages/agent && uv run pytest tests/test_paths.py::test_to_posix_round_trip)  # single test
(cd packages/agent && uv run ruff check .)
(cd packages/agent && uv run mypy --strict src tests)
(cd packages/agent && uv run grackle serve)
(cd packages/agent && uv run grackle languages)

# Frontend-only
pnpm --filter @grackle/frontend test --run
pnpm --filter @grackle/frontend test -- src/components/Foo.test.tsx   # single test file
pnpm --filter @grackle/frontend typecheck

# nn-only (Python, standalone package — "watch it learn", Phase 11)
(cd packages/nn && uv sync)
(cd packages/nn && uv run pytest -q)
(cd packages/nn && uv run ruff check . && uv run mypy --strict src tests)
pnpm nn:trace                             # trace the training demo -> packages/nn/run-a.jsonl
```

Auto-fix on dirty repos: `pnpm format` (biome write) and `uv run ruff format` in the agent.

## Architecture seams (read these to be productive)

- **`docs/adr/`** — 28 ADRs: monorepo structure (0001), WebSocket transport (0002), adapter design (0003), open-string extension surface (0004), kind registry (0005), Python ast vs Tree-sitter (0006), panel/slot system (0007), analysis registry (0008), Tree-sitter integration (0009), Rust adapter (0010), cycle detection (0011), cross-language edges (0012), runtime trace event schema (0013), trace transport (0014), runtime overlay UI (0015), real-time trace streaming (0016), server-side trace seek (0017), server-side aggregation engine (0018), call-tree + flame graph (0019), trace persistence + session store (0020), differential analysis (0021), polyglot runtime via V8 Inspector (0022), Go runtime coverage (0023), Rust runtime coverage (0024), value capture (0025), explanation layer: edge evidence + causal path (0026), watch mode (0027), the NN as a traceable subject (0028). When designing new code, check whether an ADR already constrains the decision.
- **`docs/cross-platform.md`** — the cross-platform contract (path handling, `spawn` semantics, line endings, CI matrix). Non-negotiable; CI runs Ubuntu + Windows on every PR, all three OSes on push to main.
- **`packages/agent/src/grackle/adapters/`** — `StaticParserAdapter` and `RuntimeAdapter` are `@runtime_checkable` `typing.Protocol`s (not ABCs — see ADR-0003). `AdapterRegistry` is a thread-safe module singleton; adapters register themselves and the CLI/UI look them up by language string.

## Non-obvious conventions

- **POSIX path discipline.** All path-bearing fields that cross the wire or persist (node IDs, annotation keys, manifest entries) must be POSIX-relative strings — `services/auth.py`, never `services\auth.py`. Use `grackle.paths.to_posix(p, root)`; do not call `.relative_to()` directly outside `paths.py`. A single missed call site silently diverges IDs between macOS and Windows. The `ruff PTH` ruleset and a path-discipline lint test guard this.
- **Open strings, not enums, on extension surfaces.** `language`, node `kind`, edge `kind`, trace `type` — all open `str`. Unknown values are ignored, not errors. See ADR-0004. The `KNOWN_*` `as const` arrays / `_canonical_*` validators in `kinds.py` are display-time conveniences, not gatekeepers.
- **Generated files have `_generated/` paths and are excluded from lint/typecheck** (`ruff exclude`, `mypy exclude` in `pyproject.toml`). Never edit them by hand; edit the schema and re-run `pnpm codegen`.
- **Lefthook enforces things locally before push.** `pre-commit` runs Biome + Ruff + schema parity; `pre-push` runs full typecheck + frontend tests + `mypy --strict` + pytest. If a hook fails, fix the underlying issue — don't `--no-verify`.
- **Atomic writes** — write to `.tmp`, then `Path.replace()` (not `Path.rename()` — Windows `rename` fails on existing target; this caused commit `3c31dca`).
- **Bind only to `127.0.0.1`.** Never `0.0.0.0`. Local-first is a product invariant, not a default.
- **Versions are single-source.** `__version__` is read from `importlib.metadata`; don't add string literals that duplicate `pyproject.toml`'s `version`.

## Active roadmap context

Phase 1 (adapter Protocols + `AdapterRegistry` + `grackle languages`) is shipped at tag `v0.1.0-phase-1`. Phase 2 (Python static parser via stdlib `ast`) is shipped at tag `v0.2.0-phase-2`. Phase 3 (frontend renders the static graph) is shipped at tag `v0.3.0-phase-3` — panel/slot chassis, search/filter, Shiki source viewer, stats panel, stress-2k fixture, ADRs 0007+0008. Phase 4 (TypeScript + Go adapters + analysis registry) is shipped at tag `v0.4.0-phase-4` — Tree-sitter chassis, TS + Go adapters, polyglot `parse_all`, `AnalysisRegistry` + hub-score, ADRs 0009 + 0008 amendment. Phase 5 (Rust adapter + cycle detection + cross-language edges) is shipped at tag `v0.5.0-phase-5` — Rust adapter with Cargo workspace support, Tarjan SCC cycle detection panel, HTTP route + subprocess cross-language edges, ADRs 0010–0012. Phase 6 (runtime overlay) is shipped at tag `v0.6.0-phase-6` — `sys.monitoring` tracer (6.1), WebSocket trace transport with file replay + live-attach (6.2), frontend Timeline panel + heat-map + coverage overlay (6.3), oklch→hex Sigma colour fix, ADRs 0013–0015. **Phase 7 (runtime scale + real-time streaming) is shipped at tag `v0.7.0-phase-7`** — batched rAF trace ingest + count-bounded ring buffer (7.1), real-time `--stream` mode via daemon sender thread + `SimpleQueue` hot path (7.2), server-side byte-offset seek index + WS request/response seek channel (7.3), ADRs 0016–0017.

**Phase 8 (analysis platform) is shipped at tag `v0.8.0-phase-8`** (last feature merge `5f3e8a4`, 8.6). Chunk numbering follows the source plan (`~/.claude/plans/outline-phase-8-analysis-platform.md`), anchored on ADRs 0018–0022. **Tee sink** (`--stream + --output`, PR #30) — lossless file + live stream, a down-payment on 8.3's `MultiSink` (ADR-0020) pulled forward; shipped under the informal label "8.1", now **retired** (collides with the plan's real 8.1, the aggregation engine). **8.2** — call-tree reconstruction + flame graph (PR #31, ADR-0019), pure-frontend, no wire change. **8.3** — server-side aggregation engine + trace persistence + session library (PR #32, ADR-0018 + ADR-0020): `TraceAggregates` one-pass scan, sqlite session store, `KNOWN_MESSAGE_TYPES` 12→17. **8.4** — differential analysis (PR #33, ADR-0021): `diff.py` (trace-vs-static + trace-vs-trace) + `grackle diff A.jsonl B.jsonl` CLI (exit 1 on regression) + opt-in `DiffPanel` overlay. **8.5** — polyglot Node/V8 runtime (PR #35, ADR-0022): `node_runtime/` adapter (language `"typescript"`) drives Node over the V8 Inspector (CDP), emits the same `TraceEvent` schema, reuses the whole Phase 6–8 pipeline — no wire change. **8.6** — tech-debt sweep (PR #36, no ADR): A1 schema↔`messages.ts` parity guard, A2 `pendingRequest` `client.ts` dedup, A6 `server.py` decomposition (→ `python_runtime/live_buffer.py` + `file_replay.py`), plus the deferred 8.5 `node_runtime/` refactor pass (shared `RuntimeResolver` base, `new_trace_event`/`enforce_event_cap` factories, registry-driven CLI dispatch). See `PHASE_8_SUMMARY.md` for the full per-chunk acceptance grid.

**Phase 9 (native runtime adapters, Go + Rust, + debt paydown) is shipped at tag `v0.9.0-phase-9`.** Goal: give Go and Rust real `RuntimeAdapter`s that emit the existing `TraceEvent` schema through the unchanged Phase 6–8 pipeline — **no wire-schema change all phase**. Chunks (one squash-merged PR each): **9.0** — ship Phase 8 (PR #39, `50a00b4`, tag `v0.8.0-phase-8`); **9.1** — Go runtime adapter (`go_runtime/`, ADR-0023) via `go build -cover` → `go tool covdata textfmt` → count-carrying `call` events, plus `metadata.count` weighting in `python_runtime/aggregates.py` + `diff.py` — **SHIPPED PR #40, main=`8f79fbb`**; **9.2** — Rust runtime adapter (`rust_runtime/`, ADR-0024) via `RUSTFLAGS=-Cinstrument-coverage` → `llvm-cov export` — **SHIPPED PR #43, main=`6a89192`**; **9.3** — small debts (live-stream recording sink, diff-baseline `sessionStorage` persistence) — **SHIPPED PR #45, main=`c09736e`**; **9.H** — ship (v0.9.0, ADR count stays 24) — **SHIPPED** (this PR: ADRs 0023–0024 accepted, `PHASE_9_SUMMARY.md`, `PROJECT_ACCEPTANCE.md` §E, version 0.9.0, tag `v0.9.0-phase-9` pushed post-merge). Go/Rust ship `trace()` only — exact-count coarse events (`frame_depth:0`, `metadata.count`), per the ADR-0022 asymmetric-fidelity precedent; `trace_streaming()` raises clean typed errors. **Runtime-adapter matrix today:** Python (`sys.monitoring`), TypeScript/Node (V8 Inspector), Go (coverage instrumentation), and Rust (LLVM coverage instrumentation) all register a `RuntimeAdapter`. Full plan: `~/.claude/plans/i-want-to-get-glistening-eich.md`. See `PHASE_9_SUMMARY.md` for the full per-chunk acceptance grid.

**Phase 10 (live growing graph + time-travel debugger + explanation layer) is shipped at tag `v0.10.0-phase-10`.** Goal: capture sampled argument/return values in the Python tracer and build the frontend to scrub/step/inspect them (time-travel debugger); add an explanation layer showing *why* nodes connect (edge evidence) and *why they fired* (causal call path); make the static graph grow live via a file-watcher — three ADRs (0025–0027), Python-only value capture, everything else on the existing transport. Chunks (one squash-merged PR each): **10.1** — safe-repr + redaction module (`value_repr.py`), pure/no wiring — **SHIPPED PR #49, main=`9189497`**; **10.2** — value capture + wire-schema `values` field + CLI (ADR-0025, the first wire-schema change since 8.3) — **SHIPPED PR #51, main=`e304fa7`**; **10.3** — time-travel value inspector + call-step navigation (frontend) — **SHIPPED PR #53, main=`693fd66`**; **10.4** — explanation layer: edge evidence (ADR-0026) — **SHIPPED PR #55, main=`96c172c`**; **10.5** — explanation layer: causal "why did this fire" path (ADR-0026 §8 amendment) — **SHIPPED PR #57, main=`0830b2f`**; **10.6** — watch mode server (ADR-0027, high-risk tail) — **SHIPPED PR #59, main=`99e7bd1`**; **10.7** — watch mode frontend: graph-diff animation — **SHIPPED PR #60, main=`324ca01`**; **10.H** — ship (v0.10.0, ADR count 24→27) — **SHIPPED** (this PR: ADRs 0025–0027 already accepted in their respective implementing chunks, `PHASE_10_SUMMARY.md`, `PROJECT_ACCEPTANCE.md` §F, version 0.10.0, tag `v0.10.0-phase-10` pushed post-merge). Time-travel and explanation are frontend-only reconstructions over the existing trace (no message types beyond 10.2's `values` field); watch mode's MVP is a full `static_graph` re-push, diffed client-side in 10.7 rather than a wire-level `graph_delta`. Full plan: `~/.claude/plans/plan-out-phase-10-mighty-lovelace.md`. See `PHASE_10_SUMMARY.md` for the full per-chunk acceptance grid.

**Phase 10.D (demo branch forward-sync) — partially applied, one sub-chunk still pending push.**
`demo/end-product-preview` (the visitor-facing, CI-exempt, never-merged-to-main preview branch) is
being brought up to v0.10.0. **10.D.1/2+3** (upstream fix + README refresh + golden-trace
fixtures) — **SHIPPED PR #63–64**. **10.D.4** (the demo-branch sync itself, `demo.py`
modernization + session-library wiring + `FixtureSwitcher.tsx`) — staged on local/remote branch
`demo-sync/phase-10.D`, **not yet pushed to `demo/end-product-preview`**; that push needs explicit
approval per `DEMO_BRANCH.md`'s force-push playbook (on the demo branch) and is withheld pending
it. See `PHASE_10_SUMMARY.md` for detail on 10.D.1–3.

**Phase 11 ("watch it learn") is shipped at tag `v0.11.0-phase-11`.** A new
`packages/nn/` standalone uv package (`grackle-nn`): a from-scratch, layer-granularity numpy MLP
(Linear/ReLU/Tanh, SoftmaxCE+MSE, SGD+Adam, seeded 3-class spiral demo) traced end-to-end by
grackle's own existing tracer/`ValueInspectorPanel`/heat-map/diff tooling — **zero agent/frontend/
wire-schema changes all phase** (`check-parity` a no-op every chunk). **11.1** — package + tooling
wiring — **SHIPPED PR #66, main=`92f1125`**. **11.2** — traceability contract + watch-it-learn
README walkthrough — **SHIPPED PR #67, main=`0ecf736`**. **11.H** — network-view beacons +
ship (**this PR**): folds in the "network as a network" amendment's Phase-11 deltas — two new
`metrics.py` beacons `record_architecture` (once/run, the layer stack as a token string) and
`record_layer_stats` (once/epoch, per-layer weight RMS + weight-change RMS, wired inline in
`train.fit` with zero added trace events), tests T1–T3, sizing re-derivation (34-event golden
unchanged; per-epoch tail 20→22, C 28→30, 60-epoch total ≈25,830) — plus ADR-0028 ("The NN as a
traceable subject", ADR count 27→28), `PHASE_11_SUMMARY.md`, `PROJECT_ACCEPTANCE.md` §G, version
`0.11.0`, tag (post-merge). The three beacons are identity passthroughs whose captured return reprs
are a versioned frontend parse contract (Phase 12.4 renders the latter two). **Phase 12
("grackle learns as it analyzes") queued after 11.H** — `nn/ml/`: a self-supervised
hotspot-prediction engine trained from the session-store corpus via `grackle learn`, surfaced as a
capability-gated `predicted_heat` `AnalysisRegistry` entry with **no wire-schema change**, plus a
frontend predicted-vs-actual overlay (ADRs 0029–0030, `v0.12.0`). No ADRs written yet for Phase
12 — reserved numbers only. Full plan: `~/.claude/plans/as-phase-10-is-snazzy-sedgewick.md`.

Granular per-sub-chunk implementation detail (Phase 8.5, 9.1–9.3, 10.1–10.7) has moved out
of this file — it's already fully preserved in `PHASE_8_SUMMARY.md`, `PHASE_9_SUMMARY.md`, and
`PHASE_10_SUMMARY.md` below. Read the relevant summary file when you need that level of detail
(e.g. exact mechanism notes, review-finding history, ADR cross-references for a specific chunk).

`PHASE_0_SUMMARY.md` through `PHASE_11_SUMMARY.md` at the repo root are the per-phase "what shipped + acceptance grid" reference cards. `PROJECT_ACCEPTANCE.md` at the repo root contains the whole-product definition-of-done grid.
