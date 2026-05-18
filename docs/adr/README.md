# Architecture Decision Records

This directory uses the [Nygard format](https://cognitect.com/blog/2011/11/15/documenting-architecture-decisions).

## Format

Each ADR is a numbered Markdown file:

```
NNNN-short-title.md
```

### Sections

- **Title** — short noun phrase
- **Status** — `proposed` | `accepted` | `superseded by ADR-XXXX`
- **Context** — the situation that called for a decision
- **Decision** — what we decided and why
- **Consequences** — trade-offs, follow-on work, open questions

## Index

| # | Title | Status |
|---|---|---|
| [0001](0001-monorepo-structure.md) | Monorepo structure & package boundaries | accepted |
| [0002](0002-trace-transport.md) | Trace transport: WebSocket vs alternatives | accepted |
| [0003](0003-adapter-design.md) | Adapter design: Protocols, capability flags, registry | accepted |
| [0004](0004-extension-surface.md) | Extension surface: open strings, registries, rule of three | accepted |
| [0005](0005-kind-registry.md) | Kind registry: separate node/edge registries with display metadata | accepted |
| [0006](0006-python-ast-vs-tree-sitter.md) | Python parser: stdlib ast vs Tree-sitter | accepted |
| [0007](0007-panel-slot-system.md) | Panel/slot system: registry-driven UI layout | accepted |
| [0008](0008-analysis-registry.md) | Analysis registry: graph analyses, scheduling, caching | accepted, implemented in Phase 4 |
| [0009](0009-tree-sitter-integration.md) | Tree-sitter integration: Python bindings, grammar pinning, polyglot | accepted |
| [0010](0010-rust-adapter-integration.md) | Rust adapter: trait→interface mapping, workspace glob, ABI 15 upgrade | accepted |
| [0011](0011-cycle-detection.md) | Cycle detection: Tarjan SCC, all-edge-kinds default, frontend implementation | accepted |
