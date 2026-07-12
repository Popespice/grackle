# ADR-0028 — The NN as a Traceable Subject

**Status:** Accepted (implemented across Phase 11.1–11.H, 2026-07-11)
**Date:** 2026-07-11
**Phase:** 11 (11.H)

---

## Context

Phase 11 ("watch it learn") adds `packages/nn/` — `grackle-nn`, a from-scratch, layer-granularity
numpy MLP (Linear/ReLU/Tanh, SoftmaxCrossEntropy/MSE, SGD/Adam, a seeded three-class spiral demo).
The point is not the model; it is that grackle can **watch it learn** using the *exact* instruments
it already uses on any other codebase — the `sys.monitoring` tracer, value capture (ADR-0025), the
time-travel `ValueInspectorPanel`, the heat-map, the flame graph, and `grackle diff` — with **zero
changes to the agent, the frontend, or the wire schema** for the entire phase (`check-parity` a
no-op every chunk).

That inverts the usual engineering problem. grackle's core needs nothing new; instead the *subject*
(the NN) must be written so its execution shape is maximally **legible** to those unchanged
instruments. A naive numpy training loop is not — nested numpy scalars leak into captured values, a
generator in a hot loop drifts `frame_depth`, per-epoch metrics have nowhere stable to be captured,
and rooting the trace at the package directory drags `.venv`/numpy into the graph. This ADR records
the six conventions the nn package holds to so it stays legible, and why each is load-bearing. They
are enforced by `packages/nn/tests/test_traceability.py` (a 14-test contract driven off a real
trace of `demo.py`, no privileged access to the training loop) — a tracer or capture regression
fails there loudly rather than silently degrading the watch-it-learn experience.

The package is `uv`-managed and standalone (not in the pnpm workspace). `grackle` is an editable,
**dev-only** dependency used exclusively by the traceability test; the reverse never holds — the
agent's hard dependencies stay numpy-free.

## Decision

### 1. Layer-granularity call shape + a pinned sizing formula

Every layer's `forward`/`backward` is a real Python method call, so one training step traces as a
fixed, human-readable sequence of `call`/`return` events — the model's structure *is* the call
tree. For the demo's five-layer net (3× `Linear`, 2× `ReLU`) one `train_step` is exactly **34
events** (17 invocations × 2): `train_step` → `Sequential.forward` → the interleaved layer
forwards → `SoftmaxCrossEntropy.forward`/`.backward` → `Sequential.backward` → the layer backwards
→ `SGD.step` → `Sequential.zero_grad` → return. This golden 34-event sequence is pinned by an
in-test literal (`_GOLDEN_34`).

The whole-run size is a closed formula, pinned against API drift:

```
total ≈ E × (S × 34 + 22) + C
```

with `E` epochs, `S = 12` batches/epoch, a **22-event per-epoch tail** (the `evaluate` chain of 9
invocations + `record_epoch` + `record_layer_stats`, ×2 events each), and a **one-time constant
`C = 30`** (imports, layer/optimizer `__init__`s, and the one-shot `record_architecture` call, ×2).
Warm (`grackle_nn` already imported): 60 epochs → **25,830** events; a cold `grackle trace` CLI run
adds ≈40 module/class-body frame events → ≈25,870. E=3 → 1,320.

The golden holds **only if** `Sequential.backward`/`zero_grad` iterate `self.layers` inline and
never call `self.parameters()`/`self.gradients()` (each such call would add 2 events → 36), and no
traced helper exists inside `train_step`'s dynamic extent (one-hot, log-sum-exp, RMS all inline
numpy). These are spec'd as hard invariants and guarded by the golden and sizing tests.

### 2. The builtin-float metric boundary (the `np.float64` dispatch gap)

