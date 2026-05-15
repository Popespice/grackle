# 0006 — Python parser: stdlib ast vs Tree-sitter

**Status**: accepted

## Context

grackle's Phase 2 goal is a working static parser for Python that produces
the graph schema defined in Phase 2.A. The parser must handle real-world
Python projects (imports, inheritance, calls, closures, decorators, async),
be zero-dependency for users, and produce POSIX-stable node IDs on all OSes.

Four candidates were considered:

| Option | Runtime dep | Version accuracy | Multi-language |
|--------|-------------|------------------|----------------|
| stdlib `ast` | none | CPython-exact | Python only |
| Tree-sitter (+ wasm) | wasm binary | grammar-version | Many |
| libcst | `libcst` package | CPython-close | Python only |
| parso | `parso` package | CPython-close | Python only |

grackle phases 3–5 are Python-only; phase 6 adds TypeScript/Go/Rust via a
separate runtime tracer design. The question is whether to pay the Tree-sitter
setup cost now for future multi-language coverage, or defer.

## Decision

**Use stdlib `ast` for the Python static parser (Phase 2). Use Tree-sitter for
TypeScript/Go/Rust adapters in Phase 4.**

Rationale:

- `ast` is shipped with every CPython install — no package install, no wasm
  download, no grammar pinning. Adding `libcst` or Tree-sitter to the
  `[project]` dependencies would bloat the install for users who never touch
  Python parsing.
- `ast` produces ASTs that match the running CPython version exactly. A
  Tree-sitter grammar that lags the current Python release would mis-parse
  structural pattern matching (`match`/`case`, Python 3.10+) or the new
  template strings, forcing a grammar bump on every language release.
- The `StaticParserAdapter` Protocol isolates all parsing behind a `parse()`
  call. Replacing `ast` with Tree-sitter for Python in Phase 4 is an
  adapter-internal change that does not touch the graph schema, node-ID scheme,
  or frontend (see [ADR-0003](0003-adapter-design.md)).
- Tree-sitter's canonical integration is via a WASM binary or a native
  extension. Both require a build step and cross-platform binary shipping that
  `pyproject.toml` does not yet handle. Adding that toolchain for Python alone
  is premature.

## Consequences

- The Python adapter (`python_parser/adapter.py`) has zero runtime deps beyond
  grackle itself. Users with only Python installed get full functionality.
- `ast` does not handle all encodings gracefully. Files with non-UTF-8 encoding
  declarations are decoded with `errors="replace"` before parsing; a `SyntaxError`
  from the garbled bytes causes a warn-and-skip, recorded in
  `Graph.metadata.parse_warnings`.
- `ast` provides no type inference or name binding. Cross-file call resolution
  is best-effort (see `resolver.py`). Phase 6 runtime tracing is the ground
  truth for call edges.
- When Phase 4 adds TypeScript/Go/Rust, each adapter will use Tree-sitter with
  a pinned grammar wasm. The adapter Protocol ensures no Phase 2 code changes.
  Cross-refs: [ADR-0003](0003-adapter-design.md) (adapter Protocol contract).
