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
