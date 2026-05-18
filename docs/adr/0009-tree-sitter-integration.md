# ADR-0009 — Tree-sitter integration: Python bindings, grammar pinning, and polyglot strategy

**Status:** accepted

## Context

ADR-0006 deferred Tree-sitter to Phase 4, reserving it for TypeScript and Go. The alternatives were WASM (no C compiler needed, but ~4 MB bundle overhead and slower startup in a Python process) and a native build (fast, but requires a C toolchain absent from many CI images). The Phase 4 decision is to use the Python bindings approach: `tree-sitter`, `tree-sitter-typescript`, and `tree-sitter-go` are pip-installable wheels that bundle the compiled grammar.

Three specific concerns drove the design:

1. **Cross-OS wheel availability.** Pre-built wheels exist for macOS x86_64/arm64, Linux x86_64/aarch64, and Windows x86_64. ARM Windows is not guaranteed; the runtime must degrade gracefully.
2. **Grammar version drift.** Tree-sitter grammars evolve faster than the Python binding API. Pinning to a minor-version range (`>=0.23,<0.24`) prevents silent breakage when grammars add new node types that change parse-tree shape.
3. **Re-export depth cost.** TypeScript projects routinely barrel-export through three or four layers. Building a full export table with re-export traversal is O(N·M) in the worst case. This ADR documents the cap: re-export depth is not followed; unresolved edges are emitted as-is with `resolved: false`.

## Decision

**Python bindings over WASM/native.** The `tree-sitter` PyPI package (v0.23.x) uses pre-compiled wheels distributed per grammar. This gives a one-command install, no C toolchain, and consistent behavior across platforms. The downside (binary wheel, ~1-3 MB per grammar) is acceptable for a developer tool.

**Minor-version pin.** `pyproject.toml` pins `tree-sitter>=0.23,<0.24`, `tree-sitter-typescript>=0.23,<0.24`, `tree-sitter-go>=0.23,<0.24`. When a breaking grammar change ships in 0.24, the integration tests will catch the incompatibility before the pin is bumped.

**Lazy singleton in `tree_sitter_runtime.py`.** `get_parser(language)` constructs a `Parser` at most once per process, thread-safely. If a grammar wheel fails to import, the error is logged and re-raised as `LookupError`; the adapter is simply not registered, and `grackle languages` omits it. The rest of the adapter registry remains consistent.

**Grammar-to-factory mapping.** `_GRAMMAR_FACTORIES` in `tree_sitter_runtime.py` maps language names to `(module_name, function_name)` tuples. TypeScript uses `language_typescript`; TSX uses `language_tsx`; Go uses `language` (the grammar's only export).

**Abstract `TreeSitterWalker`.** All Tree-sitter adapters extend `TreeSitterWalker`, which handles file enumeration, exclusion, content-hash caching, and result aggregation. Adapters implement only `file_extensions`, `language_name`, `visit_tree`, and optionally `_resolve`.

**Cross-file resolver shape.** Each language's `resolver.py` follows the same pattern: `build_project_scope` → `build_file_scope` per file → `SymbolResolver.resolve` per edge. Unresolved edges retain `metadata.resolved = false` rather than being dropped.

**Go implements detection.** Go's structural typing makes it impossible to determine interface satisfaction statically without full type inference. The Go resolver uses a best-effort method-set comparison: if a struct's directly-declared methods are a superset of an interface's method list, an `implements` edge is emitted. Methods promoted from embedded types are not considered. Interfaces with zero methods are skipped (every type satisfies the empty interface).

**`parse_all` for polyglot projects.** `AdapterRegistry.parse_all(root, options)` detects all languages, runs each adapter, unions the node and edge lists, and sets `graph.language` to the sorted `+`-joined detected languages. `graph.metadata.languages` records per-language node counts. Cross-language edges (e.g., a Python function that calls a TypeScript module) are deferred to Phase 5.

## Consequences

- `grackle languages` reports `['go', 'python', 'typescript']` once all three adapters load.
- `grackle parse .` against grackle's own repo produces a polyglot graph with Python and TypeScript nodes.
- A project root with only `.go` files and no `go.mod` is detected by file-extension scan; a missing `go.mod` means the Go resolver uses an empty module path, which disables cross-package call resolution (import edges are still emitted).
- Re-export chasing and cross-language edges are explicit non-goals for Phase 4; both are documented as Phase 5 work.
- Cross-refs: ADR-0003 (adapter Protocol shape), ADR-0004 (open strings), ADR-0006 (Python ast vs Tree-sitter decision history).