Value capture's `safe_repr` (ADR-0025, `value_repr.py`) dispatches on **exact `type(x)`**. A
`numpy.float64` is *not* an exact `float`, so it misses the scalar rung and falls to the rung-8
fallback, capturing the useless `<numpy.float64 object>` instead of the number. Therefore every
value that crosses a beacon boundary — every loss, accuracy, and RMS — is wrapped to a **builtin
`float`** (`float(...)`) at the numpy edge, and epoch indices stay builtin `int`. `accuracy`,
`SoftmaxCrossEntropy.forward`, and `MSE.forward` all return `float(...)`; `train.fit` wraps each RMS
before handing it to a beacon. A regression is caught by a trace-wide scan asserting the
`numpy\.\w+ object>` fallback pattern appears nowhere in any captured arg or return
(`test_no_numpy_dtype_leakage`), and by per-beacon `ast.literal_eval` round-trip + builtin-type
assertions.

### 3. The beacon convention: identity-passthrough functions whose captured return repr is a versioned parse contract

Telemetry that has no natural call site of its own (per-epoch metrics, the one-shot architecture
string) is routed through **beacon functions** in `metrics.py` — deliberate identity passthroughs
whose only reason to exist is to give the tracer a stable `node_id` at which to capture a value.
There are three:

- `record_epoch(epoch, loss, accuracy) -> (epoch, loss, accuracy)` — once per epoch; the loss-curve
  metrics.
- `record_layer_stats(epoch, stats) -> (epoch, *stats)` — once per epoch; a **flat** tuple of each
  param-carrying layer's `(w_rms, dw_rms)` in model order.
- `record_architecture(model) -> str` — once per run; the layer stack as a space-separated token
  string, `linear:<in>:<out>` per param layer else the lowercased layer type
  (`"linear:2:32 relu linear:32:32 relu linear:32:3"`).

Each beacon's **captured return repr is a versioned parse contract** a frontend reconstruction reads
(Phase 12.4's NetworkViewPanel consumes the latter two; the ValueInspector already reads all
three). The emitter rules that keep that contract safe under the default capture limits
(`max_value_len=120`, `max_value_items=10`, `max_value_depth=3`):

- **Flat, builtin-typed tuples** (depth 1) — a nested stats tuple would sit exactly at
  `max_value_depth`, zero margin; a flat `1 + 2L`-item tuple is depth 1 and stays ≤ `max_items`
  through L=4.
- **Pre-rounded to 3 significant figures on the caller** — `float(f"{x:.3g}")` in `fit`, which is
  C-level formatting (no traced helper), keeps each RMS repr short, keeps the whole return
  untruncated (< 120 chars), and absorbs the ~1e-13 cross-BLAS float drift that would otherwise
  make a structural golden flaky.
- **The identity stays pure** — a beacon does no numpy work and makes no nested traced call, so it
  adds exactly 2 events and never perturbs the golden or the sizing constant.

**Weight-change RMS, not gradient RMS.** A per-epoch beacon fires after the epoch's last
`train_step`, whose final act is `zero_grad` — so gradients are identically zero at that point and a
per-epoch *gradient* RMS is unrecordable. `record_layer_stats` instead reports `dw_rms =
rms(W − W_prev_epoch)` from fit-local weight snapshots: honestly computable per epoch, and a better
learning signal (it decays as training converges). The snapshots are taken by iterating
`model.layers` inline (never `model.parameters()`, which would shift `C`).

### 4. Trace-root discipline — root at `src/`, never the package dir

`uv sync` creates `packages/nn/.venv`. The static walker has no default excludes and `grackle
trace` has no `--exclude` flag, so rooting a trace at `packages/nn` would parse and trace numpy's
own Python frames along with the demo. `packages/nn/src` contains only `grackle_nn/`, so
`--root src` (and `pnpm nn:trace`) puts the venv and `tests/` outside the traced root entirely. The
traceability contract pins this two ways: no `<unresolved>` frames, and every traced `node_id` is a
subset of the static graph's ids (a leaked numpy/venv/test frame would break the subset). A
`trace --exclude` flag that would make root choice forgiving is noted as future work, **not built**
in this phase.

### 5. Determinism for structural goldens

