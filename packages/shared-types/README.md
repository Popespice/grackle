# @grackle/shared-types

Protocol types shared between the Python agent and the React frontend.

## Architecture

`schema/*.schema.json` files are the **single source of truth**. Codegen produces:

- `src/generated/*.ts` — TypeScript interfaces (consumed by the frontend and this package)
- `packages/agent/src/grackle/_generated/*.py` — Python TypedDicts (consumed by the agent)

Generated files are **gitignored**. Run `pnpm codegen` after a fresh clone or schema change.

## Public API vs generated output

`src/messages.ts` is the **canonical public API** — hand-written, strict, and what downstream code imports. The generated `src/generated/*.ts` files are sanity-check artifacts that confirm codegen ran and produced the expected shape; they are not re-exported.

json-schema-to-typescript v14 cannot faithfully express certain constraints (e.g. `maxProperties: 0` → `Record<string, never>`), so hand-written types are intentionally stricter. After a schema change, review both `src/messages.ts` and the generated output.

## Usage

```ts
import type { WsEnvelope, PingMessage, PongMessage } from "@grackle/shared-types";
```

## Adding a message type

See [`schema/README.md`](./schema/README.md).

## Why JSON Schema as SSoT?

A single schema validates at runtime, drives both language's types, and lives in a neutral format that neither ecosystem owns. See [ADR-0001](../../docs/adr/0001-monorepo-structure.md).
