# Schemas

These JSON Schema (Draft 2020-12) files are the **single source of truth** for all wire types. TypeScript and Python types are both generated from them.

## Adding a new message type

1. Add the definition to the relevant `*.schema.json` under `$defs`.
2. Run `pnpm codegen` from the repo root to regenerate TS and Python outputs.
3. Run `pnpm check-parity` to confirm generated files are in sync.
4. Commit the schema change — generated files are gitignored and rebuilt on demand.

## Conventions

- Use `type: "string"` (not `enum`) for discriminator fields — types are open strings per ADR-004.
- Describe every property. The description is preserved in generated code.
- Schema IDs match the filename without the `.schema.json` suffix.
