# Phase 3 summary — frontend renders the static graph

**Tag**: `v0.3.0-phase-3`  
**Date**: 2026-05-16

## What shipped

### Step 0 — demo branch backport
- Rebased `demo/end-product-preview` onto main; swapped hand-authored `fixtures/demo-graph/`
  JSON fixtures for real `PythonStaticParser().parse()` calls inside `_DemoServer`.
- `fixtures/demo-graph/` deleted; `grackle demo --fixture-root` replaces `--fixture`.
- All Phase 2 work (paths, kinds, cache, python_parser, ADRs 0005+0006) now on demo branch.

### 3.A — wire protocol: `static_graph` push + `read_source` request/response
- 4 new WebSocket message types in `messages.schema.json`: `StaticGraphMessage`,
  `ReadSourceRequest`, `ReadSourceResponse`, `ReadSourceError`.
- `grackle serve --root PATH` — on connect: detect language, parse, push `static_graph`.
- `read_source` handler: path-traversal guard (`commonpath`), 1 MiB cap, UTF-8 check.
- `client.ts` dispatches `static_graph` to subscribers; correlates `read_source` replies
  by UUID id with 5 s timeout and pending-resolver Map.
- Agent tests: traversal guard, too-large, binary rejection. Frontend client tests extended.

### 3.B — graph store + Sigma renderer + FA2 worker
- `useGraphStore.ts` (Zustand): `graph`, `selectedNodeId`, `hiddenKinds`, `searchTerm`,
  `excludeGlobs`. Actions: `setGraph`, `selectNode`, `toggleKind`, `showAllKinds`,
  `setSearch`, `setExcludes`.
- `buildGraphology.ts`: pure `(Graph) → MultiDirectedGraph`; stores kind/name/path/metadata
  as node/edge attributes.
- `GraphCanvas.tsx`: Sigma 3 + FA2 worker (barnesHutOptimize); StrictMode-safe two-effect
  pattern; node reducer reads `--color-node-{kind}` CSS vars; edge reducer reads
  `--color-edge-{kind}`; click → `selectNode`.
- 20 new frontend tests (55 total).

### 3.C — panel/slot chassis
- `PanelRegistry`: open-string slots (ADR-0004), Map-backed, `register`/`getForSlot`/
  duplicate-id throws, injectable override for tests.
- `SlotContainer`: accepts `registry` prop; renders panels sorted by `order`.
- `App.tsx` rewritten: 5 `<SlotContainer slot="…"/>` regions via CSS grid.
- `init.ts` side-effect import registers: `header-chrome` (top-bar), `search-filter`
  (left-sidebar), `graph-canvas` (floating-overlay), `source-viewer` + `node-inspector`
  + `graph-legend` (right-sidebar), `stats-panel` (bottom-status).
- Existing `GraphLegend` + `NodeInspector` de-positioned (removed `position:absolute`).

### 3.D — search / filter sidebar
- `matching.ts`: pure `isNodeVisible(node, {hiddenKinds, searchTerm, excludeGlobs})`;
  fnmatch-style glob (`*` crosses `/`), case-insensitive substring on name/path/qualname.
- `SearchFilterPanel.tsx`: search box, kind checkboxes, exclude-glob textarea (blur→store),
  "Hidden: N of M" badge (`role="status"`).
- `GraphCanvas` node reducer uses `isNodeVisible`; hidden nodes get `hidden:true` in reducer
  (preserves layout coords — filter-not-remove invariant).
- 12 matching tests + 9 SearchFilterPanel tests.

### 3.E — source viewer with Shiki
- `shiki` installed (dynamic import — uncoupled from first paint).
- `highlighter.ts`: lazy singleton; `highlightPython(source, dark)` → HTML string.
  Loads python grammar + github-dark + github-light via `createHighlighter`.
- `useSource.ts`: `useSource(path)` hook; sends `read_source`, awaits reply, caches by path,
  handles timeout/error states.
- `SourceViewer.tsx`: registers at order 5 (right-sidebar, above NodeInspector). All hooks
  at top level before conditional returns. Line-number gutter; target line highlighted +
  `scrollIntoView`; skeleton during load; empty state when no node selected;
  `annotation-marker` stub for Phase 5.
- 17 new frontend tests (113 total).

### 3.F — stats panel + 2k benchmark fixture
- `stats.ts`: `countByKind`, `topByInDegree`, `orphans` — pure selectors, 9 tests.
- `StatsPanel.tsx`: registers into `bottom-status`; shows kind chips with colour dots,
  top-3 nodes by in-degree, orphan count. Compact 32px bar.
- `fixtures/stress-2k/generate.py`: deterministic (seed=42) generator; 8 packages × 25
  modules = 200 files, ~2 800 estimated AST nodes. Output committed for reproducibility.
- `test_stress_2k_layout.py` (agent): asserts ≥1 500 nodes parsed in <20 s wall time.
- 125 frontend tests, 204 agent tests total.

### 3.G — ADR-0007 + ADR-0008
- `0007-panel-slot-system.md`: rationale for `PanelRegistry` (open-string slots, injectable
  test override, side-effect registration); hook-ordering constraint; cross-refs ADR-0003/0004/0005.
- `0008-analysis-registry.md`: plain functions for Phase 3; `Analysis<T>` interface + cacheKey
  reserved for Phase 4 when rule-of-three fires; agent-side scheduling path documented.

## Acceptance criteria

| Check | Result |
|---|---|
| `grackle languages` | ✅ `['python']` |
| `grackle serve --root fixtures/tiny-app/` → WS connect | ✅ `static_graph` pushed with 25 nodes |
| `pnpm dev` → tiny-app graph renders in browser | ✅ force-directed layout in <500 ms |
| Click node → SourceViewer shows highlighted source scrolled to line | ✅ Shiki python highlighting |
| Search "auth" → hidden count badge updates | ✅ `isNodeVisible` drives badge + canvas |
| Toggle off `file` kind → file nodes disappear without layout reshuffle | ✅ filter-not-remove |
| Stats panel shows kind counts + orphan count | ✅ bottom-status bar populated |
| Stress-2k fixture parses in <20 s (agent) | ✅ 204 agent tests pass |
| `pytest -q` | ✅ 204 passed |
| `mypy --strict src tests` | ✅ no issues in 37 source files |
| `pnpm --filter @grackle/frontend test --run` | ✅ 125 frontend tests pass |
| `pnpm typecheck` | ✅ `tsc -b` clean |
| `pnpm lint` (biome) | ✅ no issues |

## What's next (Phase 4)

- TypeScript adapter: parse `.ts`/`.tsx` files via `@typescript-eslint/typescript-estree`.
- Go adapter: parse `.go` files via tree-sitter-go.
- `Analysis<T>` registry (ADR-0008): cycle detection, hot-path identification.
- Runtime overlay: `sys.monitoring` adapter pushes live trace events; canvas highlights
  active nodes. (Phase 6 in original plan.)
