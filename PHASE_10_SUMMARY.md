# Phase 10 Summary — Live growing graph + time-travel debugger + explanation layer

**Tag:** `v0.10.0-phase-10`
**Shipped:** 2026-07-09

Phase 10 is the north-star headline: grackle stops being a one-shot frequency visualizer and
becomes a **causal, time-travelable, live-growing** view of a running system. Three ADRs
(0025–0027) were accepted. Unlike Phases 6–9, Phase 10 **intentionally ends the
no-wire-schema-change streak**: 10.2 adds a first-class typed `values` field to `TraceEvent` — the
first wire-schema change since Phase 8.3 — and every other chunk stays a `check-parity` no-op.

## What shipped

### 10.1 — Safe-repr + redaction module (PR #49)

`packages/agent/src/grackle/python_runtime/value_repr.py` — a pure, security-hardened
value→string formatter with no tracer/wire/CLI dependency, shipped standalone so the phase's most
security-sensitive code was reviewable in isolation before 10.2 wired it into the tracer's hot
path. Subclasses `reprlib.Repr` with an **exact-`type(x)` dispatch table** (module-level
`_DISPATCH`, built once at import) instead of stock reprlib's name-based dispatch — kills the
"class named `list`" spoofing hole in one move. **Never invokes an arbitrary user `__repr__`**
and **never consumes a lazy generator/iterator** — the only in-process defenses against a
slow/hanging/side-effecting repr and against turning the debugger itself into a Heisenbug. Fixes
five stock-`reprlib` traps (name-spoofing dispatch, `sorted()`-on-keys invoking user `__lt__`,
unbounded `repr_instance`, the 3.11+ huge-int `ValueError`, unbounded bytes materialization).
Dataclass fields are redacted **before** the value is read at all; the `__slots__` fallback
accepts only a genuine `types.MemberDescriptorType`, never a `property`. A total character budget
bounds nested-assembly work to a small constant multiple of `max_len` regardless of depth.
Name-based redaction runs before any repr machinery touches a value.

An xhigh multi-agent `/code-review` pass (10 finder angles → adversarial verify → sweep) found 11
real findings pre-merge; a follow-up independent verification pass (11 more agents, one per
finding) then caught 2 residual bugs introduced by the first round's own fixes (character-budget
double-counting; a `slice`-with-non-int-component crash risk) plus 3 test-coverage gaps, all
closed before merge. 66 tests, each verified to fail against the pre-fix code.

### 10.2 — Value capture + wire-schema `values` field + CLI (PR #51, ADR-0025)

Wires 10.1's `value_repr.py` into the Python `sys.monitoring` tracer and lands the phase's one
wire-schema change: a first-class typed `values` field on `TraceEvent`
(`{args?: ArgValue[], ret?: string, ret_truncated?: boolean}`), hand-synced across
`trace.schema.json`, `messages.ts`, and `adapters/base.py`'s TypedDicts (ADR-0025 documents this
three-way sync as **review-guarded, not parity-guarded** — `check-parity` only diffs
generated-artifact bytes and message-`type` consts, never these hand-written shapes). Returns are
free (`retval` handed straight to `PY_RETURN`); args use the **verified-frame technique** —
`sys._getframe(1)` called directly inside `_on_call`, `frame.f_code is code` checked before
trusting `f_locals`, degrading to no-args capture on any mismatch so the call/return event is
**always** emitted. Opt-in and default-off (`--capture-values`), Python-only, a per-`node_id`
capture budget (`--capture-first-n`, default 100) plus size caps and name-based redaction
(`--no-redact` escape hatch). A default (non-capturing) run stays byte-identical: the `values` key
is omitted, not set to `null`, when absent. ADR-0025 explicitly records the data-at-rest privacy
surface — captured values, even redacted, persist to `-o` JSONL / `--stream` recordings / the
session store. CI fans the whole check job across Python 3.12 **and** 3.13 on every OS specifically
to stress the frame-capture technique's version sensitivity.

