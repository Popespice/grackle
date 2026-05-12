# Phase 1 summary — adapter interfaces + registry

**Tag**: `v0.1.0-phase-1`  
**Date**: 2026-05-12

## What shipped

### Shared-types adapter schema (1.A)
- `packages/shared-types/schema/adapters.schema.json` — Draft 2020-12 definitions for
  `Capabilities`, `ParseOptions`, `StaticGraph`, `TraceEvent`
- Hand-written `src/adapters.ts` with `KNOWN_LANGUAGES as const` array and `KnownLanguage`
  type alias; open `string` fields per ADR-0004
- Codegen emits `src/generated/adapters.ts` and `packages/agent/src/grackle/_generated/adapters.py`;
  parity baseline committed; CI parity check green

### CI fix (1.A verification)
- `corepack enable pnpm` moved before `actions/setup-node@v4` in both `ci.yml` and
  `ci-matrix.yml`; resolves "Unable to locate executable file: pnpm" across all OSes.
  Applies to all future workflow edits.

### Python adapter contracts (1.B)
- `adapters/base.py`: `Capabilities` + `ParseOptions` as `@dataclass(frozen=True, slots=True)`;
  `type StaticGraph = dict[str, Any]` + `type TraceEvent = dict[str, Any]` (PEP 695);
  `@runtime_checkable` `StaticParserAdapter` and `RuntimeAdapter` Protocols with
  `language: str` class attribute. All path-bearing parameters typed as `pathlib.Path`.
- `adapters/noop.py`: `NoOpStaticParser` and `NoOpRuntimeAdapter` — validate Protocol
  shape end-to-end. Not auto-registered; test-only.
- `adapters/__init__.py`: public surface re-exports all types + singleton; `__all__` set.

### AdapterRegistry + tests (1.C)
- `adapters/registry.py`: thread-safe `AdapterRegistry` with `threading.Lock`,
  case-insensitive language keys, `ValueError` on duplicate, module singleton
  `registry = AdapterRegistry()`.
- 15 unit tests (`tests/adapters/test_noop.py` × 5, `tests/adapters/test_registry.py` × 10).
  Thread-safety verified under contention (50 concurrent distinct registrations;
  20 concurrent duplicate registrations → 1 winner, 19 `ValueError` losers).

### CLI `languages` subcommand + re-export (1.D)
- `grackle languages` prints `supported languages: []` (empty until a real adapter registers).
- Registry lazily imported in CLI to keep startup fast.
- `grackle/__init__.py` re-exports `registry` and declares `__all__`; version bumped to `0.1.0`.

### ADRs (1.E)
- `docs/adr/0003-adapter-design.md`: Protocols vs ABCs, capability-flag design,
  AdapterRegistry singleton rationale, POSIX-path discipline for cross-platform node IDs.
- `docs/adr/0004-extension-surface.md`: open-string philosophy for all user-extensible kinds,
  rule-of-three before extracting abstractions, why not enums / discriminated unions /
  entry-points yet.

## Acceptance criteria — all pass

| Check | Result |
|---|---|
| `grackle languages` | ✅ `supported languages: []` |
| Protocol round-trip (`isinstance` of NoOp against both Protocols) | ✅ pass |
| `pytest tests/adapters` (15 tests) | ✅ pass |
| `pytest` full suite (25 tests) | ✅ pass |
| `mypy --strict src` | ✅ no issues in 10 source files |
| `ruff check .` | ✅ all checks passed |
| `pnpm typecheck` | ✅ `tsc -b` clean |
| Codegen parity | ✅ all files up to date |
| Path normalization spot-check | ✅ Windows and POSIX paths yield identical IDs |
| CI matrix (Ubuntu + Windows + macOS) | ✅ green on every chunk commit |

## What's next — Phase 2

Python static parser via stdlib `ast`. Extract file nodes, classes, functions,
imports, inheritance edges, and best-effort call references. Cache by content
hash. Emit `graph.json`. Introduces `grackle.paths.to_posix()` helper (the
cross-platform discipline from ADR-0003 gets its first concrete use).
