# ADR-0030 — The Capability-Gated Inference Surface

**Status:** Accepted (implemented in Phase 12.2, 2026-08-20)
**Date:** 2026-08-20
**Phase:** 12 (12.H)

---

## Context

ADR-0029 covers `grackle_nn.ml` — the self-supervised pipeline that turns a `(graph, trace)` pair
into a trained hotspot-prediction model. This ADR covers how the **agent** consumes that pipeline:
the `grackle learn` CLI command that trains and saves a model, and the `serve`-time injection of
`metadata.predicted_heat` into every pushed graph. Both are new agent-side surfaces built on a
package (`grackle-nn`) that is, and must remain, an editable **dev-only** dependency — the agent's
runtime hard dependencies stay numpy-free, exactly as ADR-0028 established for the `nn` package's
own dependency on `grackle`. The two packages now depend on each other, in the dev group only, in
both directions.

The design question this ADR answers: how does a numpy-free production agent optionally expose an
ML feature that depends on a package it must not require? The answer is the same shape the agent
already uses for the Go and Rust toolchains — a capability gate — applied here to a Python package
import instead of an external toolchain probe.

## Decision

### 1. `ml_bridge.py`: the agent's only `grackle_nn` importer, gated like a toolchain

`packages/agent/src/grackle/ml_bridge.py` is, by its own module docstring and by a dedicated
hygiene test (`tests/test_ml_bridge_import_hygiene.py`), the **only** place in the agent package
that imports `grackle_nn` — and it does so exclusively inside function bodies, never at module
scope. Importing `grackle.cli` or `grackle.server` therefore never pulls in numpy, verified two
ways: a fresh-subprocess import check and a static AST scan across the whole agent source tree —
the mirror of ADR-0029's `nn`-side hygiene test, now closing the loop in both directions.

The gate itself deliberately mirrors the existing toolchain-capability precedent
(`go_runtime/capability.py`, and the analogous Rust/Node gates): `learn_available()`
(`@functools.cache`d, soft-imports `grackle_nn.ml` inside a `try/except Exception` — catching
*every* exception the import can raise, not just `ImportError`, because a numpy ABI mismatch or a
mid-edit syntax error in the sibling package must degrade the same way a missing package does, not
crash the CLI) plus `remediation_message()` (names the real install command — `grackle-nn` is not
on PyPI, it ships in-repo at `packages/nn`) plus `reset_cache()` (clears the cache so tests can flip
availability). The one structural difference from Go's gate: Go splits detection into two cached
probes (`go_executable`, `go_version`) composed by an uncached availability check, because a
toolchain can be "found but too old"; a Python package is simply importable or not, so
`ml_bridge.py` collapses this into one cached bool.

### 2. Absence is byte-identical — never a degraded key, never a `null`

Every failure mode — no model file, the ML gate closed, or a broken/mismatched-version model —
converges on **exactly the same outcome**: `metadata` carries only `{"hub_score", "cycles"}`, with
`predicted_heat` never added, not even as `null`. This is a stronger guarantee than "the feature
fails gracefully" — a client that has never heard of `predicted_heat` cannot distinguish "you don't
have a model" from "you have `--model` support disabled" from "your model file is corrupt," and
doesn't need to: the payload is identical in all three cases. It is enforced as an actual assertion,
not a code-review convention:
`test_predicted_heat_byte_identity_absent_vs_gate_closed_with_model`
(`tests/test_server_predicted_heat.py:111-136`) builds one server with no model configured and a
second with a real trained model but the gate force-closed (`ml_bridge.learn_available`
monkeypatched to `False`), and asserts `json.dumps(payload, sort_keys=True)` is equal byte-for-byte
between the two — not merely that both omit the key, but that the *entire serialized graph* is
identical. A companion test (`test_byte_identity_comparison_is_discriminating`) proves the
comparison would actually catch a real divergence, so the byte-identity assertion isn't vacuous.

### 3. `predicted_heat` wire shape: all nodes, never top-N

```json
{"metadata": {"predicted_heat": {"model_version": 1, "scores": [{"node_id": "...", "score": 0.8234}]}}}
```