Two full review rounds pre-merge: an xhigh 6-dimension pass found 5 documentation/test-coverage
gaps (an inaccurate ADR claim about `check-parity` coverage; several tests that didn't discriminate
the behavior they claimed to verify), fixed and independently re-verified via mutation testing;
then a post-merge xhigh 10-angle review found **zero correctness bugs** — both candidate concerns
were refuted by empirical end-to-end testing — leaving only 4 low-severity findings, none applied.

### 10.3 — Time-travel value inspector + call-step navigation (PR #53)

A right-sidebar `ValueInspectorPanel` (frontend-only, no wire change) that, as you scrub
`tracePlayhead`, shows a function's call args and return value plus the live call stack at that
point — a read-only time-travel debugger. `graph/ancestorStack.ts`'s `ancestorStackAt` derives the
open call stack by prefix-replay, **mirroring** `callTree.ts`'s depth-driven unwind rule (a
consistency cross-check test is the drift tripwire). `graph/useFullTrace.ts` supplies the replay
prefix: the store's append-only trace in buffered mode, or a lazily-paged, promise-cached,
stale-guarded prefix in seekable mode. **The 50k cliff is gated so a partial-prefix
reconstruction is structurally impossible** — `full.truncated && tracePlayhead >=
full.events.length` shows an explicit "first 50k only" unavailable state.

Verified live end-to-end first, then an xhigh 6-finder review found 2 real correctness bugs — a
truncation-boundary off-by-one (`>` instead of `>=`) and a trace cache keyed only on `sessionId`
that served a stale prefix when a store-loaded session's file was overwritten (fixed by keying on
`sessionId:traceTotal`) — both mutation-verified, plus a round-2 pass (false-positive capture-off
hint, unmount guard, LRU-evict-one cache, memoized derived view). Also surfaced an unrelated
`GraphCanvas`/Sigma crash on graphs with an unpositioned node, isolated by PR #46's `ErrorBoundary`
and filed as a follow-up.

### 10.4 — Explanation layer: edge evidence (PR #55, ADR-0026)

Every static edge across all four parsers and cross-language edges now carries a 1-based
`metadata.line` — the justifying import/call/inherit/implements/route/spawn source line — in the
edge's already-open `metadata` bag, so **no wire-schema change** (ADR-0004's open-metadata
posture). The load-bearing trap: all four `resolve_graph()` resolvers rebuild inherit/call edges
on resolution, and the pre-existing resolved branch **replaced** the edge metadata wholesale —
silently dropping `line` for every successfully-resolved edge. Fixed by carrying the original
evidence forward while dropping only the `resolved` marker. Cross-language line threading turned
out to be cheap: hint positions are static regex-over-source, uniform across all four languages.
Frontend: a new `EdgeEvidencePanel` triggered by a Sigma `clickEdge` handler or by selecting a node
lists its in/out edges with the source snippet, clickable to jump to the exact line;
`MultiDirectedGraph` preserves parallel edges end-to-end so two calls to the same target on
different lines surface as two independently-jumpable rows.

Manually verified end-to-end against `fixtures/tiny-polyglot` (14/14 edges carried correct lines),
then an xhigh 10-finder review (~29 agents across two runs after a session-limit interruption)
found **one real bug** — `selectEdge` cleared `selectedNodeId` but not `sourceViewerTarget`,
leaving the source viewer pinned to the previous edge's file/line (independently flagged by 5 of
10 finder angles) — fixed and mutation-verified, plus two low-severity cleanups.

### 10.5 — Explanation layer: causal "why did this fire" path (PR #57, ADR-0026 §8 amendment)

A selection-driven `CausalPathPanel` that answers "why did this node fire?" — pick a node, pick
which firing, read the ancestor chain root → … → THIS with the argument values that drove each
hop. `graph/causalPath.ts`'s `causalPathAt` is a thin, drift-guarded wrapper over 10.3's
`ancestorStackAt` — no new reconstruction algorithm needed. **The key new claim: truncation is a
completeness banner, not a hard gate** — every rendered path is provably correct even from a
>50k-truncated prefix, because `causalPathAt` only ever replays `[0, callIndex]`; only firing
*enumeration* (capped at `MAX_FIRINGS=200`) is bounded. Per-hop navigation fuses time-travel +
explanation via three independent single-action buttons, with an O(1) nested-Map index for
call-site lookup rather than a per-hop edge scan.

