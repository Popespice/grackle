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

- **`docs/adr/`** — five accepted ADRs cover monorepo structure (0001), trace transport (0002), adapter design / Protocols vs ABCs / path discipline (0003), and the open-string extension surface (0004). When designing new code, check whether an ADR already constrains the decision.
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

Phase 1 (adapter Protocols + `AdapterRegistry` + `grackle languages`) is shipped at tag `v0.1.0-phase-1`. Phase 2 (Python static parser via stdlib `ast`) is shipped at tag `v0.2.0-phase-2` — `paths.py`, `kinds.py`, `cache.py`, `python_parser/` (walker + visitors + resolver + adapter), `grackle parse` CLI, `fixtures/tiny-app/` golden-graph integration test, and ADRs 0005 + 0006. Phase 2 committed the load-bearing contracts (`graph.json` shape, node-ID scheme `<posix-path>:<qualname>`, kind registry pattern) that every later phase consumes.

**Phase 3 (frontend renders the static graph) is in progress.** Approved plan at `/Users/connorallen/.claude/plans/radiant-seeking-owl.md`. Chunks 3.A–3.H.

- **Step 0 (done):** `demo/end-product-preview` rebased onto main; `_DemoServer` swapped from hand-authored JSON to `PythonStaticParser().parse()`; `fixtures/demo-graph/` deleted.
- **3.A (done, commit `be10fe4`):** 4 new WebSocket message types (`static_graph`, `read_source`, `source_response`, `source_error`). `grackle serve --root PATH` pushes `static_graph` on connect; handles `read_source` with path-traversal guard + 1 MiB cap + UTF-8 check. `client.ts` dispatches `static_graph` to subscribers and correlates `read_source` replies by id.
- **3.B (done):** Zustand graph store (`useGraphStore.ts`) + pure graphology builder (`buildGraphology.ts`) + `GraphCanvas.tsx` (Sigma 3 + FA2 worker, StrictMode-safe). 20 new frontend tests (55 total).
- **3.C (done):** Panel/slot chassis — `PanelRegistry`, `SlotContainer`, `HeaderChrome`, `GraphLegendPanel`, `NodeInspectorPanel`, `panels/init.ts`; `App.tsx` replaced with 5-slot CSS grid layout. `showAllKinds` added to store. 14 new frontend tests (69 total).
- **3.D (done):** Search/filter sidebar — `matching.ts` (`isNodeVisible` pure function, fnmatch-style globs), `SearchFilterPanel.tsx` (search + kind toggles + exclude globs + hidden badge), registered into `left-sidebar`. `GraphCanvas` updated to use `isNodeVisible`. 25 new frontend tests (94 total).
- **3.E (next):** Source viewer with Shiki — lazy-load `@shikijs/core`, `useSource.ts` hook, `SourceViewer.tsx` panel into `right-sidebar`.

`PHASE_0_SUMMARY.md`, `PHASE_1_SUMMARY.md`, and `PHASE_2_SUMMARY.md` at the repo root are the per-phase "what shipped + acceptance grid" reference cards.
