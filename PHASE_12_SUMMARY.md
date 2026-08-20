# Phase 12 Summary — grackle learns as it analyzes

**Tag:** `v0.12.0-phase-12`
**Shipped:** 2026-08-20

Phase 12 turns the Phase-11 MLP from a traced *subject* into grackle's own ML engine. It closes a
self-supervised loop — **analyze** a project, **trace** it, **learn** from the pair, **predict** on
graphs that have never been traced — using nothing but signal grackle already produces: a static
graph and a runtime trace. `grackle learn` trains a hotspot-prediction model from a
`(graph, trace)` pair; `grackle serve --model` injects the prediction as an open `predicted_heat`
metadata key, capability-gated so its absence is byte-identical to never having built the feature
at all; the frontend renders it as a predicted-vs-actual overlay and a live loss curve; and a new
`NetworkViewPanel` renders the NN itself as a network, animated off the same trace playhead every
other panel uses. **No wire-schema change all phase** — `check-parity` is a no-op on every chunk.
Two ADRs were accepted (0029, 0030); ADR count 28 → 30.

## What shipped

### 12.0 — Incremental trace persistence (PR #76, `6d89fd6`)

An agent-only durability fix, landed before the ML chunks because it was a bug found on shipped
behavior, not new product surface. `grackle trace -o` used to buffer an entire run in memory and
write it once at the end — a killed tracing process lost everything, and memory grew unbounded on
long runs. `JsonlPartWriter` (`python_runtime/writer.py`), extracted from the Phase-9.3
`RecordingSink` mechanism, now backs both `-o` and the `--stream --output` tee: per-event writes to
`FILE.jsonl.part` with no per-event flush, and an atomic truncate-torn-tail → close → rename
finalize. Gated on a new `RuntimeAdapter.streaming_trace_parity` Protocol attribute — Python is
`True` (its `trace()`/`trace_streaming()` are the same instrument); Node, Go, and Rust stay `False`
and keep their original one-shot buffered write, since Node's `trace_streaming()` is a different
instrument (coverage polling vs. sampling) and Go/Rust have no streaming trace at all. An existing
`.part` is **refused, never cleared** — an earlier design that cleared a stale `.part` at the start
of a re-run was reverted during review as both self-defeating (it deletes exactly the salvaged
events this mechanism exists to produce) and unsafe under concurrency (POSIX `unlink` on a file
another process still holds open lets a second run silently overwrite the first). Write failures
are swallowed at the sink and reported from `writer.broken`, never propagated into the traced
program via `sys.monitoring` — a tracer must not change the traced program's semantics. All JSONL
emitters (`write_jsonl`, `JsonlPartWriter`, and `ml_bridge.py`'s history log) now write binary UTF-8
with an explicit `\n`; `write_jsonl` previously used text mode and emitted CRLF on Windows.
Documented as a second Amendment to **ADR-0020** (no new ADR — the same precedent `RecordingSink`
itself used).

### 12.1 — ML pipeline (`packages/nn/src/grackle_nn/ml/`) (PR #77, `604e245`)

A self-supervised hotspot-prediction pipeline, standalone in the `nn` package — see **ADR-0029**
for the full design. `features.py` extracts a 35-column structural feature vector from a raw static
graph (degree buckets by edge kind, an independently-reimplemented iterative Tarjan SCC, BFS
reachability from an entry set, name/path flags); `labels.py` mirrors the agent's
`TraceAggregates` count-weighting exactly, pinned by a cross-check test against all 5 committed
golden traces; `dataset.py` splits train/val by whole graph, never by node; `metrics_rank.py` is
pure-numpy Spearman + top-k overlap (no scipy); `heat_model.py` trains the Phase-11 MLP
architecture (`Linear(35,64)→ReLU→Linear(64,32)→ReLU→Linear(32,1)`, Adam+MSE) wired to the
`record_epoch` beacon, with an atomic, two-pass-validated `heat-model.npz` checkpoint format. A
seeded synthetic-corpus acceptance test proves the trained model beats a raw-in-degree Spearman
baseline on held-out graphs by a documented margin; a real-fixture smoke test (no held-out baseline
— only ~2 real trace-bearing fixtures exist) covers Python plus the polyglot Go/Rust/Node goldens.
Import-hygiene is enforced by a fresh-subprocess + AST-scan test pair (`grackle_nn.ml` never
imports `grackle` at runtime). 84 test functions across 8 files. An 8-angle, 58-agent adversarial
code review found 25 raw findings, 23 confirmed and fixed pre-merge — including one real
correctness bug (a self-recursive node with no other callers was wrongly excluded from the BFS
entry set), three unguarded crashes on malformed input, `spearman` returning `NaN` on empty input
instead of the documented `0.0`, and a duplicated tie-averaging algorithm extracted to a shared
`_rank_utils` helper.

### 12.2 — `grackle learn` + capability-gated `predicted_heat` (PR #79, `d55dc18`)

See **ADR-0030** for the full design. `src/grackle/ml_bridge.py` is the agent's only `grackle_nn`
importer, and does it exclusively inside function bodies — importing `grackle.cli`/`grackle.server`
never pulls in numpy, enforced by a fresh-subprocess + AST-scan test pair mirroring 12.1's nn-side
hygiene test. The gate (`learn_available()`, cached, soft-imports `grackle_nn.ml`) mirrors the
existing Go/Rust toolchain-capability precedent. `grackle learn [TRACES...] --root R` merges every
trace's heat into one `Example` (one root ⇒ one graph — no held-out graph, so the reported Spearman
numbers are train-set metrics, never mislabeled as validation). `serve --model` injects
`metadata.predicted_heat` from inside `_build_static_graph`, so watch-mode re-broadcasts carry it
too, not just the connect-time push. Absence is byte-identical: model missing, gate closed, or
model broken all add **no key** — proven by a test comparing serialized payloads byte-for-byte, not
just key presence. The prediction cache (`(graph_sig, model_mtime_ns, model_size)`) is deliberately
separate from the hub-score/cycles `meta_cache`, so a model failure can't poison unrelated
analyses and a retrain never forces a Tarjan re-run. During this PR, a bundled hotfix (out of scope
for 12.2, blocking its own CI) fixed a Phase-12.1 bug in `labels.py::make_targets`: mixing
`np.log1p` with `math.log1p` disagreed by 1 ULP on at least one Linux CI runner, normalizing the
hottest node to `1.0000000000000002` and breaking the documented `[0,1]` bound (macOS's libm never
reproduced it, so it first read as a flake).