Two full xhigh review rounds, 69 agents total — the standout lesson of the phase. Round 1
(pre-commit, 29 agents) found and fixed six real issues, including a `callEdgeLineIndex` lookup
that matched *any* edge kind sharing a (source, target) pair (an import edge could masquerade as a
"call site"). Round 1's fix for a re-scan issue was a `useRef` latch ("once true, stay true"); a
**fresh round-2 review on the already-committed PR (40 agents) found this latch had introduced two
real regressions** — a post-commit-effect session-switch re-latch, and a fixed session id that
could never trigger a session-keyed reset — so it was reverted to the plain `useMemo` it replaced.
Round 2 also caught a spurious staleness warning, a sticky-firing key missing a session id, and a
misleading "did not fire" message for a not-yet-loaded prefix. **Lesson: a fresh adversarial pass
on an already-committed diff caught a correctness fix's own optimization silently regressing
correctness** — something no amount of re-reading the same fix in place would have surfaced.

### 10.6 — Watch mode server (PR #59, ADR-0027)

File-watcher on `grackle serve --watch`: stdlib mtime-polling by default, an optional accelerated
`watchfiles` backend behind `try: import watchfiles` (never a required dependency). On a real
content change, **hash-gates** (SHA-256-of-bytes, with a cheap `(mtime_ns, size)` pre-filter)
before evicting the cache and re-parsing — atomic-save and a bare `touch` fire zero re-parses. A
dedicated single-worker `ThreadPoolExecutor` runs the rebuild off the event loop; the watch loop
skips parse+broadcast entirely when no client is connected. The re-push reuses the **exact same**
`make_static_graph` builder and `broadcast()` fan-out as the connect-time push — **no new wire
message type**. `--watch-interval` (default 0.3s) is both the poll cadence and the `watchfiles`
debounce window; measured warm-cache rebuild ≈55ms on `fixtures/stress-2k` (209 files), comfortably
inside the interval. Deliberately incomplete by design: the re-push is wire-indistinguishable from
a fresh connect, so before 10.7 the frontend rebuilt Sigma from scratch on every watch event —
ADR-0027 explicitly defers the fix.

### 10.7 — Watch mode frontend: graph-diff animation (PR #60)

The frontend payoff of 10.6: a watch-mode `static_graph` re-push now diffs against the **live
graphology instance** instead of rebuilding Sigma from scratch, so ForceAtlas2 layout positions
and the camera survive edits. `graph/applyGraphDiff.ts` merges survivor attributes **without ever
touching x/y/size/color/hidden**, seeds new nodes near a positioned neighbor, and multiset-matches
parallel edges by a `source\0target\0kind\0line` composite key. `GraphCanvas.tsx` splits into an
unmount-only teardown effect plus a rebuild-or-apply effect gated by a `hasSurvivor` check; new
nodes/edges pulse in and removed nodes fade out via a small rAF-driven animation helper, suppressed
under `prefers-reduced-motion`. A bounded FA2 reheat pins survivors so only the changed neighborhood
resettles. **Pre-existing bug fixed en route:** the node reducer returned a bare
`{color, size, hidden}`; Sigma's `nodeReducer` *replaces* attrs wholesale rather than merging, so
`x`/`y` were silently dropped and Sigma threw on its first `addNode` — **the graph canvas had never
actually rendered, on `main` too**, isolated only by PR #46's `ErrorBoundary`.

