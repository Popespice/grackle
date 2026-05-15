# 0005 — Kind registry: separate node/edge registries with display metadata

**Status**: accepted

## Context

Phase 2 introduces four node kinds (`file`, `class`, `function`, `method`) and
three edge kinds (`import`, `call`, `inherit`). The frontend needs display
metadata — colours, shapes, labels — alongside the structural type system. The
question is where this metadata lives and how it can be extended.

Two requirements pull in opposite directions:

1. **Openness** — any extension adapter can add new node or edge kinds without
   modifying grackle core (see [ADR-0004](0004-extension-surface.md)).
2. **Inspectability** — the UI must be able to enumerate all known kinds and
   their associated display data without falling back to ad-hoc `if` chains.

A single string-tagged `dict` or a flat enum both fail one of these requirements.
The rule-of-three from ADR-0004 has now triggered: 4 node kinds and 3 edge
kinds are enough variety to justify extracting a dedicated registry abstraction.

## Decision

**Two separate, generic registries** — `KindRegistry[NodeKind]` and
`KindRegistry[EdgeKind]` — each backed by a `dict[str, T]` and a
`threading.Lock`. Module singletons `node_kinds` and `edge_kinds` are
pre-populated at import time with the Phase 2 defaults.

```python
@dataclass(frozen=True, slots=True)
class NodeKind:
    name: str           # open string identifier, e.g. "file"
    display_name: str   # human-readable label
    color: str          # design-token name, e.g. "--color-node-file"
    shape: str          # "rounded-square" | "circle" | "diamond" | "dot"
    icon: str | None = None

@dataclass(frozen=True, slots=True)
class EdgeKind:
    name: str
    display_name: str
    color: str
    style: str          # "solid" | "dashed" | "double"

class KindRegistry(Generic[T]):
    def register(self, kind: T) -> None: ...   # raises ValueError on collision
    def get(self, name: str) -> T | None: ...  # case-insensitive, strip whitespace
    def known_names(self) -> list[str]: ...    # sorted, thread-safe

node_kinds: KindRegistry[NodeKind]   # file, class, function, method
edge_kinds: KindRegistry[EdgeKind]   # import, call, inherit
```

**Why separate registries**: `NodeKind` and `EdgeKind` have different metadata
fields (shape/icon vs style); a single `KindRegistry[Any]` would lose the
type-level distinction. The `Generic[T]` bound is `NodeKind | EdgeKind` in
spirit, though it is not enforced by Python's type system at the generic level —
the concrete singletons are typed precisely.

**Why frozen dataclasses**: display metadata is configuration, not behaviour.
`frozen=True, slots=True` gives cheap equality, dict-safety (no accidental
mutation), and a repr useful in error messages.

**Color as design-token name**: colors are not raw hex values but CSS custom-
property names (`--color-node-file`, `--color-edge-import`). This lets the
frontend theme override colors without touching the registry.

**Register-time validation**: `register()` rejects duplicates with `ValueError`
(same contract as `AdapterRegistry`). Empty names and names with control
characters are rejected by `_canonical_name()` — a guard copied from the
language-key validator.

## Consequences

- Extension adapters that add new node/edge kinds must register them before
  calling `parse()`. The UI falls back to a generic "unknown" appearance for
  unregistered kinds, so a forgotten registration is a display regression, not
  a crash.
- The pre-populated defaults (`file`, `class`, `function`, `method`, `import`,
  `call`, `inherit`) are registered in `kinds.py` at import time. Tests that
  need a clean registry must instantiate `KindRegistry()` directly.
- Cross-refs: [ADR-0003](0003-adapter-design.md) (adapter Protocol; registry
  pattern origin), [ADR-0004](0004-extension-surface.md) (open strings, rule
  of three).
