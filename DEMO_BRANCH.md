# Demo branch — `demo/end-product-preview`

**2026-05-24 forward-sync**: Phase 5+6 (`v0.5.0-phase-5` + `v0.6.0-phase-6`). Reset to `origin/main`
(`878c006`) and re-layered the demo surface. Main shipped: Rust adapter (Phase 5), Tarjan cycle
detection + CyclesPanel (Phase 5), HTTP-route + subprocess cross-language edges (Phase 5), and the
full Phase 6 runtime overlay — `sys.monitoring` tracer, WebSocket trace transport (file replay +
live-attach), Timeline panel, node heat-map, and runtime coverage. The demo's `_pulse_loop` (a
declared mock of the overlay) has been **replaced** by real golden-trace replay: on connect to the
Python fixture, the demo server replays `fixtures/tiny-python-app/trace.golden.jsonl` using
`protocol.make_trace_*` + `read_jsonl` + `_MAX_GAP_S`-clamped pacing — the exact same path the
production `grackle serve --trace-source` uses. Rust, Go, and polyglot fixtures render as
static-only (honest — they have no golden trace). CLI: `--live/--no-live` dropped; `--loop/--no-loop`
and `--no-pace` added; default fixture changed from `tiny` → `python`. Sync strategy: reset + re-layer
(matches Phase 3 precedent in this file); cleaner than forward-merge given the volume of main changes.

**2026-05-18 forward-merge**: Phase 4 (`v0.4.0-phase-4`). Forward-merged main's Go static parser
(Tree-sitter), polyglot detection (`parse_all`), TypeScript adapter, and analysis registry
(hub-score, ADR-0009). Demo's `_parse` method upgraded from `PythonStaticParser` to
`registry.parse_all()` — viewers could switch between Python (`tiny`), Go (`go`), and polyglot
(`poly`) fixtures. Merge was zero-conflict (`--no-ff`; SHA `5931afa`).

**2026-05-16 rebase**: Phase 3 backport (`v0.3.0-phase-3`). Promoted to `main`: WS `static_graph` +
`read_source` protocol, Sigma renderer + FA2 worker, panel/slot chassis, search/filter sidebar, Shiki
source viewer, stats panel, stress-2k fixture, ADRs 0007 + 0008. Demo branch was **reset to main** and
demo-only value re-layered. All of `packages/frontend/src/graph/` and `packages/frontend/src/panels/`
now ships from `main`; only `main.tsx` retains the `DemoErrorBoundary` wrap on the demo side.

**2026-05-15 rebase**: Phase 2 backport (`v0.2.0-phase-2`). `_DemoServer` swapped from hand-authored
JSON to `PythonStaticParser().parse(root, ParseOptions())`; `fixtures/demo-graph/` deleted;
`--fixture-root NAME=PATH` flag replaced `--fixture-dir`.

A long-lived branch that visually previews the v1 end-state on top of whatever `main` currently ships.
As phases land on `main`, the demo's mocks shrink and the real implementations take their place. The
branch is **never** merged into `main`; it's a sibling that mirrors the current production code and
adds the forward-looking demo layer on top.

## Run it

```bash
git checkout demo/end-product-preview

# Bootstrap (first time only)
pnpm install
(cd packages/agent && uv sync)
pnpm codegen

# Terminal 1 — demo agent (parses fixtures, replays golden trace for Python fixture)
uv run --project packages/agent grackle demo

# Terminal 2 — frontend (same renderer as main)
pnpm --filter @grackle/frontend dev

# Open http://localhost:5173
```

The agent parses each fixture root via `registry.parse_all()` on the first client connect and caches
the result. On connect the Python fixture (`tiny-python-app`) immediately replays its golden trace —
the Timeline panel appears, pressing Play advances the playhead, and nodes heat-map by call frequency
using real `sys.monitoring` data.  Rust, Go, and polyglot fixtures render static-only graphs.