Two full xhigh review rounds. Round 1 (7 finder angles → verify → sweep) found and fixed two real
bugs in the reheat/FA2 state machine: a node-changing re-push arriving during the initial 5s settle
froze a permanently half-settled layout, and `reheat`'s bare `fa2.start()` silently no-ops when FA2
is already running. A candidate "space-separator collision" in the edge composite key was
**refuted** by direct byte inspection (the delimiter is `\0`, which review tooling had rendered as
whitespace). Round 2, run specifically to distrust round 1's fixes, confirmed them clean. Verified
live against a real `grackle serve --watch` session: survivor positions bit-for-bit identical
across add/edit-line/delete/atomic-no-op-save cycles.

---

### 10.D — Demo branch forward-sync (PRs #63–64, one sub-chunk pending)

`demo/end-product-preview` (the visitor-facing, CI-exempt, never-merged-to-main preview branch)
had gone stale at the Phase 6 line since a 2026-06-10 fixture-switcher patch — 30 commits / phases
7–10 behind. 10.D brings it up to v0.10.0.

**10.D.1** (PR #63) — theme-aware `labelColor` + `allowInvalidContainer` in `GraphCanvas.tsx`,
porting a fix the demo branch already carried locally, so demo graph labels are no longer
invisible on the dark canvas.

**10.D.2+3** (PR #64) — rewrote `README.md`'s "What it does" for the 4-language static+runtime
matrix and Phase 8–10 features (was still Python-only framing); captured `trace.golden.jsonl` for
all four runtimes (`fixtures/value-capture` with `--capture-values`, `fixtures/tiny-node-app`,
`fixtures/tiny-go-app`, `fixtures/tiny-rust-app`). An xhigh multi-agent `/code-review` pass found
and fixed 4 real issues before merge: the README's `grackle diff` bullet overstated the CLI (it's
trace-vs-trace only; trace-vs-static is UI-only, `DiffPanel`); no invocation path was given for any
headlined runtime feature (fixed with a "try it" command block); `fixtures/tiny-go-app/go.mod`
required Go 1.21 while the README/adapter floor is 1.20 (lowered, golden trace re-verified
unaffected); `fixtures/tiny-rust-app/Cargo.lock` was left untracked and ungitignored (committed
it). Also documented (inherent to CPU-sampling profilers, not a bug) that the Node golden's
`add()` — called 2,000,000× — has zero trace events since it completes faster than the ~250µs
sampler interval.

**10.D.4** (the demo-branch sync itself) — **staged, not yet applied.** `demo.py` modernized to
delegate to `python_runtime.file_replay.replay_trace` and `server._build_static_graph` instead of
hand-rolled copies (demo graphs now carry hub-score + cycle metadata too); new fixtures
`values`/`node`/`watch`; `go`/`rust` gained real golden-trace replay; the session library (Phase
8.3) backed by a real seeded `SessionStore` through the production
`session_list_request`/`session_load_request`/`trace_seek_request` path (a missing
`trace_seek_request` handler — sessions loaded but could never actually serve their events — was
found via a raw-protocol test bypassing the browser, then fixed and re-verified); `FixtureSwitcher.tsx`
(new) + `HeaderChrome`/`client.ts`/`main.tsx` re-layered — all verified live end-to-end (12
fixtures, value redaction, edge evidence, causal path, session load, watch-mode diff animation) and
full-gate green, but **staged on branch `demo-sync/phase-10.D`, not pushed to
`demo/end-product-preview`** — that push needs explicit approval per `DEMO_BRANCH.md`'s
force-push playbook (on the demo branch) and is withheld pending it. See `DEMO_BRANCH.md` on the
demo branch for the full sync changelog once applied.

---

## Code-review fixes (per chunk, highlights)

Every chunk went through at least one xhigh multi-agent `/code-review` pass before merge, several
through two. The highest-signal fixes:

| Chunk | Representative fixes |
|---|---|
| 10.1 | Exact-`type(x)` dispatch table hardened; round-2 independent verification (11 agents) caught 2 regressions in round-1's own fixes (character-budget double-counting; a `slice`-with-non-int crash) plus 3 test gaps. |
| 10.2 | 5 documentation/test-coverage gaps closed pre-merge (an inaccurate ADR claim; non-discriminating tests); post-merge review found zero correctness bugs, refuting two candidate concerns via empirical e2e testing. |
| 10.3 | Truncation-boundary off-by-one (`>` vs `>=`) and a trace cache keyed only on `sessionId` serving stale prefixes across store-loaded session overwrites — both fixed and mutation-verified. |
| 10.4 | `selectEdge` left a stale `sourceViewerTarget` pinned after selecting a line-less edge — independently flagged by 5 of 10 finder angles in a ~29-agent pass. |
| 10.5 | A `useRef` "sticky" latch that fixed a round-1 issue was itself found, on a **second** adversarial pass of the already-committed PR, to have introduced two new regressions — reverted to a plain `useMemo`. |
| 10.6 | Hash-gate (SHA-256 + `(mtime_ns, size)` pre-filter) added specifically so atomic-save/touch never trigger a spurious full re-parse + broadcast. |
| 10.7 | A watch-triggered re-push mid-initial-settle could freeze the layout permanently (timer-ref split fix); `fa2.start()` silently no-ops when already running (`stop(); start()` fix); an unrelated pre-existing bug (the graph canvas never actually rendering on `main`) found and fixed en route. |

---

## Acceptance grid — Phase 10

| # | Criterion | Status |
|---|---|---|
| 1 | **Safe-repr module.** `value_repr.py` never invokes an arbitrary `__repr__`, never consumes a lazy iterator/generator, redacts sensitive names before repr, bounds output by length/items/depth/character-budget, and never raises. 66 tests, each mutation-verified. | **10.1 ✓** automated |
| 2 | **Value capture wire + CLI.** `grackle trace fixture.py --capture-values -o t.jsonl` emits a typed `values` field on call/return events; redaction, per-node budget, and size caps hold; a default run stays byte-identical (no `values` key). | **10.2 ✓** automated |
| 3 | **Frame-capture correctness.** The verified-frame technique (`sys._getframe(1)` + `frame.f_code is code`) degrades args, never events, on any mismatch; a dedicated Python 3.13 CI leg stresses positional/kw-only/`*args`/`**kwargs`/generator/comprehension/async/method/recursive fixtures. | **10.2 ✓** automated |
| 4 | **`messages.ts` / TypedDict hand-sync.** The schema, `messages.ts`'s canonical `TraceEvent`, and the Python `TraceEvent` TypedDict all carry the new `values` field in agreement; `check-parity` passes and reflects the field in `src/generated/`. | **10.2 ✓** automated + manual |
| 5 | **Data-at-rest privacy documented.** ADR-0025 explicitly records that captured values, even redacted, persist to on-disk recordings and the session store — not treated as "no change." | **10.2 ✓** manual |
| 6 | **Time-travel value inspector.** `ValueInspectorPanel` shows per-arg + return values and the live call stack at `tracePlayhead`; prev/next stepping lands on call/return boundaries; prefix memoized per session. | **10.3 ✓** automated + manual |
| 7 | **50k-cliff gating (inspector).** A partial-prefix stack reconstruction is structurally impossible — `truncated && playhead >= events.length` shows an explicit unavailable state rather than a plausible-but-wrong stack. | **10.3 ✓** automated |
| 8 | **Edge evidence.** Every edge kind (import/call/inherit/route/subprocess/cross-language) carries `metadata.line`; clicking an edge or a node's in/out edges shows the justifying source line and jumps to it; unresolved edges degrade cleanly. | **10.4 ✓** automated (pytest) + manual |
| 9 | **Causal path.** Selecting a firing renders the ancestor call-path chain with per-hop argument values; hops navigate independently (time-travel / select / call-site); disambiguates which invocation when a node fired many times. | **10.5 ✓** automated + manual |
| 10 | **Causal-path truncation correctness.** Every rendered path is correct even from a >50k-truncated prefix — only firing *enumeration* is bounded, never path reconstruction itself. | **10.5 ✓** design + automated |
| 11 | **Watch mode server.** `grackle serve --watch` + edit/add/delete a `.py`/`.ts`/`.go`/`.rs` file pushes an updated graph to connected browsers within the debounce window; atomic-save with no content change triggers no re-push (hash-gated). | **10.6 ✓** automated + manual |
| 12 | **Watch mode perf ceiling documented.** Warm-cache rebuild ≈55ms measured on `fixtures/stress-2k` (209 files); no required new dependency (`watchfiles` optional-only). | **10.6 ✓** bench (manual) |
| 13 | **Graph-diff animation.** A watch-triggered re-push grows the graph in place — existing node positions and camera survive; new nodes/edges animate in; removed nodes fade out; suppressed under `prefers-reduced-motion`. | **10.7 ✓** automated + manual |
| 14 | **Wire-schema discipline.** `KNOWN_MESSAGE_TYPES` and the schema change exactly once, in 10.2; `check-parity` is a no-op for 10.1 and 10.3–10.7. | **10.1–10.7 ✓** automated |
| 15 | **ADR discipline.** ADR-0025 (value capture), ADR-0026 (edge evidence + causal path), ADR-0027 (watch mode) accepted; ADR count 24 → 27. | **10.2 / 10.4 / 10.5 / 10.6 ✓** manual |
| 16 | **Cross-OS.** All chunks green on the Ubuntu + Windows CI matrix; 10.2's frame-capture fixtures additionally cross Python 3.12 × 3.13 (4 legs total). | **CI ✓** automated |
| 17 | **Ship.** ADRs 0025–0027 accepted; `PHASE_10_SUMMARY.md`; `PROJECT_ACCEPTANCE.md` §F grid (27 ADRs); `CLAUDE.md` (Phase 10 shipped, Phase 11 candidate pool); version 0.10.0; tag `v0.10.0-phase-10`. | **10.H ✓** |

---

## Known limitations

- **The 50k `fetchFullTrace` cliff is gated, not removed.** Both the value inspector (10.3) and the
  causal path (10.5) show an explicit unavailable state past the cliff rather than a wrong stack,
  but a server-side `trace_ancestors_at` query (the real long-term fix) is documented future work
  in ADR-0026 §8, not built — it would add an 18th message type.
- **Value capture is Python-only.** `sys.monitoring` is the only runtime with argument/return
  values; Node/Go/Rust have no values capture (categorical, not a gap to close — Node's CDP
  sampling channel doesn't naturally expose call arguments, and Go/Rust's coverage-based channels
  have no values at all).
- **Redaction is name-based only.** A sensitive value behind an innocuous argument or key name
  (e.g. a token in a variable named `s`) is not caught; content/entropy scanning is out of scope
  (documented since 10.1).
- **Watch mode has no `.gitignore` respect.** The watcher and underlying parser walkers only honor
  `ParseOptions.exclude_patterns`; a large ignored directory sharing a parseable extension is still
  walked and watched. Documented future work in ADR-0027.
- **Full re-push, not `graph_delta`.** Watch mode re-pushes the entire static graph on every
  change; a wire-level incremental diff protocol is deferred (ADR-0027) — at measured
  `stress-2k` scale (209 files, ≈55ms) it is not the bottleneck, but the ceiling on much larger
  repos is undocumented.

---

## Phase 11 preview

Phase 10 completes the north-star trio (time-travel, explanation, live growth) on the visualization
side. Phase 11 turns toward the **learning** half of the north star: **"Watch it learn"** — a new
`packages/nn/` package containing a from-scratch, layer-granularity numpy MLP (traced by grackle's
own existing tracer/inspector/heat-map/diff tooling, unchanged) — followed by Phase 12, **"grackle
learns as it analyzes"**: a self-supervised hotspot-prediction engine trained on the session-store
corpus, surfaced as a capability-gated `predicted_heat` analysis with no wire-schema change. Both
phases are already detailed to block-level implementation specs (a separate, user-approved planning
pass), sequenced to start after this ship — Phase 11 first (lower risk, standalone package), Phase
12 second. No ADRs are pre-committed yet; ADR numbers 0028 (Phase 11) and 0029–0030 (Phase 12) are
reserved but not written.
