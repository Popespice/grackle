# Demo branch — `demo/end-product-preview`

**2026-07-15 forward-sync (Phase 11)**: `v0.11.0-phase-11` ("watch it learn"). Reset to `origin/main`
and re-layered the demo surface — Phase 11 touched only `packages/nn/` plus docs/tooling, so every
demo-only file (`demo.py`, `cli.py`, `FixtureSwitcher.tsx`, `HeaderChrome.tsx`, `client.ts`,
`main.tsx`) was a zero-diff re-layer (confirmed via `git diff v0.10.0-phase-10 origin/main` on each
file before trusting the checkout). New fixture: `nn`, rooted at `packages/nn/src` (the from-scratch
numpy MLP), replaying a committed golden trace of a real 60-epoch training run
(`fixtures/nn-training/trace.golden.jsonl`, ~25,870 events, generated deterministically via
`pnpm nn:trace` — seed 0, converges to ~0.99 accuracy). Because the nn fixture's source root
(`packages/nn/src`) and its golden trace (`fixtures/nn-training/`) live in different places — unlike
every other fixture, which co-locates `trace.golden.jsonl` next to its source — `demo.py` gained a
small `_trace_for`/`trace_overrides` decoupling (threaded from `cli.py` through `serve_demo` and
`_DemoServer`, same pattern as `fixture_roots`) instead of forcing the trace into the production
`packages/nn/src` tree. `.gitignore` and `biome.json` were hand-reconciled (union), not blind-copied,
since Phase 11 added `packages/nn/*.jsonl` to `.gitignore` after the demo branch's last sync. Sync
strategy: reset + re-layer (matches every prior sync in this file); landed in two steps — first
Phase 10.D (stale since 2026-05-24, held pending approval) to bring the branch to v0.10.0, then this
Phase 11 sync on top.

**2026-07-09 forward-sync (Phase 10.D)**: Phases 7 through 10 (`v0.7.0-phase-7` → `v0.10.0-phase-10`).
Reset to `origin/main` and re-layered the demo surface — the branch had gone stale at the Phase 6 line
since the 2026-06-10 fixture-switcher patch (which pulled nothing new from main). Main shipped since:
real-time streaming + server-side seek (Phase 7), the whole analysis platform — flame graph, trace
aggregation + sqlite session store, differential analysis, the Node/V8 runtime adapter (Phase 8), Go
and Rust runtime adapters (Phase 9), and value capture + the time-travel value inspector + the
explanation layer (edge evidence, causal path) + watch mode (Phase 10). `demo.py` was modernized to
**delegate** rather than hand-roll: trace replay now calls
`python_runtime.file_replay.replay_trace` and parsing calls `server._build_static_graph` (so demo
graphs finally carry hub-score + cycle metadata too) — both are the exact functions production
`grackle serve` uses. New fixtures: `values` (Python, `--capture-values` golden trace — exercises the
ValueInspectorPanel including redaction), `node` (TypeScript/Node golden trace, real call depth), and
`watch` (a canned graph-diff simulation — see "What's mocked"). `go` and `rust` gained real golden
traces (previously static-only). The session library (Phase 8.3) is now real, not mocked: a real
`SessionStore` seeded from every fixture's golden trace, answering `session_list_request` /
`session_load_request` through the actual production code path. Sync strategy: reset + re-layer
(matches every prior sync in this file).

**Correctness bug found + fixed during manual verification:** the first session-library implementation
wired `session_list_request`/`session_load_request` (→ `load_stored_session`, which registers a
`SeekableSession` and sends `trace_session_start(seekable=true)` + `trace_session_end`) but never added
a `trace_seek_request` handler — so a loaded session had a `SeekableSession` registered server-side with
no way to ever actually serve it. The frontend showed a silent "0/0" (`TimelinePanel`'s
`requestTraceWindow` call timing out after 5s with no visible error) rather than a crash — confirmed via
a raw-protocol test script bypassing the browser entirely (`session_list_response` → `session_load_request`
→ `trace_session_start`/`trace_session_end` all correct, but `trace_seek_request` got no reply at all).
Fixed by porting `server.py`'s `trace_seek_request` handling (session lookup → `JsonlIndex.read_window`
→ `protocol.make_trace_window`) into `_DemoServer._handle_trace_seek`. Re-verified end-to-end after the
fix: session load → `0/7` on the timeline → "Load call stack" → full seekable prefix loads correctly.

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

The agent parses each fixture root via `server._build_static_graph()` on the first client connect and
caches the result (hub-score + cycles included — the same enrichment production `serve` does). On
connect the Python fixture (`tiny-python-app`) immediately replays its golden trace — the Timeline
panel appears, pressing Play advances the playhead, and nodes heat-map by call frequency using real
`sys.monitoring` data. `values`, `node`, `go`, `rust`, and `nn` also replay real golden traces (see
below). `poly` and the synthetic size-tier presets render static-only graphs. `watch` runs a canned
graph-diff simulation instead of a trace replay (see "What's mocked").