To switch fixtures mid-session the frontend can send a `load_fixture` envelope (JSON over WS):
`{"id":"1","type":"load_fixture","payload":{"name":"rust"}}`.

CLI flags:

```text
grackle demo
  --host TEXT                 Bind address (default 127.0.0.1)
  --port INTEGER              WebSocket port (default 7878)
  --fixture-root NAME=PATH    Named project root. Repeatable.
                              (defaults: python/rust/poly/tiny/go — see below)
  --default TEXT              Fixture name pushed on connect (default: python)
  --loop / --no-loop          Repeat trace replay after it ends (default --no-loop)
  --no-pace                   Push trace events immediately, no inter-event delay
```

Default fixtures (used when no `--fixture-root` flags are passed):

```bash
uv run --project packages/agent grackle demo
# equivalent to:
uv run --project packages/agent grackle demo \
  --fixture-root python=fixtures/tiny-python-app \
  --fixture-root rust=fixtures/tiny-rust-app \
  --fixture-root poly=fixtures/tiny-polyglot \
  --fixture-root tiny=fixtures/tiny-app \
  --fixture-root go=fixtures/tiny-go-app
```

## What's mocked (the demo surface)

These are the pieces that were **replaced** as corresponding phases landed on `main`, plus anything
still forward-looking.

| Mock | Lives at | Replaced by | Phase |
|---|---|---|---|
| ~~Hand-authored graph~~ | ~~`fixtures/demo-graph/graph.json`~~ | ✅ Replaced by `PythonStaticParser` at v0.2.0 rebase (2026-05-15) | 2 |
| ~~`grackle demo` JSON fixture push~~ | ~~`packages/agent/src/grackle/{demo.py,cli.py}`~~ | ✅ Replaced by `PythonStaticParser().parse(root, ParseOptions())` at v0.2.0 rebase (2026-05-15) | 2 |
| ~~Frontend `GraphView` renderer~~ | ~~`packages/frontend/src/graph/GraphView.tsx`~~ | ✅ Replaced by `main`'s `GraphCanvas` + panel chassis at v0.3.0 rebase (2026-05-16) | 3 |
| ~~Live-mode random pulse loop~~ | ~~`_DemoServer._pulse_loop` (agent)~~ | ✅ Replaced by real golden-trace replay (`protocol.make_trace_*` + `read_jsonl`) at Phase 5+6 sync (2026-05-24) | 6 |
| ~~Pulse overlay on the canvas~~ | ~~_not implemented in demo yet_~~ | ✅ Replaced by real Phase 6.3 Timeline + heat-map overlay (from `main`) at Phase 5+6 sync (2026-05-24) | 6 |
| `DemoErrorBoundary` in `main.tsx` | `packages/frontend/src/main.tsx` | Promoted to `main` as a real boundary, or stays demo-only — TBD | n/a |

Anything **not** in this table is already real production code shared with `main` (the WebSocket
transport, the static-graph push, the panel/slot system, the Shiki source viewer, the adapter
registry, the design tokens, the `sys.monitoring` tracer, the Timeline panel, the heat-map, etc.).

## Keeping the demo current as phases ship

After each phase merges to `main` and gets a tag (`v0.X.0-phase-N`), refresh the demo branch using
the **reset + re-layer** strategy (matching Phase 3 and Phase 5+6 precedents):

```bash
git fetch origin
git checkout demo/end-product-preview
git reset --hard origin/main          # bring demo up to the new tip of main
# … re-apply demo-only files (demo.py, cli.py demo subcommand, main.tsx, DEMO_BRANCH.md) …
git push --force-with-lease origin demo/end-product-preview
```

Demo-only files to re-layer after each reset:

- `packages/agent/src/grackle/demo.py`
- the `demo` subcommand in `packages/agent/src/grackle/cli.py`
- `packages/frontend/src/main.tsx` (`DemoErrorBoundary`)
- `DEMO_BRANCH.md` (this file)

No CI runs on demo pushes — intentional.  The demo bar is "renders plausibly in a browser", not
"passes the full test matrix".
