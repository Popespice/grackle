# Phase 11 Summary — Watch it learn

**Tag:** `v0.11.0-phase-11`
**Shipped:** 2026-07-11

Phase 11 turns toward the **learning** half of grackle's north star. It adds `packages/nn/` —
`grackle-nn`, a from-scratch, layer-granularity numpy MLP — and proves grackle can **watch it
learn** end-to-end using the *exact* instruments it already ships (the `sys.monitoring` tracer,
value capture, the time-travel `ValueInspectorPanel`, the heat-map, the flame graph, `grackle
diff`), with **zero changes to the agent, the frontend, or the wire schema** — `check-parity` a
no-op on every chunk. The engineering is inverted from every prior phase: grackle's core needs
nothing new; the *subject* is written so its execution shape is maximally legible to those unchanged
tools. One ADR (0028) was accepted; ADR count 27 → 28.

## What shipped

### 11.1 — The NN package + tooling wiring (PR #66, `92f1125`)

A standalone `uv`-managed package (hatchling build, numpy-only runtime dep) implementing an MLP from
scratch: `Linear`/`ReLU`/`Tanh` layers, `SoftmaxCrossEntropy`/`MSE` losses, `SGD`/`Adam`
optimizers, a `Sequential` container (with atomic `save`/`load`), and a seeded three-class spiral
`demo.py` that trains to ≥0.95 accuracy in 60 epochs. Correctness is pinned by a central-difference
gradient check against every analytic backward. Tooling: the `nn` CI leg (`ruff` + `mypy --strict` +
`pytest`, `uv sync --frozen`) on the OS matrix, the `nn:trace` root script, `nn` added to the
commitlint scope allow-list, and `packages/nn/*.jsonl` + `.grackle/` gitignored.

### 11.2 — Traceability contract + watch-it-learn walkthrough (PR #67, `0ecf736`)

The contract that makes 11.1 legible to grackle, proved straight from a real trace of `demo.py`
(no privileged access to the training loop): the per-epoch `record_epoch` beacon is captured every
epoch under budget; one training step is exactly the documented 34-event call shape (`_GOLDEN_34`);
captured values format cleanly (no `numpy.float64` dtype leakage, no accidental redaction, ndarray
args summarized to shape/dtype); the demo actually learns (loss falls, accuracy climbs); and the
**per-event capture-budget accounting itself** is pinned so a tracer regression fails loudly here.
Plus the `README.md` watch-it-learn walkthrough (trace → serve → scrub → diff), including *why*
`--capture-first-n 200` (per-event, per-node budget) and *why* `--root src` (the venv-under-package
trap).

### 11.H — Network-view beacons + ship (this PR)

The ship chunk, which also **folds in the network-view data beacons** (the "network as a network"
amendment's Phase-11 deltas — beacons `record_architecture` + `record_layer_stats`, blocks
B8/B9/B10, tests T1–T3). These were originally slated as 11.1/11.2 deltas but landed here as a
single, reviewable addition; they are the data source Phase 12.4's NetworkViewPanel will render:

- **`record_architecture(model) -> str`** (`metrics.py`, B8) — fires once, before the training loop
  (B10, `demo.py`), returning the layer stack as a token string
  (`"linear:2:32 relu linear:32:32 relu linear:32:3"`); duck-typed on `layer.params`, list
  comprehension only, `Sequential` imported under `TYPE_CHECKING`.
