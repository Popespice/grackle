# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## What this is

grackle is a local-first live code visualizer for Python: static graph from `ast`, runtime overlay via `sys.monitoring` (Python 3.12+) over a `127.0.0.1` WebSocket, React + Sigma.js frontend.

Status: **active solo development, contributions closed** (see `CONTRIBUTING.md`). Treat external PRs and issues as out of scope.

## Repo shape

pnpm workspace monorepo with three packages plus shared schema:

- `packages/agent/` — Python WebSocket server + adapters (`uv`-managed, hatchling build, `grackle` CLI entry point)
- `packages/frontend/` — React 19 + Vite + Sigma.js + Zustand
- `packages/shared-types/` — JSON Schema is the **single source of truth**; codegen emits TS interfaces *and* Python TypedDicts

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
```

Auto-fix on dirty repos: `pnpm format` (biome write) and `uv run ruff format` in the agent.

## Architecture seams (read these to be productive)

- **`docs/adr/`** — 24 ADRs: monorepo structure (0001), WebSocket transport (0002), adapter design (0003), open-string extension surface (0004), kind registry (0005), Python ast vs Tree-sitter (0006), panel/slot system (0007), analysis registry (0008), Tree-sitter integration (0009), Rust adapter (0010), cycle detection (0011), cross-language edges (0012), runtime trace event schema (0013), trace transport (0014), runtime overlay UI (0015), real-time trace streaming (0016), server-side trace seek (0017), server-side aggregation engine (0018), call-tree + flame graph (0019), trace persistence + session store (0020), differential analysis (0021), polyglot runtime via V8 Inspector (0022), Go runtime coverage (0023), Rust runtime coverage (0024). When designing new code, check whether an ADR already constrains the decision.
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

**Phase 9 (in progress) — native runtime adapters (Go + Rust) + debt paydown.** Goal: give Go and Rust real `RuntimeAdapter`s that emit the existing `TraceEvent` schema through the unchanged Phase 6–8 pipeline — **no wire-schema change all phase**. Chunks (one squash-merged PR each, stop for review after each): **9.0** — ship Phase 8 (PR #39, `50a00b4`, tag `v0.8.0-phase-8`); **9.1** — Go runtime adapter (`go_runtime/`, ADR-0023) via `go build -cover` → `go tool covdata textfmt` → count-carrying `call` events, plus `metadata.count` weighting in `python_runtime/aggregates.py` + `diff.py` — **SHIPPED PR #40, main=`8f79fbb`**; **9.2** — Rust runtime adapter (`rust_runtime/`, ADR-0024) via `RUSTFLAGS=-Cinstrument-coverage` → `llvm-cov export` — **SHIPPED PR #43, main=`6a89192`**; **9.3** — small debts (live-stream recording sink, diff-baseline `sessionStorage` persistence) — **SHIPPED PR #45, main=`c09736e`**; **9.H** — ship (v0.9.0, ADR count stays 24) — **next**. Go/Rust ship `trace()` only — exact-count coarse events (`frame_depth:0`, `metadata.count`), per the ADR-0022 asymmetric-fidelity precedent; `trace_streaming()` raises clean typed errors. **Runtime-adapter matrix today:** Python (`sys.monitoring`), TypeScript/Node (V8 Inspector), Go (coverage instrumentation), and Rust (LLVM coverage instrumentation) all register a `RuntimeAdapter`. Full plan: `~/.claude/plans/i-want-to-get-glistening-eich.md`. North star (Phase 10+): point grackle at any filesystem and watch the graph grow live as the system is built, with an explanation layer for how/why files connect and fire (value capture → time-travel debugger).

**Phase 8.5 shipped at main=`0537680` (PR #35).** `NodeRuntimeAdapter` (language `"typescript"`, in `packages/agent/src/grackle/node_runtime/`) drives Node over the V8 Inspector (CDP) on `127.0.0.1`, emits the same `TraceEvent` schema resolved to TS static-graph node IDs, and reuses the whole Phase 6–8 transport/UI — no wire change (`KNOWN_MESSAGE_TYPES` unchanged, `check-parity` no-op), no new deps (minimal CDP client over the existing `websockets`). **Hybrid two-channel mechanism:** `trace()` = CPU **sampling** profiler → `profile_reconstruct.reconstruct` (stack-diff over samples by V8 tree-node identity → faithful `call`/`return` with real `frame_depth` + `ts_ns`) → `--connect` replay / `-o`; `trace_streaming()` = **precise-coverage** polling → `coverage_poll` → one coarse live `call` per active function per poll (`frame_depth:0`, `metadata.live:true`, exact per-poll delta in `metadata.count`) → `--stream`. Node-ID resolution via **type-stripping** (Node ≥ 22.6). Capability-gated; `.tsx`/`.jsx` give a clean "Phase 9" error; `.pyw`/extension-less → Python. 13 post-build correctness findings fixed before merge (2 REFUTED). **Refactor pass** (`node_runtime/` drift from `python_runtime/` mirror — shared resolver base / `TraceEvent` factory / dead CDP-listener code / adapter-declared extensions) **landed in 8.6** (PR #36). Full plan in `project_phase_8_progress.md` + ADR-0022.

**Phase 9.1 shipped at main=`8f79fbb` (PR #40).** `GoRuntimeAdapter` (language `"go"`, in `packages/agent/src/grackle/go_runtime/`) drives Go programs via `go build -cover -covermode=count -coverpkg=./...` → run with `GOCOVERDIR` → `go tool covdata textfmt` → pure-Python parse (`covdata_parse.py`) → one `call` `TraceEvent` per executed function, `metadata.count` = entry-block call count, `frame_depth: 0`. Reuses the whole Phase 6–8 pipeline — **no wire-schema change** (`check-parity` no-op), no new Python deps (`go` toolchain required ≥ 1.20, capability-gated). Two correctness traps handled: (1) covdata import-path-prefixed paths stripped via `go.mod` module read + `to_posix`; (2) block start line ≠ decl line — decl-line bisect via `_resolve_by_decl_line` added to shared `RuntimeResolver` base. `TraceAggregates` now weights by `metadata.count` (default 1 → byte-identical for Python/Node). `GOWORK=off` neutralizes inherited workspaces; stdin closed; `TimeoutExpired`/`OSError` → typed `GoRuntimeError`. `trace_streaming()` raises clean typed error. ADR-0023. CI green Ubuntu + Windows.

**Phase 9.2 shipped at main=`6a89192` (PR #43).** `RustRuntimeAdapter` (language `"rust"`, in `packages/agent/src/grackle/rust_runtime/`) drives Rust programs via `RUSTFLAGS=-Cinstrument-coverage cargo build` → run with `LLVM_PROFILE_FILE` → `llvm-profdata merge` → `llvm-cov export` → pure-Python parse (`llvm_cov_parse.py`) → one `call` `TraceEvent` per executed function, `metadata.count` = summed entry count across all monomorphisations, `frame_depth: 0`. Reuses the whole Phase 6–8 pipeline — **no wire-schema change** (`check-parity` no-op), no new Python deps (Rust toolchain + `llvm-tools-preview` required, capability-gated). Key correctness traps: (1) absolute-path normalization via `to_posix` (macOS `/var`→`/private/var`, Windows short paths); (2) monomorphised generics SUM counts by resolved `node_id`; (3) sysroot binary discovery via `rustc --print sysroot` + `host:` triple; (4) macro-expansion regions (fileID>0) filtered from `start_line` computation; (5) workspace-root-as-package fallback in `_resolve_package`. `trace_streaming()` raises clean typed error. ADR-0024. CI green Ubuntu + Windows.

**Phase 9.3 shipped at main=`c09736e` (PR #45).** Two small debts, no wire-schema change. **(a) Live-stream recording sink** (`packages/agent/src/grackle/python_runtime/recording_sink.py`, ADR-0020 amendment): `serve --store PATH` now tees every inbound live `--stream` session to `<store_dir>/recordings/<session_id>.jsonl` and registers it via `save_session`, so a live run is immediately loadable from the SessionLibrary without a separate `--output` capture. `RecordingSink` writes in **binary** mode with a hand-tracked byte offset (`_last_good_offset`, advanced only on a fully successful write) — avoids a per-event `tell()` flush on the hot path and Windows `\n`→`\r\n` translation; a mid-stream write failure salvages the events already written via a single `truncate(offset)` instead of discarding the whole recording. Finalize (close+rename+`save_session`) is fully guarded so no failure mode can propagate and crash the connection's receive loop; fires on `trace_session_end`, producer disconnect, or server shutdown (the shutdown path is provably safe — verified that `websockets`' `wait_closed()` joins every handler, including the shielded finalize, before `serve()`'s own `store.close()` runs). `is_safe_session_id` allow-lists the wire-supplied `session_id` before it's used as a filename (untrusted local input); exclusive-create (`"xb"`) on the `.part` makes a same-id collision fail loudly instead of silently truncating another recorder's file. **(b) Diff-baseline `sessionStorage` persistence** (`packages/frontend/src/graph/diffBaselinePersistence.ts`, ADR-0021 amendment): DiffPanel's baseline now persists per-project via the (previously unconsumed) `graphCacheKey` content hash and restores on reload; persistence is driven only from the explicit Set/Clear handlers (queued to preserve click order), never a store subscriber, because `setGraph` unconditionally clears `diffBaseline` on every `static_graph` push. `isBaseline` rejects empty/negative-count objects so a degenerate snapshot can't round-trip into a phantom all-"hotter" diff. Two review rounds (multi-agent `/code-review`) before merge; CI green Ubuntu + Windows.

**Unplanned: PR #46 (`bda7440`) — GraphCanvas duplicate-node-ID crash fix.** Found while manually verifying 9.3 in the preview browser: any duplicate static-graph node ID (e.g. a Python `@property` getter/setter pair, or `@overload` stubs sharing a name with their implementation) threw inside `graphology`'s `addNode`, and with no error boundary anywhere in the tree the **entire app** unmounted to a blank root — not a headless-browser artifact, reproducible in any browser. Fixed at the source (`python_parser/visitors.py` now skips `@overload` stubs and dedupes nodes by ID, keeping the first definition while still merging call edges from every body) and defensively (`buildGraphology.ts` skip-and-warns on a duplicate ID, protecting every adapter, not just Python; new `ErrorBoundary.tsx` wired into `SlotContainer` isolates a single panel's crash instead of blanking the whole app). Not part of the Phase 9 chunk plan — a correctness bug that happened to surface during 9.3's manual verification.

`PHASE_0_SUMMARY.md` through `PHASE_8_SUMMARY.md` at the repo root are the per-phase "what shipped + acceptance grid" reference cards. `PROJECT_ACCEPTANCE.md` at the repo root contains the whole-product definition-of-done grid.
