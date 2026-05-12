# Phase 0 summary — scaffold + WS hello-world + visual baseline

**Tag**: `v0.0.0-phase-0`  
**Date**: 2026-05-12

## What shipped

### Monorepo scaffold (0.A + 0.B)
- pnpm 11 workspace with three packages: `agent`, `frontend`, `shared-types`
- Biome 2.4 (lint + format), lefthook hooks (pre-commit / pre-push / commit-msg),
  commitlint with `agent|frontend|shared-types|docs|ci|tooling|release` scope enum
- `.editorconfig`, `.gitattributes` (LF, lockfiles binary-merge), `.gitignore`
  (includes Windows `Thumbs.db`, `Desktop.ini`, `$RECYCLE.BIN`)

### Type-sharing pipeline (0.C)
- `packages/shared-types/schema/messages.schema.json` — Draft 2020-12 source of truth
- Codegen: `json-schema-to-typescript` → TS, `datamodel-code-generator` via `uvx` → Python
- `scripts/verify-parity.mjs` — Node script (cross-platform) diffs regen vs committed outputs
- `tools/check-parity.mjs` — root-level wrapper called by hooks and CI
- Pre-commit hook re-runs codegen on schema changes; CI fails on drift

### Python agent (0.D)
- `grackle serve --host 127.0.0.1 --port 7878` — asyncio WebSocket server
- `protocol.parse_envelope` + `make_pong` — JSON Schema–validated envelope round-trip
- `configure_logging` — pretty on TTY, JSON otherwise, `GRACKLE_LOG_FORMAT` override
- 9 pytest tests (ping/pong, malformed input, abnormal disconnect)

### Frontend + visual baseline (0.E)
- React 19 + Vite 6 + Vitest 3 + Tailwind 4
- OKLCH design tokens (`tokens.css`), element resets (`base.css`),
  `prefers-reduced-motion` override (`theme.css`)
- `useTheme` — Zustand store, localStorage persistence, OS-preference fallback
- `useGrackleClient` — Zustand WS store with `connect/disconnect/ping/lastPong`
- `ConnectionBadge` — live status dot + label, 2 s pulse animation when connected
- `ThemeToggle` — accessible icon-only button, `aria-pressed`, persists choice
- 14 Vitest tests (WS client mock, theme hook)

### CI + docs (0.F)
- `ci.yml`: PR gate — ubuntu + windows matrix, `fail-fast: false`
- `ci-matrix.yml`: push-main adds macOS
- `dependabot.yml`: weekly bumps for npm, pip, actions
- `docs/adr/0001-monorepo-structure.md`, `0002-trace-transport.md`
- `docs/design/overview.md` (Mermaid architecture), `docs/design/visual-baseline.md`
- `docs/cross-platform.md` contributor cheatsheet

## Acceptance criteria — all pass

| Check | Result |
|---|---|
| Biome CI | ✅ no errors |
| TypeScript `tsc -b` | ✅ clean |
| Codegen parity | ✅ up to date |
| Frontend tests (14) | ✅ pass |
| Python ruff + mypy --strict | ✅ clean |
| Python pytest (9) | ✅ pass |
| E2E ping/pong roundtrip | ✅ pong `id` echoed, `type == "pong"` |
| Path normalization spot-check | ✅ Windows and POSIX paths yield identical IDs |

## What's next — Phase 1

Two adapter Protocols (`StaticParserAdapter`, `RuntimeAdapter`), a central
`AdapterRegistry`, no-op stubs, ADRs 0003 and 0004.
