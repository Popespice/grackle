# Phase 4 Summary — TypeScript + Go adapters + Analysis registry

**Tag:** `v0.4.0-phase-4`
**Shipped:** 2026-05-18

## What shipped

### 4.A — Schema-batch
- `kinds.py`: registered `interface`, `type_alias`, `enum`, `struct` node kinds + `implements` edge kind.
- `shared-types/src/graph.ts`: `KNOWN_NODE_KINDS` extended to 8; `KNOWN_EDGE_KINDS` extended to 4.
- `tokens.css`: added `--color-node-{interface,type_alias,enum,struct}` + `--color-edge-implements`.
- `GraphLegend.test.tsx`: asserts 8 node-kind chips + 4 edge-kind rows.

### 4.B — Tree-sitter chassis
- `tree_sitter_runtime.py`: lazy singleton `get_parser(language)` — thread-safe, graceful missing-grammar fallback.
- `tree_sitter_walker.py`: abstract `TreeSitterWalker` (file enum → cache → parse → `visit_tree` → aggregate → `_resolve`).

### 4.C — TypeScript adapter
- Full `typescript_parser/` package with walker, visitors, resolver.
- Node kinds: `file`, `class`, `interface`, `function`, `method`, `type_alias`, `enum`.
- Edge kinds: `import`, `inherit` (extends), `implements`, unresolved → resolved `call`.
- `fixtures/tiny-ts-app/`: 6-file exercise fixture.

### 4.D — Go adapter
- Full `go_parser/` package with walker, visitors, resolver.
- Node kinds: `file`, `struct`, `interface`, `function`, `method`, `type_alias`.
- Edge kinds: `import`, `inherit` (struct embedding), `implements` (method-set detection), `call`.
- `fixtures/tiny-go-app/`: 5-file exercise fixture.
- `go_parser/resolver.py`: reads `go.mod`, builds per-package scope, resolves `pkg.Func` calls cross-file, detects implements via method-set comparison.

### 4.E — Polyglot detection + `parse_all`
- `AdapterRegistry.parse_all(root, options)`: detects all languages, merges node/edge lists, sets `graph.language = "python+typescript"` etc., stores per-language counts in `metadata.languages`.
- `cli.py`: `grackle parse` calls `parse_all` when `--language` is omitted and >1 language detected.
- `server.py`: `_push_static_graph` calls `parse_all` in the polyglot case.
- `fixtures/tiny-polyglot/`: 2 Python + 2 TypeScript files for union test.

### 4.F — Frontend AnalysisRegistry + hub-score
- `graph/analysis/registry.ts`: `AnalysisRegistry` with WeakMap-based per-graph-reference caching.
- `graph/analysis/cacheKey.ts`: async `graphCacheKey(graph)` via SHA-256 over canonical JSON.
- `graph/analysis/hubScore.ts`: `hubScore(graph): HubEntry[]` — score = in-degree − out-degree.
- `graph/analysis/index.ts`: registers 4 analyses; exports `useAnalysis<T>(id)` hook.
- `StatsPanel.tsx`: refactored to use `useAnalysis`; new Hub section showing top-3.

### 4.G — ADRs
- `docs/adr/0009-tree-sitter-integration.md`: Python bindings decision, grammar pinning, lazy singleton, cross-OS matrix, re-export cap, Go implements limits.
- `docs/adr/0008-analysis-registry.md`: amended Status to "accepted, implemented in Phase 4"; documents WeakMap cache + SHA-256 cacheKey.
- `docs/adr/README.md`: ADR-0009 entry added; ADR-0008 status updated.

### 4.H — Version + tag
- `packages/agent/pyproject.toml`: `version = "0.4.0"`.
- `packages/frontend/package.json`: `"version": "0.4.0"`.

## Acceptance grid

| # | Criterion | Status |
|---|---|---|
| 1 | `grackle languages` → `['go', 'python', 'typescript']` | ✓ |
| 2 | `grackle parse fixtures/tiny-ts-app` → ≥25 nodes incl. interface/type_alias/enum + resolved cross-file call edges | ✓ (shipped 4.C) |
| 3 | `grackle parse fixtures/tiny-go-app` → ≥15 nodes incl. struct/interface/type_alias + cross-file call edges | ✓ |
| 4 | `grackle parse .` → polyglot graph; `metadata.languages` populated | ✓ |
| 5 | `pnpm dev` against polyglot root → both subgraphs visible; legend shows 8 node kinds + 4 edge kinds; `implements` styled distinctly from `inherit` | ✓ (tokens + legend shipped 4.A) |
| 6 | Hub-score top-3 visible in StatsPanel; updates when graph changes | ✓ |
| 7 | AnalysisRegistry cache hits on identical graph reference (verified via test) | ✓ |
| 8 | Stress-2k parse <20 s unchanged | ✓ (no changes to Python parser or walker loop) |
| 9 | POSIX IDs verified via integration tests for TS and Go fixtures | ✓ |
| 10 | All linters + typecheckers + tests green | ✓ |

## Known limitations (Phase 5+)

- Go `implements` detection is best-effort: only considers methods declared directly on the struct, not those promoted from embedded types.
- Re-export chasing (TS barrel files) is not followed; unresolved call edges remain with `resolved: false`.
- Cross-language edges (Python → TypeScript call resolution) are deferred to Phase 5.
- Rust adapter deferred to Phase 5.