`predict_scores(graph, model_path)` (`ml_bridge.py`) emits a score for **every** node in the graph —
deliberately not top-N truncated. The reason is specific to what this feature is for: the
interesting case is an **under-predicted "surprise hotspot"** — a node the model scored low that
actually ran hot. Top-N truncation by construction removes exactly the nodes that would reveal that
case, since a surprise hotspot may not have been in the model's own top-N. Scores are rounded to 4
decimal places (`round(float(s), 4)`) — enough precision for the frontend's ranked lists and
overlay coloring, not so much that the payload balloons; on `stress-2k` this lands the payload at
roughly one-time-per-connect, ~110 KB. `model_version` is `grackle_nn.ml.FEATURE_VERSION`
(ADR-0029 §1) — so a `predicted_heat` payload is self-describing about which feature-vector
generation produced it, without needing a second field. This is an **open metadata surface**: no
schema change, `check-parity` stays a no-op, consistent with `hub_score`/`cycles` already living on
`graph["metadata"]` unmediated by the wire schema.

### 4. Injection point: `_build_static_graph`, not just the connect-time push

`server.py`'s static-graph pipeline has one build function, `_build_static_graph`, with **two**
callers: `_push_static_graph` (the per-connection send) and `_watch_loop` (every watch-mode
rebuild, ADR-0027). `_maybe_inject_predicted_heat` is called from inside `_build_static_graph`
itself — before the `hub_score`/`cycles` enrichment block — so **both** callers carry
`predicted_heat`. Hooking only the connect-time path would have silently dropped `predicted_heat`
from every watch-mode re-broadcast, a regression that would only surface as "the overlay
disappears the moment I edit a file while `serve --watch --model` is running." A dedicated test,
`test_watch_mode_rebroadcast_carries_predicted_heat`, exists specifically because this was flagged
as the discriminating risk during design, not discovered after the fact.

Injection runs **before** the `hub_score`/`cycles` block for a second, independent reason: the
enrichment block's cache-hit path does `metadata.update(cached)`, and the cached dict only ever
contains `hub_score`/`cycles` — writing `predicted_heat` first means that `.update()` can never
clobber it on a cache hit.

### 5. Model discovery and a cache deliberately separate from `meta_cache`

Model resolution: an explicit `--model PATH` (on `grackle serve`) takes precedence; otherwise the
server `stat()`s `<root>/.grackle/heat-model.npz` **fresh on every graph build** — so retraining a
model and re-running `grackle learn` is picked up on the next push, with no server restart. The
prediction cache key is `(graph_signature, model_mtime_ns, model_size)` — a cache miss on either the
graph changing *or* the model file changing (by mtime or size), whichever happens first.

