# grackle-nn

A from-scratch, layer-granularity numpy MLP — designed to be legible to grackle's own
tracer, time-travel debugger, heat-map, and diff tooling. The point isn't the model (a
small MLP on synthetic spirals); it's that grackle can watch it learn using the exact
same instruments it uses to analyze any other codebase, with zero changes to grackle
itself.

Layers, losses, optimizers, and the training loop are all implemented here without a
deep-learning framework: `Linear` / `ReLU` / `Tanh` layers, `SoftmaxCrossEntropy` / `MSE`
losses, `SGD` / `Adam` optimizers, and a `Sequential` model container.

## Watch it learn

`src/grackle_nn/demo.py` trains the MLP on a synthetic three-class spiral dataset for 60 epochs.
Training routes its telemetry through a small family of **beacon functions** in
`grackle_nn/metrics.py` — deliberate identity passthroughs that exist solely so grackle's tracer
has a stable place to capture values:

- `record_epoch(epoch, loss, accuracy)` — once per epoch, the loss-curve metrics.
- `record_layer_stats(epoch, stats)` — once per epoch, each layer's weight RMS and per-epoch
  weight-change RMS, as a flat pre-rounded tuple.
- `record_architecture(model)` — once per run, the layer stack as a token string
  (`"linear:2:32 relu linear:32:32 relu linear:32:3"`).

Trace the demo with `--capture-values` and you can scrub through an entire training run in
grackle's frontend, exactly like debugging any other traced program. Each beacon's captured
return repr is a small, versioned parse contract — flat, builtin-typed, and short enough to stay
untruncated under the default capture limits.

### 1. Install

```bash
cd packages/nn
uv sync
```

### 2. Trace the demo

```bash
uv run grackle trace src/grackle_nn/demo.py --root src --capture-values --capture-first-n 200 -o run-a.jsonl
```

Or, from the repo root: `pnpm nn:trace`.

**Why `--capture-first-n 200`?** The capture budget is spent per *event* and counted
**per node** — one epoch's `record_epoch` call and return together cost 2 against
`record_epoch`'s own budget, and `record_layer_stats` has its own separate budget. Each
per-epoch beacon fires 60 times (120 events); the default budget (100) captures only the first
50 epochs before values silently stop (the call/return events themselves are still emitted —
only the *values* drop). `200` gives each beacon node room for 100 full invocations,
comfortably covering this demo's 60 epochs with headroom.

**Why `--root src`, not `--root .`?** `uv sync` creates `packages/nn/.venv`. grackle's
walker has no default excludes and `trace` has no `--exclude` flag, so rooting at
`packages/nn` would parse and trace numpy's own Python frames along with the demo.
`packages/nn/src` contains only `grackle_nn/`, so the venv and `tests/` sit outside the
traced root and are skipped entirely.

A 60-epoch run produces roughly 25,900 events in well under a second.

### 3. Browse it live

```bash
uv run grackle serve --root src --trace-source run-a.jsonl
```

...and in another terminal, from the repo root:

```bash
pnpm --filter @grackle/frontend dev
```

Open the printed frontend URL. You'll see:

- The static graph of `grackle_nn` itself (layers, losses, optimizers, training loop).
- The **heat map** — `Linear.forward` lights up hottest (it runs 3× per batch, 12
  batches/epoch, 60 epochs).
- The **flame graph** — `fit` → `train_step` → `forward` / `backward` / `step`, one
  frame per batch.
- **Timeline** scrub + the **value inspector** on `record_epoch` — step through
  training and watch loss fall and accuracy climb, epoch by epoch, straight from
  captured values (no separate logging). Scrub to any epoch and inspect
  `record_layer_stats` the same way to read each layer's weight magnitude and how much it
  moved that epoch.

Scrubbing the timeline is a better way to explore a run than 1× playback — jump
straight to the epochs you care about.

### 4. Diff two runs (counts)

Trace a shorter run and diff it against the first:

```bash
# bash/zsh
NN_DEMO_EPOCHS=30 uv run grackle trace src/grackle_nn/demo.py --root src --capture-values --capture-first-n 200 -o run-b.jsonl
# cmd.exe
set NN_DEMO_EPOCHS=30 && uv run grackle trace src/grackle_nn/demo.py --root src --capture-values --capture-first-n 200 -o run-b.jsonl
# PowerShell
$env:NN_DEMO_EPOCHS=30; uv run grackle trace src/grackle_nn/demo.py --root src --capture-values --capture-first-n 200 -o run-b.jsonl

uv run grackle diff run-a.jsonl run-b.jsonl
```

Every node on the training path (layers, losses, optimizer, `train_step`) roughly
halves in count going from 60 to 30 epochs; one-time setup nodes (module imports,
`__init__`s) are unchanged. Changing only the learning rate, by contrast, produces no
count diff at all — same call shape, different values — which is why step 5 compares
*values*, not counts.

### 5. Compare learning curves (values)

Re-trace with a different learning rate:

```bash
NN_DEMO_LR=0.05 uv run grackle trace src/grackle_nn/demo.py --root src --capture-values --capture-first-n 200 -o run-c.jsonl
```

`--trace-source` replays one file per server, so compare sequentially: load
`run-a.jsonl` and note the `record_epoch` trajectory in the value inspector, then
restart `grackle serve` with `--trace-source run-c.jsonl` (or run a second server on
a different `--port`) and compare — same call shape (`grackle diff` would report
"same" for every node), different learning dynamics.
