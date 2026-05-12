# 0004 — Extension surface: open strings, registries, rule of three

**Status**: accepted

## Context

grackle is designed as an extensible platform (brief §6.5). The adapter
Protocol shape itself is the subject of [ADR-0003](0003-adapter-design.md);
this ADR addresses the parallel question of how *values* flowing through that
contract (and through other extension points) are typed.

Several concepts are "kinds" that users, adapter authors, and future core
development will want to extend without modifying grackle's core:

| Concept | Where it appears |
|---|---|
| Language name | `StaticParserAdapter.language`, `RuntimeAdapter.language`, `AdapterRegistry` keys |
| Node kind | Graph node shape/colour in the UI (phase 2+) |
| Edge kind | Graph edge style in the UI (phase 2+) |
| Trace event type | `TraceEvent.type` — `"call"`, `"return"`, `"exception"`, ... |
| Annotation kind | Optional tag on a note (phase 5+) |
| Panel ID | UI panel registration (phase 3+) |
| Analysis name | Community detection, centrality, orphan-finding (phase 3+) |
| Exporter format | SVG, PNG, dot, mmd, GRAPH_REPORT.md (phase 9+) |

Two contrasting approaches exist: **closed enums** (all valid values enumerated
at compile time) or **open strings** (any string is valid; known values are
documented but not constraining).

## Decision

**Use open strings everywhere a kind is plausibly extended downstream.**

- In JSON Schema: `"type": "string"` with `"examples": [...]`. Never `"enum"`.
- In TypeScript: `KNOWN_LANGUAGES` (and future `KNOWN_NODE_KINDS` etc.) are
  `as const` arrays for IDE autocomplete, with a named type alias
  (`KnownLanguage`) for callers that want to restrict their own input. Public
  API parameters accept the wider `string`, not the narrower union.
- In Python: dispatch tables keyed by string; known values documented in module
  docstrings. `KNOWN_*` constants available for callers who want them.

**Rule of three**: do not extract a shared interface, abstract base class, or
enum until the third concrete implementation exists. One implementation → inline
it. Two → notice the pattern. Three → extract the abstraction. Applying this
rule to kinds means: language "python" ships alone in phase 2; "typescript" and
"go" arrive in phase 4 — that is the moment to evaluate whether a formal
`KindRegistry` type pays its way.

**Dispatch and validation**: registries validate at registration time, not
dispatch time, in strict mode. Unknown kinds that arrive over the wire (e.g.,
from a future adapter version) are logged as warnings and skipped, not crashed
on. This preserves forward-compatibility: a newer adapter talking to an older
grackle UI degrades gracefully.

**Why not closed enums**: the most common extensibility failure mode is using a
closed enum and then having to cut a breaking release to add a third vendor.
Consider: if `StaticParserAdapter.language` were a `Literal["python",
"typescript"]` union type, every external adapter would require a grackle core
PR to be recognised. Open strings eliminate this class of breakage entirely.

**Why not discriminated unions / sealed types**: same problem — closing the
union at compile time couples downstream callers to core's release cycle.
Discriminated unions are the right tool for internal protocol messages where
exhaustive handling is required (e.g., the WebSocket envelope `type` field at
the server level). They are the wrong tool for user-extensible kind registries.

**Why not a plugin system (entry points, importlib.metadata)**: premature for
v1. Entry-point discovery is the right answer when grackle is a published
package with independent third-party adapters installing via pip. Until then,
programmatic `registry.register_*()` calls are simpler and equally capable. The
registry API is already the right shape for a future entry-point loader — it
would just call `register_*` on discovered adapters automatically.

## Consequences

- Typos in string keys fail silently until dispatch. Mitigate with `KNOWN_*`
  constants, IDE autocomplete on them, and dispatch-table assertions in tests.
- The JSON Schema parity verifier accepts new string values in `examples`
  without changes — no schema migration required to add a new known language.
- Rule of three means tolerating a little duplication before factoring.
  This is intentional: the duplication is cheap, and premature abstraction is
  expensive to undo.
- "Forward-compatible" means *reading* unknown values without crashing. It does
  not mean *producing* unknown values intentionally — adapters should document
  every string value they emit.
- This philosophy applies identically to the TypeScript side: the frontend
  renders unknown node kinds with a fallback shape rather than throwing.
