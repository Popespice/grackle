# Phase 5 Summary — Rust adapter + cycle detection + cross-language edges

**Tag:** `v0.5.0-phase-5`
**Shipped:** 2026-05-18

## What shipped

### 5.1 — Rust adapter via Tree-sitter + Cargo workspaces

- `tree-sitter-rust>=0.23,<0.24` added to `pyproject.toml`; `tree_sitter_runtime.py` registers `"rust"` grammar.
- Full `rust_parser/` package: `adapter.py`, `walker.py`, `visitors.py`, `resolver.py`, `workspace.py`, `hints.py`.
- Node kinds (reusing Phase 4 schema): `file`, `struct`, `interface` (Rust `trait`, tagged `metadata.subkind = "trait"`), `function`, `method`, `type_alias`, `enum`.
- Edge kinds (reusing Phase 4 schema): `import` (Rust `use`), `call`, `inherit` (supertrait `trait Foo: Bar`), `implements` (`impl Trait for Struct`).
- `RustWalker`: walks `.rs` files via the Tree-sitter chassis; delegates symbol resolution to `resolver.py`.
- `workspace.py`: reads root `Cargo.toml`; if a `[workspace]` table is present, enumerates `members` globs via `pathlib.Path.glob` (no Cargo CLI shell-out); each member crate gets its own `CrateScope`.
- `fixtures/tiny-rust-app/`: Cargo workspace with `crates/models` + `crates/api`, ~150 LOC, exercises supertrait, `impl Trait for Struct`, and cross-crate `use` resolution.
- Tests: `test_adapter.py`, `test_walker.py`, `test_visitors.py`, `test_resolver.py`, `test_workspace.py`, `test_integration_tiny_rust_app.py`.
- ADR-0010: trait→interface mapping, Cargo workspace glob approach, out-of-scope items (macros, generics, procedural macros).

### 5.2 — Cycle detection analysis + CyclesPanel UI

- `frontend/src/graph/analysis/cycleDetection.ts`: iterative Tarjan SCC, O(V+E), all edge kinds. Returns `CycleEntry[]` (id, nodes, size, edge_kinds) sorted by size descending. Self-loops included.
- Registered as 5th analysis `"cycles"` in `analysis/index.ts`.
- `useGraphStore.ts`: `highlightedNodeIds: Set<string> | null` Zustand state + `setHighlightedNodes` action; `setGraph` resets to null.
- `GraphCanvas.tsx`: highlighted nodes use `--color-highlight-cycle`; others dim.
- `CyclesPanel.tsx`: right-sidebar panel at order 30; lists SCCs with size + first 3 names; click toggles highlight in renderer.
- `StatsPanel.tsx`: added "Cycles: N" line.
- `tokens.css`: added `--color-highlight-cycle: oklch(72% 0.2 40)`.
- Tests: `cycleDetection.test.ts`, `CyclesPanel.test.tsx`, extended `index.test.ts` + `StatsPanel.test.tsx`.
- ADR-0011: Tarjan vs Kosaraju rationale, all-edge-kinds default, frontend implementation, cycle ID hash scheme.

### 5.3 — Cross-language edges: HTTP routes + subprocess refs

- New edge kinds: `cross_language_call` ("HTTP Call", dashed) + `cross_language_spawn` ("Subprocess", dotted).
- `tokens.css`: `--color-edge-cross-lang-call: #fb923c`, `--color-edge-cross-lang-spawn: #38bdf8`.
- `cross_language.py`: `normalize_http_path` (strip/lower/trailing-slash/param-collapse) + `resolve_cross_language_edges` (≥2-segment HTTP path filter, `argv[0]` suffix match).
- Per-language hint modules: `python_parser/hints.py`, `typescript_parser/hints.py`, `go_parser/hints.py`, `rust_parser/hints.py` — regex-based extraction of HTTP client, HTTP server, subprocess patterns.
- `TreeSitterWalker`: `hints_for_file` hook (default `[]`); TS/Go/Rust walkers override to call `extract_hints`. `PythonAstWalker` calls `extract_hints` inline. Hints land in `graph.metadata.cross_language_hints`.
- `registry.py` `parse_all`: gathers hints from all per-language graphs, calls `resolve_cross_language_edges`, appends resulting edges to the merged graph.
- `fixtures/tiny-polyglot/`: extended with `python/client.py` (requests + subprocess), `typescript/server.ts` (Express-style), `scripts/build.ts` (subprocess target).
- `StatsPanel.tsx`: "Cross-language: N (M HTTP, K subprocess)" when cross-language edges present.
- `GraphLegend.tsx`: 6 edge-kind rows (was 4).
- Tests: `test_cross_language.py`, per-adapter `test_hints.py` in each parser test dir, extended `test_registry.py`.
- ADR-0012: heuristic vs symbol-table, framework allow-list, normalisation rules, known limitations.

### 5.H — Version + tag

- `packages/agent/pyproject.toml`: `version = "0.5.0"`.
- `packages/frontend/package.json`: `"version": "0.5.0"`.
- Tag: `v0.5.0-phase-5`.

## Acceptance grid

| # | Criterion | Status |
|---|---|---|
| 1 | `grackle languages` → `['go', 'python', 'rust', 'typescript']` | ✓ |
| 2 | `grackle parse fixtures/tiny-rust-app` → ≥20 nodes, ≥1 cross-crate `call`, ≥1 `implements`, ≥1 trait `inherit` | ✓ |
| 3 | Rust workspace: `use models::User` resolves to `crates/models/src/lib.rs::User` | ✓ |
| 4 | Cycle panel: classic SCC cases surfaced; click highlights members | ✓ |
| 5 | `pnpm dev` + tiny-polyglot → ≥1 `cross_language_call` (Python→TS HTTP) + ≥1 `cross_language_spawn` (Python→TS file) | ✓ |
| 6 | Stats panel shows cycle count + cross-language counts (HTTP + subprocess) | ✓ |
| 7 | All linters + typecheckers + tests green | ✓ |

## Known limitations

- Subprocess `argv[0]` only: `subprocess.run(['node', 'build.ts'])` resolves to `node` (no file match). Use `subprocess.run(['./build.ts'])` for direct file reference.
- HTTP path matching is literal-string only: dynamic URL construction via f-strings or template literals is not detected.
- Single-segment HTTP paths (`/`, `/health`) are intentionally suppressed (≥2 segments required).
- Rust macros, `derive`, procedural macros, and generic monomorphization are out of scope.
- Re-export chaining (TS barrel files, Rust `pub use`) is a known resolver limit from Phase 4.

## Phase 6 candidates

- Runtime overlay via `sys.monitoring` (the original product story — static foundation now complete).
- Generics-aware Rust resolver (track monomorphisation).
- gRPC/protobuf as a cross-language edge surface.
- WebSocket/EventSource cross-language edge kind.
- Agent-side cycle detection if frontend profiling shows it hot.
- Re-export chasing (TS barrel files, Rust `pub use` chains).
