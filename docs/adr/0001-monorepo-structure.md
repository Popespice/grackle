# 0001 — Monorepo structure & package boundaries

**Status**: accepted

## Context

grackle has three distinct concerns: a Python WebSocket server/agent, a React
frontend, and a shared protocol-type definition layer. We need a repository
structure that keeps these concerns isolated while enabling type-sharing without
drift, and that works natively on macOS, Windows, and Linux from day one.

## Decision

Use a pnpm workspace monorepo with three packages:

```
packages/
  agent/          # Python — uv-managed, hatchling build
  frontend/       # React + Vite — pnpm workspace package
  shared-types/   # JSON Schema + TS + Python codegen
```

**Tool choices and rationale:**

| Concern | Choice | Why |
|---|---|---|
| JS workspace | pnpm workspaces | Workspace protocol (`workspace:*`), fast installs, native Win/Mac/Linux |
| JS lint/format | Biome 2.x | Single binary replaces ESLint + Prettier; 10–25× faster; React hooks rules |
| Git hooks | lefthook | Go binary, ships native for all OSes; hooks invoke only cross-platform commands |
| Python env | uv | Fastest resolver; reads `.python-version`; native Win/Mac/Linux |
| Type sharing | JSON Schema → TS + Python codegen | Single source of truth; drift caught in CI |

**Why not Nx or Turborepo**: heavyweight for our build graph (three packages, two
ecosystems). The overhead exceeds the benefit at this scale.

**Why not separate repos**: the protocol-type contract between the Python agent
and the TypeScript frontend would drift. A monorepo makes drift a compile-time
error (parity check) rather than a runtime surprise.

**Cross-platform note**: every tool chosen ships native binaries or pure code for
macOS, Windows, and Linux. No tool requires WSL, Cygwin, or platform-specific
build steps. The CI matrix (PR: Linux + Windows; push-main: + macOS) enforces
this continuously.

## Consequences

- Contributors need both `pnpm` and `uv` on `PATH` before bootstrapping.
  The `prepare` script checks for `uv` and prints a link if absent.
- Adding a new package (e.g., a VS Code extension) is a one-liner in
  `pnpm-workspace.yaml`.
- The codegen pipeline (`pnpm codegen`) must be re-run after schema changes.
  The pre-commit hook and CI both enforce parity.