The golden 34-event sequence and the sizing constants are only stable if execution is deterministic:
a **single seeded `numpy.random.Generator`** threads all randomness (data + weight init); there are
**no generator expressions or `yield`** in traced code (a genexp creates a frame and, without
`PY_YIELD` subscription, drifts `frame_depth` by one — the tracer's documented generator limitation,
ADR-0013 — so list comprehensions and explicit loops are used throughout); and **no
data-dependent branching** exists in the traced control flow (branches depend only on model
*structure*, e.g. "is this a param-carrying layer", never on a runtime value). `demo.py`'s
accuracy-threshold demo is tuned to clear ≥0.95 with a ≥2-point margin so float64+fixed-seed drift
never flips a metric assertion.

### 6. Standalone uv package; `grackle` an editable dev-only dependency

`packages/nn` is its own `uv`-managed package with a hatchling build and a numpy-only runtime
dependency. `grackle` appears **only** in the `dev` dependency-group via
`[tool.uv.sources] grackle = { path = "../agent", editable = true }`, consumed **only** by
`test_traceability.py`. The invariant — **the nn package never runtime-imports grackle**, and the
agent's hard dependencies stay numpy-free — keeps the two halves independent: grackle can analyze
the NN without depending on it, and the NN can train without depending on grackle. A dedicated
enforcement test asserting the direction of the dependency arrives with Phase 12.1 (which begins
importing nn from the agent side and so needs the boundary made explicit); for Phase 11 the
invariant is upheld by construction and by review.

## Consequences

- **The engineering constraint lives in the subject, not the tools.** Every convention above is a
  rule the *NN* follows so unchanged grackle instruments read it cleanly. This is the whole thesis
  of the phase, and it is why Phase 11 ships with `check-parity` a no-op on every chunk.
- **The beacon return reprs are now a cross-package contract.** Phase 12.4 parses
  `record_architecture` / `record_layer_stats` reprs to render the network view; changing a
  beacon's grammar, flatness, rounding, or builtin-typedness is a breaking change to that consumer,
  not a private refactor. The docstrings say so; the traceability tests pin the exact reprs.
- **Value capture at `--capture-first-n 200` is the documented recipe** for the 60-epoch demo: the
  budget is per-`node_id` and per-*event*, so each per-epoch beacon (120 events over 60 epochs)
  needs a budget ≥ 120; 200 gives each beacon node room for 100 full invocations with headroom.
  The default 100 silently drops values past epoch 50 — pinned by a discriminating test that
  requires all 60 epochs to carry values.
- **Root-at-`src` is a papercut, not a wall.** Until a `trace --exclude` flag exists, a
  differently-laid-out traceable subject would need the same "root at a directory that excludes the
  venv" discipline; documented, accepted for this phase.
- **The nn CI leg is fully gated** (`ruff` + `mypy --strict` + `pytest`, `uv sync --frozen`) on the
  OS matrix, independent of the agent leg.

## Constraints honored

- **No wire-schema change** — `KNOWN_MESSAGE_TYPES` and every generated artifact are untouched all
  phase; `check-parity` is a no-op for 11.1, 11.2, and 11.H. The beacons ride ADR-0025's existing
  `values` field; the beacon return reprs are an *application-level* parse contract, not a wire type.
- **Open strings, not enums (ADR-0004)** — the architecture token grammar is an open string parsed
  leniently; an unknown non-`linear` token is treated as a named activation glyph, not an error.
- **numpy-free agent** — `grackle`'s hard dependencies are unchanged; numpy enters only the nn
  package's own runtime deps and the agent's *dev* environment transitively via the editable dep.
- **Cross-platform** — the demo is float64 + fixed-seed; node IDs are POSIX (`to_posix`) exactly as
  every other traced project; Ubuntu + Windows CI green.

## Future work

- **`grackle trace --exclude`** — would remove the root-at-`src` requirement (Convention 4); noted,
  not built.
- **A dependency-direction enforcement test** ("nn never runtime-imports grackle") — lands with
  Phase 12.1, where the agent begins importing nn and the boundary needs a guard rather than a
  convention.
- **Per-line local-variable capture** — would let a beacon-free training loop expose intermediate
  activations; out of scope (and a value-capture surface change, not an nn change).