- **`record_layer_stats(epoch, stats) -> tuple`** (`metrics.py`, B8; wired in `fit`, B9) — fires
  once per epoch with each param-carrying layer's `(w_rms, dw_rms)` in model order. `fit` snapshots
  each layer's weights (iterating `model.layers` inline, never `model.parameters()`) and computes
  RMS + per-epoch weight-change RMS inline with numpy (DISABLE'd frames, zero added events),
  pre-rounded to 3 sig figs on the caller; the beacon stays a pure identity passthrough. **Weight-
  change RMS, not gradient RMS** — gradients are zero after each epoch's final `zero_grad`, so a
  per-epoch gradient RMS is unrecordable; `dw_rms = rms(W − W_prev_epoch)` is honestly per-epoch and
  a better learning signal.
- **Tests & docs**: three new traceability tests, T1 (60 `record_layer_stats` returns each parsing
  to a 7-tuple `(int, float×6)`, all finite, none truncated, epoch-0 weight-change RMS strictly
  positive — discriminating at `--capture-first-n 100`; exactly one `record_architecture` return
  with the exact repr; an executable proof that neither beacon ever fires inside a `train_step`
  slice, so the golden 34 stays untouched); the sizing-test edit, T2 (1,312 → 1,320); and the
  README beacon-family callout, T3.

Ship deliverables: **ADR-0028** ("The NN as a traceable subject" — the six conventions, incl. the
generalized beacon parse-contract convention) written and accepted; this summary; the
`PROJECT_ACCEPTANCE.md` §G grid; `CLAUDE.md` (Phase 11 shipped, Phase 12 next); version bump to
`0.11.0` with both `uv` locks refreshed; tag `v0.11.0-phase-11` (post-merge).

## Sizing table (verified against a real trace)

| Quantity | Value | Note |
|---|---|---|
| Per `train_step` | **34 events** | 17 invocations × 2; the golden call shape |
| Per-epoch tail | **22 events** | `evaluate` chain 9 + `record_epoch` 1 + `record_layer_stats` 1, ×2 |
| One-time constant `C` | **30 events** | imports + `__init__`s + one `record_architecture`, ×2 (warm cache) |
| Formula | `E × (S × 34 + 22) + C` | `S = 12` batches/epoch |
| 60-epoch run (warm) | **25,830 events** | in-test; cold `grackle trace` CLI ≈ 25,870 |
| 3-epoch run | **1,320 events** | pins the formula against API drift (±50 slack) |
| Capture recipe | `--capture-first-n 200` | per-`node_id`, per-event; each beacon fires ≤ 60× (120 events) |

## Acceptance grid — Phase 11

| # | Criterion | Status |
|---|---|---|
| 1 | **Standalone package.** `grackle-nn` is a `uv`-managed hatchling package with a numpy-only runtime dependency; `grackle` is an editable **dev-only** dep (`[tool.uv.sources]`), consumed only by the traceability test; the agent stays numpy-free. | **11.1 ✓** automated |
| 2 | **MLP correctness.** `Linear`/`ReLU`/`Tanh`, `SoftmaxCrossEntropy`/`MSE`, `SGD`/`Adam`, `Sequential` (+ atomic `save`/`load`); every analytic backward matches a central-difference gradient check; the demo trains to ≥0.95 accuracy. | **11.1 ✓** automated |
| 3 | **Layer-granularity golden.** One `train_step` traces as exactly the 34-event `_GOLDEN_34` sequence; holds only because `Sequential.backward`/`zero_grad` iterate `self.layers` inline (no `parameters()`/`gradients()` call). | **11.2 ✓** automated |
| 4 | **Sizing formula pinned.** `total ≈ E × (S×34 + 22) + C`, C=30; E=3 → 1,320 ± 50; E=60 → 25,830 warm; drift-guarded (`10k < total < 40k`) and pinned exactly at E=3. | **11.2 / 11.H ✓** automated |
| 5 | **Builtin-float metric boundary.** Every loss/accuracy/RMS crossing a beacon is a builtin `float` (never `np.float64` — the exact-type `safe_repr` dispatch gap); the `numpy\.\w+ object>` fallback appears nowhere in any captured value. | **11.2 / 11.H ✓** automated |
| 6 | **The three beacons.** `record_epoch`, `record_layer_stats`, `record_architecture` are identity passthroughs whose captured return reprs are flat, builtin-typed, 3-sig-fig, and untruncated under default limits — a versioned frontend parse contract; each fires the expected count with values under the `--capture-first-n 200` recipe. | **11.2 / 11.H ✓** automated |
| 7 | **Beacons never perturb the golden.** An executable test walks every `train_step` slice (720 of them) and proves no beacon `node_id` appears within; the per-epoch RMS math adds zero trace events (DISABLE'd numpy frames). | **11.H ✓** automated |
| 8 | **Capture-budget accounting.** The per-`node_id`, per-event budget is pinned (`capture_first_n=4` over 3 epochs → values present pattern `[True, True, False]` on both call and return); requiring all 60 epochs' values discriminates the 200 recipe from the default 100. | **11.2 / 11.H ✓** automated |
| 9 | **Trace-root discipline.** `--root src` keeps `.venv`/numpy/`tests/` out of the graph; no `<unresolved>` frames and traced `node_id`s ⊆ static-graph ids prove nothing leaked. | **11.2 ✓** automated |
| 10 | **Determinism.** One seeded `Generator`; no genexps/`yield` in traced code; no data-dependent branching — structural goldens are stable across runs and OSes. | **11.1 / 11.2 ✓** automated |
| 11 | **Watch-it-learn walkthrough.** `README.md` documents trace → serve → scrub → diff, the beacon family, the `--capture-first-n 200` rationale, and the `--root src` trap; verified live in the browser (heat, flame, timeline, ValueInspector). | **11.2 ✓** manual |
| 12 | **Zero agent/frontend/wire change.** `KNOWN_MESSAGE_TYPES` and every generated artifact untouched all phase; `check-parity` a no-op for 11.1, 11.2, 11.H. | **11.1–11.H ✓** automated |
| 13 | **ADR discipline.** ADR-0028 ("The NN as a traceable subject") accepted; ADR count 27 → 28. | **11.H ✓** manual |
| 14 | **Cross-OS.** The nn CI leg (`ruff` + `mypy --strict` + `pytest`, `uv sync --frozen`) green on the Ubuntu + Windows matrix. | **CI ✓** automated |
| 15 | **Ship.** ADR-0028 accepted; `PHASE_11_SUMMARY.md`; `PROJECT_ACCEPTANCE.md` §G grid (28 ADRs); `CLAUDE.md` (Phase 11 shipped, Phase 12 next); version 0.11.0; tag `v0.11.0-phase-11`. | **11.H ✓** |

## Known limitations

- **Value capture is Python-only** (inherited from ADR-0025) — the whole watch-it-learn experience
  rides `sys.monitoring`; there is no Node/Go/Rust equivalent for an MLP written in those languages.
- **Trace-root discipline is manual.** Until a `grackle trace --exclude` flag exists (noted as
  future work in ADR-0028), a traceable subject must be laid out so a single `--root` excludes its
  own virtualenv; `--root src` is the demo's answer.
- **The beacon reprs are a cross-package contract.** Phase 12.4 will parse `record_architecture` /
  `record_layer_stats` reprs; changing a beacon's grammar/flatness/rounding is a breaking change to
  that future consumer, not a private refactor. Guarded by the traceability tests' exact-repr pins.
- **Per-weight data is not captured** — `record_layer_stats` reports a whole-matrix RMS, not
  per-weight values; Phase 12.4's network view will state this honesty contract in its legend.
- **The "nn never runtime-imports grackle" boundary is convention-enforced** this phase; its
  dedicated test arrives with Phase 12.1, where the agent first imports nn.

## Phase 12 preview

Phase 12 — **"grackle learns as it analyzes"** — turns the NN into grackle's own ML engine: a
`nn/ml/` self-supervised hotspot-prediction model trained from the session-store corpus via `grackle
learn`, surfaced as a capability-gated `predicted_heat` `AnalysisRegistry` entry with a
predicted-vs-actual frontend overlay — and **no wire-schema change**. It also lights up **chunk
12.4, the NetworkViewPanel**, which renders the NN as a network (neuron columns, weight bundles,
playhead-animated forward/backward sweeps) directly from this phase's `record_architecture` /
`record_layer_stats` beacons — so every Phase-11 trace (including `run-a.jsonl`) already carries the
visual's data and lights up retroactively. ADR numbers 0029–0030 are reserved but not written.