The `nn` fixture is the "watch it learn" story (Phase 11): select it to see the `grackle_nn` MLP's
static graph (heat map lights up `Linear.forward` as the hottest node; the flame graph shows
`fit → train_step → forward/backward/step`), then scrub the timeline or open the value inspector on
`grackle_nn/metrics.py:record_epoch` to watch loss fall and accuracy climb epoch-by-epoch straight
from captured trace values.

To switch fixtures mid-session the frontend can send a `load_fixture` envelope (JSON over WS):
`{"id":"1","type":"load_fixture","payload":{"name":"rust"}}`. The session library is real: send
`session_list_request` / `session_load_request` and the agent answers from a `SessionStore` seeded at
startup from every fixture with a golden trace.

CLI flags:

```text
grackle demo
  --host TEXT                 Bind address (default 127.0.0.1)
  --port INTEGER              WebSocket port (default 7878)
  --fixture-root NAME=PATH    Named project root, or a pre-built *.json graph. Repeatable.
                              (defaults: python/values/node/go/rust/poly/watch + size tiers — see below)
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
  --fixture-root values=fixtures/value-capture \
  --fixture-root node=fixtures/tiny-node-app \
  --fixture-root go=fixtures/tiny-go-app \
  --fixture-root rust=fixtures/tiny-rust-app \
  --fixture-root poly=fixtures/tiny-polyglot \
  --fixture-root watch=fixtures/tiny-app \
  --fixture-root tiny=fixtures/demo-graph/tiny.json \
  --fixture-root small=fixtures/demo-graph/small.json \
  --fixture-root medium=fixtures/demo-graph/medium.json \
  --fixture-root large=fixtures/demo-graph/large.json \
  --fixture-root huge=fixtures/demo-graph/huge.json \
  --fixture-root nn=packages/nn/src
```

The `nn` fixture's golden trace is *not* co-located with its source root like every other fixture —
it's registered via `demo.py`'s `_trace_for`/`trace_overrides` at
`fixtures/nn-training/trace.golden.jsonl`, since the source root (`packages/nn/src`) is shared
production code and shouldn't carry a demo-only trace file.

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
| `DemoErrorBoundary` in `main.tsx` | `packages/frontend/src/main.tsx` | Stays demo-only (resolved 2026-07-09): PR #46 landed a per-panel `ErrorBoundary` in `SlotContainer` on `main`, but it doesn't cover app-shell / registry-init crashes outside any panel — `DemoErrorBoundary` still catches those, which matters more for an unattended visitor-facing preview. | n/a |
| Watch-mode diff animation (`watch` fixture) | `_DemoServer._watch_sim_loop` (agent) | Not yet — no filesystem watcher in the demo. Periodically re-pushes a synthetically mutated graph variant through the *exact* production envelope (`protocol.make_static_graph`), so the frontend's graph-diff animation (Phase 10.7) runs identically to how it would for a real `serve --watch` file edit. Honest label: this is a canned simulation, not a real watch. | 10.6/10.7 |

Anything **not** in this table is already real production code shared with `main` (the WebSocket
transport, the static-graph push, the panel/slot system, the Shiki source viewer, the adapter
registry, the design tokens, the `sys.monitoring` tracer, the Timeline panel, the heat-map, the
session store, etc.). Notably the session library is **real** as of 2026-07-09 — a genuine
`SessionStore` seeded from the golden-trace fixtures, not a canned response. The `nn` fixture added
2026-07-15 is likewise **honest, not mocked**: a real, deterministic trace of the `grackle_nn` MLP
actually training (seed 0, 60 epochs, converges to ~0.99 accuracy) — the only demo-specific part is
that the trace is pre-recorded rather than run live in the visitor's browser.

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
- `packages/frontend/src/graph/FixtureSwitcher.tsx`
- `packages/frontend/src/panels/HeaderChrome.tsx` (`<FixtureSwitcher />` wiring)
- `packages/frontend/src/ws/client.ts` (`agent_hello` handling + `loadFixture` — hand-adapt, this
  file evolves fastest on `main`; verify against the current `pendingRequest`/message-switch shape
  rather than diffing blind)
- `fixtures/demo-graph/` (generator + the 5 sized JSON presets)
- `fixtures/nn-training/trace.golden.jsonl` (regenerate via `pnpm nn:trace` if `packages/nn` source
  changes — the trace must match the exact committed source or node-ID/event-shape alignment breaks)
- `.gitignore` (`.claire/`, in addition to main's own `packages/nn/*.jsonl` — union, don't overwrite)
- `biome.json` (`!**/fixtures/demo-graph` exclusion — `huge.json` is ~2.7 MB)
- `DEMO_BRANCH.md` (this file)

This list was previously incomplete (it named only 4 of the 10 files above) — keep it in sync with
reality; a stale list here is exactly how a sync silently drops a demo-only patch.

No CI runs on demo pushes — intentional.  The demo bar is "renders plausibly in a browser", not
"passes the full test matrix".