This cache is a **separate object** from `meta_cache`, not a third key layered onto it, for a
concrete reason: `meta_cache`'s cache-hit path hardcodes exactly the two keys `{hub_score, cycles}`
when copying a cached entry back onto a fresh metadata dict (see §4) — a third key living in that
same cache would be silently dropped on every hit unless that copy logic were also taught about it.
Splitting the caches means a model failure (an entry recorded in `broken_models`, so a corrupt
checkpoint isn't re-attempted on every single push) can never poison the unrelated hub-score/cycles
cache, and a retrain never forces Tarjan/hub-score to redo work it didn't need to. Both caches are
bounded (`_PREDICTED_CACHE_MAX`, `_META_CACHE_MAX`) — watch mode mints a fresh cache key per edit,
so an unbounded cache would grow for the lifetime of a long `--watch` session.

### 6. `grackle learn` CLI: one root, one graph, train-set metrics honestly labeled

```
grackle learn [TRACES...] --root R [-o MODEL] [--from-store DB] [--epochs 200] [--seed 0] [--exclude PATTERN]
```

`--root` is required (unlike `serve`'s defaulted `--root`) because the shared-root assumption is
load-bearing, not incidental: **every trace passed to one `learn` invocation is merged into a
single `Example`** (one root ⇒ one graph, ADR-0029 §5's `Example` unit). That has a direct
consequence for what the reported numbers mean — with only one graph, there is no second graph to
hold out, so `split_by_graph` (ADR-0029 §3) cannot run, and the `train_spearman`/
`baseline_spearman` figures `grackle learn` echoes are **train-set metrics**, not validation
metrics. This is stated explicitly in the summary object's docstring rather than left to be
misread as a held-out score, and a `val_spearman` field stays `null` in the on-disk
`learn-history.jsonl` report card until a real multi-root corpus exists (see Future work). Traces
may also come from `--from-store DB`, which appends every session-store recording whose
`source_path` still exists on disk (missing files are warned and skipped, not a hard error) — sugar
over the store, not a new merge semantic.

`--exclude` is accepted at parse time (so a model can be trained on a filtered subset), but `serve`
has no equivalent `--exclude` — the graph a model is later scored against at inference time is
always the full, unfiltered parse. `grackle learn` surfaces this train/serve feature skew as an
explicit runtime warning rather than silently tolerating it or attempting to fix it; fixing it
(giving `serve` a matching `--exclude`) is out of scope here (see Future work).

## Consequences

- **The inference surface can be disabled, deleted, or never installed, and the rest of grackle is
  unaffected.** Because absence is byte-identical (§2) and the import is function-body-local (§1),
  removing `packages/nn` from a checkout, or simply never running `grackle learn`, changes nothing
  about `grackle parse`/`grackle serve`'s behavior for every other feature.
- **A `predicted_heat` payload is only as trustworthy as the trace(s) it was trained on** — the
  same caveat ADR-0029 records for the label itself now applies at the product surface: a model
  trained on one narrow trace will predict that trace's hot paths, not some universal notion of
  "important code."
- **The one-graph-per-`learn`-invocation design (§6) is the direct cause of "no held-out
  validation" today** — this is a scope decision, not an oversight, and the summary object says so
  in its own words so a future reader building on `val_spearman` doesn't have to rediscover why
  it's `null`.
- **Cache correctness under watch mode is now a permanent review focus** — any future change to
  `_build_static_graph`'s caller set needs to preserve "predicted_heat reaches every caller," per
  §4's regression story.

## Constraints honored

- **No wire-schema change** — `predicted_heat` is an open metadata key (§3); `check-parity` is a
  no-op for this chunk.
- **Numpy-free agent runtime** — `grackle-nn` is a `dev`-group-only dependency
  (`[tool.uv.sources] grackle-nn = { path = "../nn", editable = true }`, plus a
  `[[tool.mypy.overrides]] module = "grackle_nn.*"` entry since the package ships its own
  `py.typed`), never in `[project.dependencies]`.
- **Capability-gate precedent (ADR-0023/ADR-0024)** — `ml_bridge.py`'s gate follows the same
  cache/remediation/reset shape as the Go and Rust toolchain gates, so a missing or broken
  `grackle-nn` install degrades exactly like a missing Go or Rust toolchain: a clean, actionable CLI
  error, never a traceback, and a silently-absent feature at serve time.
- **Cross-platform** — model files are written atomically (`.tmp` + `Path.replace()`, the
  established repo rule) and `learn-history.jsonl` is appended in binary mode with an explicit
  `\n`, matching every other JSONL emitter in the agent (`write_jsonl`, `JsonlPartWriter`).

## Future work

- **A `root` column on the session store** — the store currently records `(id, label, started_ns,
  ended_ns, source_path, event_count, language)` with no paired graph or root, so `--from-store`
  can only assume every recording shares one root (documented, not enforced). A `root` column would
  let `grackle learn` build a genuine multi-graph corpus straight from the store, at which point
  `split_by_graph` (ADR-0029 §3) becomes usable and `val_spearman` stops being permanently `null`.
- **Per-trace roots** — explicitly rejected for Phase 12 as scope creep (§6); would let one `learn`
  invocation merge traces from genuinely different projects.
- **`serve --exclude`** — the train/serve feature-skew warning (§6) is a stopgap; carried forward as
  Phase 13.0 scope, not built here.
- **A classification head, a graph-convolution layer** — carried from ADR-0029's future work; both
  would change the artifact format this ADR's caching/discovery logic depends on, so they're noted
  here too as a heads-up to whoever picks them up.