### 12.3 — Frontend: predicted-heat overlay + `LossCurvePanel` (PR #81, `c3a21bf`)

Frontend-only consumer of 12.2's `metadata.predicted_heat`. A right-sidebar `PredictedHeatPanel`
(Off/Predicted/Vs-actual modes, top-10 lists, vs-actual status chips) paints the graph via a new
`predictedOverlay` cascade branch in `GraphCanvas`, inserted after `diffOverlay` and before the
runtime heat-map — an explicitly-toggled diff always wins. A bottom-dock `LossCurvePanel` extracts
the NN's per-epoch loss/accuracy curve from `record_epoch` trace-beacon events (new pure modules
`epochSeries.ts` + `lossCurveLayout.ts`) with hover tooltips and click-to-seek. `predictedHeat.ts`'s
vs-actual classification mirrors `labels.py`'s log1p normalization — JS `Math.log1p` is a third,
slightly-divergent implementation, absorbed by a `±0.25` threshold rather than chased to exact
equality (the same lesson 12.2's `make_targets` fix already taught). A same-day review-fix commit
landed two real correctness bugs before merge: `PredictedHeatPanel`'s debounced overlay-push effect
depended on the vs-actual overlay unconditionally, so in "Predicted" mode a live-streaming session's
per-batch churn kept re-arming the 150ms debounce timer and the overlay never actually painted; and
`lossCurveLayout.ts`'s accuracy axis was unclamped — `record_epoch`'s third field is accuracy
`[0,1]` for the classification demo but an unbounded validation loss for `heat_model.py`'s
regression loop sharing the same beacon field, so an out-of-range value rendered off-canvas. The
same commit added an incremental scan cache for `LossCurvePanel` (previously O(N²) per
live-streaming session), extracted a shared `countEvents` helper, and hid the panel entirely for
non-NN traces instead of a permanent dead row.

### 12.4 — `NetworkViewPanel` (PR #83, `7bf6502`)

Renders the NN as a network — neuron columns, weight-bundle edges, playhead-animated forward/eval/
backward sweeps — reading the Phase-11.H `record_architecture`/`record_layer_stats` beacons, so
every Phase-11 trace (including `run-a.jsonl`) already carries the data and lights the panel up
retroactively. Two pure modules parse the beacons: `networkSpec.ts` (`extractNetworkSpec`, the
architecture token string → typed layer/activation tokens, chain-validated) and `layerStats.ts`
(`extractLayerStats`, per-epoch per-layer weight/weight-change RMS via an arity-exact regex built
from `epochSeries.ts`'s shared `FLOAT` fragment). `layerActivity.ts` and `networkLayout.ts` derive
the phase (forward-train/forward-eval/backward/idle) and geometry from the trace playhead.
Floating-overlay placement, order 10, above `GraphCanvas` — collapsed by default (a small chip),
expanded on click into a card occluding the graph (deliberate — a money-shot view). All colors are
literal hex (2D canvas can't resolve CSS `var()`/oklch). Frontend-only; zero wire change; zero new
dependencies. Two review-fix rounds followed the initial PR: **0631a49** fixed exception-exit
frames latching their phase flag forever (frames were closing on a return/RAISE event that never
fires on unwind — now closed by `frame_depth`), unified the playhead-inclusion convention across
four modules that had silently disagreed by one event, and extracted the incremental `useAppendOnlyScan`
hook (generalizing 12.3's inline LossCurvePanel cache) so the panel's four per-batch rescans stay
O(1) amortized; **07ea07a** fixed a silently-dropped non-finite-value counter in the layer-stats
scanner and added canvas-painting test coverage (previously zero, since `getContext` was stubbed to
`null`); **99b5a9a** was a pure test addition — a mutation-verified battery (30/30 mutants killed
across the 8 new modules) that added 9 intentionally-red `it.fails` tests documenting real,
un-fixed defects for follow-up (e.g. the phase indicator never returning to idle on the real
25,870-event `run-a.jsonl` trace, since the transition is keyed off an event that doesn't fire on
every exit path). Test count for the 8 phase-12.4 modules: 164 cases across 8 files (commit-stated
progression 723 → 759 → 774 tests repo-wide across the two fix commits).

### 12.H — Ship (this PR)

**ADR-0029** ("The self-supervised learning loop" — the F=35 feature table, label normalization and
its rejected alternatives, split-by-graph anti-leakage, from-scratch rank metrics, the independent
Tarjan/BFS reimplementation, the labels/aggregates cross-check tripwire, the `record_epoch` reuse in
the trainer) and **ADR-0030** ("The capability-gated inference surface" — the `ml_bridge` gate, the
byte-identical-when-absent guarantee, the `predicted_heat` wire shape, the separate model-freshness
cache, the `grackle learn` shared-root contract) accepted; ADR count 28 → 30. This summary;
`PROJECT_ACCEPTANCE.md` §H; `CLAUDE.md` (Phase 12 shipped, Phase 13 candidate pool); version 0.12.0
on `packages/agent`, `packages/nn`, and `packages/frontend` (both `uv` locks re-locked — now
bidirectional: the agent's lock pins editable `grackle-nn`, the nn package's lock pins editable
`grackle`); tag `v0.12.0-phase-12` (post-merge).

## Sizing / integrity numbers

| Quantity | Value | Note |
|---|---|---|
| Feature vector width | **35 columns** | `FEATURE_VERSION = 1`; a reorder or column-set change requires a version bump |
| ML pipeline tests | **84** functions, 8 files | `packages/nn/tests/ml/` |
| Synthetic acceptance corpus | **8 graphs**, seeds 0–7 | `val_count=2`, 300 epochs, margin `≥ baseline + 0.05` and `> 0.5` absolute |
| Golden-trace label cross-check | **5 fixtures** | Python, Node, Go, Rust, value-capture — `heat_from_jsonl` vs. live `TraceAggregates` |
| `predicted_heat` payload (stress-2k) | **~110 KB** | all nodes, 4-decimal-place scores, one-time per connect |
| Prediction cache key | `(graph_sig, model_mtime_ns, model_size)` | deliberately separate from `meta_cache` |
| 12.4 module tests | **164** cases, 8 files | mutation-verified 30/30 in the final test-only commit |
| ADR count | **28 → 30** | ADR-0029, ADR-0030 |
| Wire-schema changes all phase | **0** | `check-parity` a no-op on every chunk |

## Acceptance grid — Phase 12

| # | Criterion | Status |
|---|---|---|
| 1 | **Incremental trace persistence.** `grackle trace -o`/`--stream --output` write per-event to a `.part` file with atomic finalize; a killed Python tracing process keeps every event written so far; an existing `.part` is refused, never cleared; Node/Go/Rust keep their original one-shot write, gated on `streaming_trace_parity`. | **12.0 ✓** automated |
| 2 | **Cross-platform JSONL byte format.** Every JSONL emitter writes binary UTF-8 with an explicit `\n`; `write_jsonl`'s prior CRLF-on-Windows divergence from `RecordingSink`/`JsonlPartWriter` is fixed. | **12.0 ✓** automated |
| 3 | **35-column feature vector, versioned.** `extract_features` computes all 35 columns from a raw graph dict only (never enriched metadata); `FEATURE_VERSION` gates the column layout; pinned by an exact-literal test on two hand-computed node rows. | **12.1 ✓** automated |
| 4 | **Label correctness and cross-platform stability.** `make_targets`'s per-graph max-normalized log heat uses one `np.log1p` implementation for numerator and denominator (never a `math`/`numpy` mix), pinned exact (not tolerance) across ten magnitudes. | **12.1 / 12.2 ✓** automated |
| 5 | **Anti-leakage split.** `split_by_graph` moves whole graphs, never nodes; executable test proves no graph's rows straddle the split. | **12.1 ✓** automated |
| 6 | **Model beats a from-scratch baseline.** A seeded synthetic-corpus acceptance test proves the trained model's held-out Spearman beats raw in-degree by a documented margin; a mutation test (shuffled labels) proves the bar isn't vacuous. | **12.1 ✓** automated |
| 7 | **Labels mirror the agent exactly.** `heat_from_jsonl` reproduces the agent's `TraceAggregates.cumulative_heat_all` byte-for-byte on all 5 committed golden traces. | **12.1 ✓** automated |
| 8 | **`nn.ml` never imports `grackle` at runtime.** Fresh-subprocess + AST-scan test pair (not regex — catches comma/alias/`TYPE_CHECKING` forms). | **12.1 ✓** automated |
| 9 | **`ml_bridge` gate mirrors the toolchain precedent.** Cached availability check, remediation message, reset hook — same shape as the Go/Rust capability gates; a broken/absent `grackle-nn` degrades to a clean CLI error, never a traceback. | **12.2 ✓** automated |
| 10 | **`grackle.cli`/`grackle.server` never pull in numpy.** Fresh-subprocess + AST-scan test pair on the agent side, mirroring 12.1's. | **12.2 ✓** automated |
| 11 | **Absence is byte-identical.** Model missing, gate closed, and a broken model all produce the exact same serialized graph payload (`{hub_score, cycles}` only) — asserted byte-for-byte, with a test proving the comparison is discriminating. | **12.2 ✓** automated |
| 12 | **Watch-mode parity.** `predicted_heat` is injected from `_build_static_graph`, so a watch-triggered re-broadcast carries it, not only the connect-time push. | **12.2 ✓** automated |
| 13 | **`grackle learn` CLI contract.** Shared-`--root` assumption documented and enforced; `--from-store` warns and skips missing recordings; zero traces → usage error; zero overlap → clean error. | **12.2 ✓** automated |
| 14 | **Predicted-heat overlay + cascade order.** `PredictedHeatPanel` (Off/Predicted/Vs-actual) paints via a `GraphCanvas` cascade branch after `diffOverlay`, before the heat-map; vs-actual normalization matches `labels.py` within a documented `±0.25` tolerance. | **12.3 ✓** automated + manual |
| 15 | **`LossCurvePanel`.** Extracts `record_epoch` events into a loss/accuracy curve with hover + click-to-seek; degrades cleanly (hidden) on non-NN traces; incremental (non-quadratic) scan under live streaming. | **12.3 ✓** automated |
| 16 | **`NetworkViewPanel`.** Renders the demo architecture from `record_architecture`/`record_layer_stats`, animates phase off the trace playhead, collapsed by default; all literal hex colors (no CSS `var()` on canvas). | **12.4 ✓** automated + manual |
| 17 | **12.4 mutation-verified.** A dedicated mutation-testing pass over the 8 phase-12.4 modules kills 30/30 mutants; known residual defects are captured as intentionally-red `it.fails` tests, not silently accepted. | **12.4 ✓** automated |
| 18 | **No wire-schema change all phase.** `KNOWN_MESSAGE_TYPES` and every generated artifact untouched 12.0–12.4; `check-parity` a no-op on every chunk. | **12.0–12.4 ✓** automated |
| 19 | **ADR discipline.** ADR-0029 (self-supervised learning loop) and ADR-0030 (capability-gated inference surface) accepted; ADR-0020 amended (incremental persistence) rather than given a new ADR, matching the `RecordingSink` precedent. | **12.H ✓** manual |
| 20 | **Cross-OS.** All chunks green on the Ubuntu + Windows CI matrix; the agent's `uv sync --frozen` dev venv now pulls numpy + `grackle-nn` on every leg. | **CI ✓** automated |
| 21 | **Ship.** ADRs 0029–0030 accepted; this summary; `PROJECT_ACCEPTANCE.md` §H grid (30 ADRs); `CLAUDE.md` (Phase 12 shipped, Phase 13 candidate pool); version 0.12.0 on agent/nn/frontend, both `uv` locks re-locked; tag `v0.12.0-phase-12`. | **12.H ✓** |

## Known limitations

- **The self-supervised label is only as good as the trace it comes from.** A short or
  unrepresentative trace produces a label reflecting that one run, not a program's general hot
  paths — inherent to the approach, not a defect (ADR-0029).
- **`grackle learn` reports train-set metrics, never validation.** One `--root` ⇒ one merged graph
  ⇒ no held-out graph to score against; `val_spearman` stays `null` in the report card until a
  multi-root corpus exists (ADR-0030 future work: a session-store `root` column).
- **`serve` has no `--exclude` to match `learn --exclude`.** A model trained on a filtered subset is
  still scored against the full unfiltered graph at inference time; surfaced as an explicit runtime
  warning, not fixed — deferred to Phase 13.0 (ADR-0030).
- **The synthetic/real acceptance split is a data-regime concession, not a permanent design.** Only
  ~2 real Python trace-bearing fixtures exist today; the discriminating acceptance bar runs on a
  seeded synthetic corpus, and real fixtures get a smoke test only. As real traces accumulate, the
  bar could migrate toward real data (ADR-0029 future work).
- **No classification head, no graph-convolution layer.** Both considered and explicitly deferred
  (ADR-0029) — the regression head and structural-feature-only input were judged sufficient for
  Phase 12's scope.
- **Known-red tests document real, unfixed Phase-12.4 defects** — nine `it.fails` cases (the network
  phase indicator not always returning to idle chief among them) are committed as intentionally
  failing rather than silently passing over, so a future fix has a discriminating test already
  waiting.

## Phase 13 preview

Phase 12's own future-work lists point at the same seam from two directions: the session store's
missing `root` column blocks both a genuine multi-graph training corpus (ADR-0029) and honest
validation metrics (ADR-0030). A `root` column plus `serve --exclude` (closing the train/serve
feature-skew gap) are the natural next steps for the learning loop; the nine `it.fails` tests left
in `NetworkViewPanel`'s test suite are a ready-made punch list for a frontend polish pass.
