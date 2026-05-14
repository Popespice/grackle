# Schemas

These JSON Schema (Draft 2020-12) files are the **single source of truth** for all wire types. TypeScript and Python types are both generated from them.

## Codegen-only manifests

These schemas are **codegen manifests, not runtime validators**. The root schema object in each file has no top-level `type` or `$ref`, so AJV would accept arbitrary JSON if you compiled it directly. This is a deliberate limitation of the `json-schema-to-typescript` v14 toolchain (top-level `$ref` + `$defs` breaks its ref resolver).

Runtime validation uses hand-rolled schemas (e.g. `_ENVELOPE_SCHEMA` in `packages/agent/src/grackle/protocol.py`). If Phase 3 or later needs to validate `graph.json` payloads on the wire, add a hand-rolled AJV schema rather than compiling `graph.schema.json` directly.

## Adding a new type

1. Add the definition to the relevant `*.schema.json` under `$defs`.
2. Run `pnpm codegen` from the repo root to regenerate TS and Python outputs.
3. Run `pnpm check-parity` to confirm generated files are in sync.
4. Commit the schema change — generated files are gitignored and rebuilt on demand.

## Conventions

- Use `type: "string"` (not `enum`) for discriminator fields — types are open strings per ADR-004.
- Describe every property. The description is preserved in generated code.
- Schema IDs match the filename without the `.schema.json` suffix.
- Use `$defs` (Draft 2020-12) not `definitions` (Draft 07).
