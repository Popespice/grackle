# Demo branch — `demo/end-product-preview`

**2026-05-18 forward-merge**: Phase 4 (`v0.4.0-phase-4`). Forward-merged main's Go static parser (Tree-sitter), polyglot detection (`parse_all`), TypeScript adapter, and analysis registry (hub-score, ADR-0009). Demo's `_parse` method upgraded from `PythonStaticParser` to `registry.parse_all()` — viewers can now switch between Python (`tiny`), Go (`go`), and polyglot (`poly`) fixtures. Three default fixtures ship: `tiny=fixtures/tiny-app`, `go=fixtures/tiny-go-app`, `poly=fixtures/tiny-polyglot`. Merge was zero-conflict (`--no-ff`; SHA `5931afa`).

**2026-05-16 rebase**: Phase 3 backport (`v0.3.0-phase-3`). Promoted to `main`: WS `static_graph` + `read_source` protocol, Sigma renderer + FA2 worker, panel/slot chassis, search/filter sidebar, Shiki source viewer, stats panel, stress-2k fixture, ADRs 0007 + 0008. Demo branch was **reset to main** and demo-only value re-layered (see "What's mocked" table below). All of `packages/frontend/src/graph/` and `packages/frontend/src/panels/` now ships from `main`; only `main.tsx` retains the `DemoErrorBoundary` wrap on the demo side.

**2026-05-15 rebase**: Phase 2 backport (`v0.2.0-phase-2`). `_DemoServer` swapped from hand-authored JSON to `PythonStaticParser().parse(root, ParseOptions())`; `fixtures/demo-graph/` deleted; `--fixture-root NAME=PATH` flag replaced `--fixture-dir`.

A long-lived branch that visually previews the v1 end-state on top of whatever
`main` currently ships. As phases land on `main`, the demo's mocks shrink and
the real implementations take their place. The branch is **never** merged into
`main`; it's a sibling that mirrors the current production code and adds the
forward-looking demo layer on top.

## Run it

```bash
git checkout demo/end-product-preview

# Terminal 1 — demo agent (parses fixtures/tiny-app/, optional pulse loop)
uv run --project packages/agent grackle demo

# Terminal 2 — frontend (same renderer as main)
pnpm --filter @grackle/frontend dev

# Open http://localhost:5173
```

The agent parses each fixture root via `registry.parse_all()` on the first
client connect and caches the result. Python, Go, and polyglot fixtures all
work out of the box. The frontend uses `main`'s panel/slot chassis: the static
graph renders in `GraphCanvas`, search/filter works, clicking nodes opens the
Shiki source viewer. When `--live` is set (default), the agent loops random
`pulse` envelopes every 1.5 s; the frontend ignores them silently for now —
Phase 6/7 will add a real overlay.

CLI flags:

```text
grackle demo
  --host TEXT                 Bind address (default 127.0.0.1)
  --port INTEGER              WebSocket port (default 7878)
  --fixture-root NAME=PATH    Named project root (Python/Go/polyglot). Repeatable.
                              (defaults: tiny/go/poly — see below)
  --default TEXT              Fixture name pushed on connect (default: tiny)
  --live / --no-live          Push random pulses every 1.5 s (default --live)
```

Default fixtures (used when no `--fixture-root` flags are passed):

```bash
uv run --project packages/agent grackle demo
# equivalent to:
uv run --project packages/agent grackle demo \
  --fixture-root tiny=fixtures/tiny-app \
  --fixture-root go=fixtures/tiny-go-app \
  --fixture-root poly=fixtures/tiny-polyglot
```

## What's mocked (the demo surface)

These are the pieces that should be **removed or swapped** as the corresponding
phase lands on `main`.

| Mock | Lives at | Replaced by | Phase |
|---|---|---|---|
| ~~Hand-authored graph~~ | ~~`fixtures/demo-graph/graph.json`~~ | ✅ Replaced by `PythonStaticParser` at v0.2.0 rebase (2026-05-15) | 2 |
| ~~`grackle demo` JSON fixture push~~ | ~~`packages/agent/src/grackle/{demo.py,cli.py}`~~ | ✅ Replaced by `PythonStaticParser().parse(root, ParseOptions())` at v0.2.0 rebase (2026-05-15) | 2 |
| ~~Frontend `GraphView` renderer~~ | ~~`packages/frontend/src/graph/GraphView.tsx`~~ | ✅ Replaced by `main`'s `GraphCanvas` + panel chassis at v0.3.0 rebase (2026-05-16); demo's `GraphView`/`FixtureSwitcher`/`PulseRateControl` deleted | 3 |
| Live-mode random pulse loop | `_DemoServer._pulse_loop` (agent) | Real `sys.monitoring` runtime tracer pushing `call` / `return` events | 6 + 7 |
| Pulse overlay on the canvas | _not implemented in demo yet_ | Will arrive with the real runtime overlay; frontend currently ignores `pulse` envelopes | 6 + 7 |
| `DemoErrorBoundary` in `main.tsx` | `packages/frontend/src/main.tsx` | Promoted to `main` as a real boundary, or stays demo-only — TBD | n/a |

Anything **not** in this table is already real production code shared with
`main` (the WebSocket transport, the static-graph push, the panel/slot system,
the Shiki source viewer, the adapter registry, the design tokens, etc.).

## Keeping the demo current as phases ship

After each phase merges to `main` and gets a tag (`v0.X.0-phase-N`), refresh
the demo. If the rebase becomes a conflict thicket (as it did for Phase 3 once
`main` adopted the panel chassis), it's permitted to **reset to main** and
re-layer the small set of demo-only files instead:

```bash
git checkout demo/end-product-preview
git fetch origin
git reset --hard origin/main    # only when rebase is intractable
# Re-apply: demo.py, demo subcommand in cli.py, demo schema types,
# DemoErrorBoundary in main.tsx, this DEMO_BRANCH.md
git push --force-with-lease origin demo/end-product-preview
```

**When phase 6 + 7 ship**: drop the live-pulse loop in `_DemoServer`. Real
runtime tracing now fills that role.

**Eventually**: when every mock from the table above has been replaced, the
branch contains nothing not already in `main`. At that point: delete the
branch with a small ceremony.

## What CI does (and doesn't)

The project's CI workflows (`ci.yml`, `ci-matrix.yml`) trigger on `main` pushes
and on pull requests. **Neither fires on pushes to `demo/end-product-preview`.**
That's deliberate — the demo isn't production code and we don't want to burn
CI minutes on it. If the demo breaks during a rebase, you'll find out by
running it locally.

## Why a branch, not a `main` feature

The fixture, the live-pulse loop, and the entire `grackle demo` CLI surface
are scaffolding — code that exists solely to show what the product will look
like before the real thing is built. Putting any of that on `main` adds
production weight (tests, ADRs, mypy strictness, CI cycles) for code we're
going to delete in a few months. The branch model keeps the demo in a place
where the bar is "does it render plausibly?" rather than "does it pass
mypy --strict?"

The renderer code now lives on `main`. The demo branch just adds the pulse-
loop preview on top.
