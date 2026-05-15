# Phase 2 summary — Python static parser via stdlib ast

**Tag**: `v0.2.0-phase-2`  
**Date**: 2026-05-15

## What shipped

### 2.A — Foundation: paths helper + graph schemas
- `paths.py`: `to_posix(p, root) -> str` — single canonical helper for POSIX-relative
  node-ID paths; guards against Windows path divergence. `ruff PTH` + path-discipline test.
- `packages/shared-types/schema/graph.schema.json`: Draft 2020-12 schema for `GraphNode`,
  `GraphEdge`, `Manifest`. Path-bearing strings carry `pattern: "^[^\\\\]*$"`.
- Hand-written `packages/shared-types/src/graph.ts` with `KNOWN_NODE_KINDS` / `KNOWN_EDGE_KINDS`
  `as const` arrays; parity-checked against codegen output.
- `adapters/base.py` tightened: replaced `type StaticGraph = dict[str, Any]` with a full
  `TypedDict` (`version`, `language`, `nodes`, `edges`, `metadata`).

### 2.B — Kind registries + content-hash cache
- `kinds.py`: `NodeKind` + `EdgeKind` frozen-slotted dataclasses; `KindRegistry[T]` with
  thread-safe register/get/known-names; module singletons `node_kinds` + `edge_kinds`
  pre-populated with 4 node kinds and 3 edge kinds.
- `cache.py`: `CacheManager` — SHA-256 content-hash sidecars under `.grackle/cache/`;
  atomic writes via `.tmp` + `Path.replace()`; `threading.Lock` for concurrent access;
  `evict()` + `flush()`.

### 2.C — AST walker: files, classes, functions, imports
- `python_parser/walker.py`: `PythonAstWalker` — file enumeration, exclude-pattern
  matching (gitignore-style fnmatch), cache integration, per-file `ast.parse`, warnings
  for syntax errors and encoding failures.
- `python_parser/visitors.py`: `GraphBuilder`, `FileVisitor`, `ClassVisitor`,
  `FunctionVisitor`, `ImportVisitor`. Closure qualname uses parent + line suffix.
  Import edges capture `type_checking`, `conditional`, `platform`, `alias`, `names`.
  40 tests (27 visitor + 13 walker).

### 2.D — AST resolver: inheritance + best-effort calls
- `python_parser/resolver.py`: `Resolution` dataclass (source ∈ local/import/method/
  unresolved), `FileScope`, `ProjectScope`, `SymbolResolver`, `resolve_graph()`.
- `python_parser/visitors.py`: `_CallVisitor` emits unresolved `call` edges from
  function/method bodies; stops at nested function/class boundaries.
- `walker.py` calls `resolve_graph()` post-walk; upgrades edges using project-wide
  import maps and exports table. 21 new tests.

### 2.E — PythonStaticParser adapter + CLI parse
- `python_parser/adapter.py`: `PythonStaticParser` implementing `StaticParserAdapter`
  (`language="python"`, `detect()`, `capabilities()`, `parse()`).
- Auto-registered via `python_parser/__init__.py` import side-effect; triggered by
  `import grackle.python_parser` added to `grackle/__init__.py`.
- `grackle parse [ROOT] [--output FILE] [-l LANG] [-e PATTERN]` CLI subcommand.
  17 new tests (adapter + CLI).

### 2.F — Tiny-app fixture + integration test
- `fixtures/tiny-app/` (~100 lines, 6 files): covers class inheritance, methods,
  closures, async functions, decorated methods, TYPE_CHECKING imports, cross-file
  calls, and a re-export package.
- `tests/test_integration_tiny_app.py`: golden-graph test asserting exact counts
  (25 nodes, 42 edges) and key edge endpoints. Regression net for the full pipeline.

### 2.G — ADRs 0005 + 0006
- `docs/adr/0005-kind-registry.md`: separate NodeKind/EdgeKind registries with display
  metadata; frozen dataclasses; design-token colors; register-time validation.
- `docs/adr/0006-python-ast-vs-tree-sitter.md`: stdlib ast for Python (zero deps,
  CPython-exact); Tree-sitter deferred to Phase 4 for TS/Go/Rust adapters.

## Acceptance criteria — all pass

| Check | Result |
|---|---|
| `grackle languages` | ✅ `supported languages: ['python']` |
| `grackle parse fixtures/tiny-app/` | ✅ 25 nodes, 42 edges, exit 0 |
| Parse tiny-app cache-warm (second call) | ✅ identical graph, cache hit |
| `grackle parse fixtures/tiny-app/ --output /tmp/g.json` | ✅ file written, summary on stderr |
| `pytest` full suite | ✅ 191 passed |
| `mypy --strict src tests` | ✅ no issues in 34 source files |
| `pnpm typecheck` | ✅ `tsc -b` clean |
| `pnpm --filter @grackle/frontend test --run` | ✅ 30 frontend tests pass |
| POSIX path discipline (node IDs) | ✅ all IDs use `/`, cross-platform stable |
| Pre-push lefthook (mypy + pytest + vitest + tsc) | ✅ all green |

## What's next (Phase 3)

Phase 3: frontend renders the graph produced by `grackle parse`. Key work:
- Wire `grackle serve` to call the Python static parser on startup and push
  `StaticGraph` JSON over the WebSocket.
- Sigma.js renderer in React reads the `StaticGraph` and lays out nodes/edges.
- Live overlay: `sys.monitoring` runtime adapter emits trace events over the
  same WebSocket; frontend highlights active nodes in real time.
