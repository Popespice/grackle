# 0003 — Adapter design: Protocols, capability flags, registry

**Status**: accepted

## Context

grackle needs to support multiple languages (Python now, TypeScript / Go / Rust
in v1.5) without rearchitecting the engine each time. Two adapter kinds exist:

- **Static parser adapters** — analyse source files at rest and produce a graph.
- **Runtime adapters** — instrument a running process and emit trace events.

Each language's parsing and tracing implementation is discrete. The adapter
contract must be pluggable, inspectable (what does this adapter actually
support?), and safe to extend from outside grackle's own package tree.

## Decision

**Two `@runtime_checkable` `typing.Protocol` classes** define the contracts:

```python
class StaticParserAdapter(Protocol):
    language: str
    def detect(self, project_root: Path) -> bool: ...
    def capabilities(self) -> Capabilities: ...
    def parse(self, project_root: Path, options: ParseOptions) -> StaticGraph: ...

class RuntimeAdapter(Protocol):
    language: str
    def capabilities(self) -> Capabilities: ...
```

**`Capabilities`** is a `@dataclass(frozen=True, slots=True)` with seven boolean
flags (`files`, `classes`, `functions`, `imports`, `calls`, `runtime_tracing`,
`annotations`), all defaulting to `False`. Adapters advertise what they
actually deliver; the UI reads these flags to decide what to render.

**`AdapterRegistry`** is a thread-safe central dispatch table. Adapters
register themselves by language string; CLI and UI lookups go through the
module-level singleton `registry = AdapterRegistry()`. No caller needs to know
which registry instance to use.

**Path discipline**: all path-bearing Protocol parameters are `pathlib.Path`,
never `str`. When adapters emit node IDs (graph nodes, annotation keys), they
must normalize to POSIX-relative form (`src/foo/bar.py`, never
`src\foo\bar.py`) via `pathlib.Path.as_posix()` relative to the project root.
This ensures that a project shared between macOS, Windows, and Linux produces
identical node IDs and that cached annotations remain valid across OSes. The
`ruff PTH` rule enforces `pathlib` over `os.path` strings at lint time.

**Why Protocols, not ABCs**: structural typing means an external adapter
satisfies the contract by having the right methods — it does not need to
`import grackle` at all. An ABC would require `from grackle.adapters import
BaseAdapter` — a hard coupling between the adapter author's package and
grackle's internal module tree. With Protocols, grackle can `isinstance`-check
an adapter it never imported. The downside is that `isinstance` checks only
method presence, not signatures; explicit unit tests on return shapes compensate
(see `tests/adapters/test_noop.py`).

**Why a module singleton**: one well-known object removes the "which registry?"
question at every call site. CLI, server, and future UI code all import the
same `registry` without passing it as a parameter.

**Open language values**: the `language` attribute on adapters is an open
`str`, not a closed `Literal` or enum. See [ADR-0004](0004-extension-surface.md)
for the broader open-string convention applied to other extension-surface
fields (node-kind, edge-kind, trace event type, exporter format). The
path-discipline contract above complements the cross-platform discipline
established in [ADR-0001](0001-monorepo-structure.md).

## Consequences

- `super()`-based code reuse is unavailable across Protocol implementors.
  When the rule of three triggers (phase 2 adds Python, phase 4 adds TS/Go/Rust
  stubs), extract shared helpers into `grackle.adapters.common`.
- External adapters work without installing grackle as a runtime dependency.
  They will want grackle as a dev dependency to get type hints.
- Path normalization is a contract, not an enforcement. A misbehaving adapter
  that emits Windows-style paths on Windows will produce node IDs that differ
  from Mac/Linux runs of the same project. The `grackle.paths` helper (phase 2)
  and `PTH` lint rule mitigate this; test fixtures that assert exact node IDs
  enforce it per-adapter.
- Capability flags are opt-in false by default. An adapter that forgets to
  set `functions=True` will simply not show function nodes in the UI — a silent
  regression. Phase-2 adapter tests must assert capabilities explicitly.
- Subprocess / multiprocessing tracer design must be `spawn`-compatible from the
  start (Windows has no `fork`). Phase 6 design documents this constraint in its
  own ADR.
