# ADR-0029 — The Self-Supervised Learning Loop

**Status:** Accepted (implemented in Phase 12.1, 2026-08-20)
**Date:** 2026-08-20
**Phase:** 12 (12.H)

---

## Context

Phase 12 ("grackle learns as it analyzes") turns the Phase-11 MLP from a traced *subject* into
grackle's own ML engine: a model that predicts which nodes in a codebase will run hot, trained
without any human-provided label. The label is self-supervised — grackle already produces it every
time someone traces their own program. The loop is: **analyze** a project (`grackle parse` → a
static graph), **trace** it (`grackle trace` → a JSONL of what actually ran), **learn** from the
pair (`grackle learn` → a `heat-model.npz` checkpoint), **predict** on graphs that have never been
traced (Phase 12.2's `predicted_heat`). Every stage of that loop is itself inspectable with
grackle's own tools — `grackle learn` calls the Phase-11 `record_epoch` beacon, so a traced
training run lights up the same `LossCurvePanel` a user would use to watch any other program.

This ADR covers the pipeline that makes the "learn" stage possible:
`packages/nn/src/grackle_nn/ml/` — a self-supervised hotspot-prediction pipeline, standalone in
the `nn` package. It converts a raw static graph into a 35-column structural feature matrix,
converts a trace into a per-node heat label, and trains the Phase-11 `Sequential` MLP architecture
to predict that label from structure alone. It never touches the agent, the frontend, or the wire
schema — `check-parity` is a no-op for the whole chunk. `packages/agent`'s capability-gated
consumption of this pipeline (`grackle learn`, `predicted_heat`) is covered separately in
ADR-0030; this ADR is about the pipeline itself.

The package continues ADR-0028's discipline: `grackle_nn.ml` never imports `grackle` at runtime —
not even under `TYPE_CHECKING` — enforced by both a fresh-subprocess import check and a static AST
scan (`packages/nn/tests/ml/test_import_hygiene.py`), the dependency-direction enforcement test
ADR-0028 named as future work for whenever the agent side began importing `nn`.

## Decision

### 1. Feature vector: 35 structural columns, computed from the raw graph dict only

`extract_features(graph: Mapping[str, Any]) -> tuple[list[str], Array]`
(`packages/nn/src/grackle_nn/ml/features.py:171`) takes a bare `grackle parse` dict — never the
server-side enriched metadata (`hub_score`/`cycles` are truncated top-N and only exist after
`enrich_metadata`, and never touch trace/heat data) — and returns `(node_ids, X)` with
`X.shape == (N, 35)`. `FEATURE_VERSION = 1` (`features.py:31`). The 35 columns
(`FEATURE_NAMES`, `features.py:41-63`):

| idx | feature | idx | feature | idx | feature |
|---|---|---|---|---|---|
| 0 | `log1p_in_degree` | 12 | `log1p_out_cross_language` | 24 | `log1p_file_fan_out` |
| 1 | `log1p_out_degree` | 13 | `log1p_out_other` | 25 | `log1p_bfs_depth` |
| 2 | `log1p_in_import` | 14 | `kind_function` | 26 | `reachable` |
| 3 | `log1p_in_call` | 15 | `kind_method` | 27 | `log1p_name_len` |
| 4 | `log1p_in_inherit` | 16 | `kind_class` | 28 | `is_dunder` |
| 5 | `log1p_in_implements` | 17 | `kind_file` | 29 | `is_private` |
| 6 | `log1p_in_cross_language` | 18 | `kind_other` | 30 | `is_test` |
| 7 | `log1p_in_other` | 19 | `in_cycle` | 31 | `is_async` |
| 8 | `log1p_out_import` | 20 | `log1p_scc_size` | 32 | `has_decorators` |
| 9 | `log1p_out_call` | 21 | `in_degree_percentile` | 33 | `log1p_path_depth` |
| 10 | `log1p_out_inherit` | 22 | `out_degree_percentile` | 34 | `log1p_line` |
| 11 | `log1p_out_implements` | 23 | `log1p_file_fan_in` | | |

Edge kinds bucket into six groups (`_EDGE_BUCKETS`, `features.py:38`): exact match for
`import`/`call`/`inherit`/`implements`; anything **starting with** `"cross_language"` (e.g.
`cross_language_http_route`) collapses to bucket 4 — an open string treated leniently, per
ADR-0004; everything else (interface/type_alias/enum/struct edges, any future kind) falls to
`"other"`. Node kinds bucket analogously into five one-hot columns (`function`/`method`/`class`/
`file`/`other`). **A feature reorder or column-set change requires a `FEATURE_VERSION` bump** — the
column layout is baked into every trained checkpoint (D12.6, ADR-0030) and every consumer that
standardizes against it; the rule is stated in a source comment next to `FEATURE_NAMES` and pinned
by a test asserting the entire 35-column row for two hand-computed nodes against a literal.

### 2. Label: per-graph max-normalized log heat, single MSE regression head

`make_targets(node_ids, heat) -> Array` (`labels.py:81`) computes
`y = log1p(count) / log1p(max_count_in_graph)`, zeros when the graph's max is zero
(`labels.py:97-102`) — bounded to `[0, 1]`, cross-trace comparable (a graph with one 10,000-call
hotspot and a graph with one 10-call hotspot both normalize their hottest node to `1.0`), and
exactly the normalization Phase 12.3's frontend vs-actual comparison reuses independently in
TypeScript (`predictedHeat.ts`'s `anorm`).

**Rejected alternatives, and why:**
- **Per-training-set z-score labels** — would make the label's scale depend on which graphs
  happen to be in the training corpus, so the same node's target shifts every time the corpus
  changes; per-graph max-normalization keeps the label a property of the graph alone.
- **A binary "touched" classification head (BCE)** — the pipeline already has one grad-checked
  loss (`MSE`, shipped and gradient-verified since Phase 11.1) and no `SoftmaxCrossEntropy`/BCE
  variant built for this shape; adding one is a new loss implementation *and* a new gradient check
  for a marginal gain on genuinely small data, since "touched" is already derivable from the
  regression output by thresholding. Rejected as scope creep, recorded here rather than left
  unexplained in the source.

**Both `y`'s numerator and denominator go through `np.log1p` — never a `math.log1p`/`np.log1p`
mix.** They are different implementations (numpy's own vs. the platform libm) that can disagree by
1 ULP; on at least one Linux CI runner that made the hottest node normalize to
`1.0000000000000002`, one ULP over the documented `[0, 1]` bound, and only on that platform (it
never reproduced locally, so it first read as a flake). Using one implementation for both makes the
max element cancel to exactly `1.0` by IEEE-754 construction (`a / a == 1.0` for any finite
non-zero `a`) on every platform. Pinned by a test parametrized over ten `max_count` magnitudes
asserting *exact* equality, not a tolerance — "the bound is a documented contract, and a tolerance
would have hidden this" (`labels.py:88-95`, `tests/ml/test_labels.py:69-84`).

### 3. Split by whole graph, never by node — the anti-leakage rule

`split_by_graph(examples, *, val_count, rng)` (`dataset.py:84`) permutes **example** indices
(`rng.permutation(n)`), never row indices — a graph's nodes always move together to train or to
validation, so no node's structural neighborhood leaks across the split boundary. This is executed
as a test, not just documented: `test_split_by_graph_never_straddles_a_graph`
(`tests/ml/test_dataset.py:121`). `Example` (`dataset.py:29-37`) is the corpus's in-memory unit —
`(name, node_ids, x: (N,35) raw, y: (N,), counts: (N,))` — one per graph.

### 4. Metrics from scratch: rank correlation, not accuracy

`spearman(a, b) -> float` and `top_k_overlap(a, b, k) -> float` (`metrics_rank.py`) are pure numpy
— no scipy. Rank correlation, not a pointwise error metric, is the right measure here: the
consumer (`predicted_heat`, the frontend overlay) cares about relative ordering — "which nodes are
hottest" — not the exact predicted count. Tie-averaged ranks (`_ranks`, shared with `features.py`'s
percentile column via an extracted `_rank_utils.tie_averaged_ranks_0indexed` helper — the two call
sites' tie-breaking logic had briefly diverged into two copies during review and was consolidated
into one). Two deliberate zero-not-NaN cases, both documented and tested: **empty input** returns
`0.0` (nothing to correlate — numpy would otherwise emit `NaN` with a `RuntimeWarning`); **constant
input** (`std == 0`) also returns `0.0` rather than propagating a `NaN` from a zero-variance
Pearson correlation. `top_k_overlap` breaks ties via a stable descending argsort — deterministic
regardless of the input's tie order, `k <= 0` raises, `k` larger than the array clamps.

**The baseline is raw in-degree (feature column 0)** — the cheapest structural signal that exists
before any training. The whole acceptance story (§6) is "does the learned model beat this," not
some absolute accuracy bar; a from-scratch model that can't beat looking at fan-in isn't earning
its training cost.

### 5. Corpus: raw `(graph, trace)` pairs on disk; features extracted at learn time

`load_corpus_dir` reads a directory of `{name}.graph.json` + `{name}.trace.jsonl` pairs
(`dataset.py:61`) — the **raw** pair, not a pre-extracted feature dump. `grackle learn` (ADR-0030)
builds `Example`s in memory from a parsed graph and `TraceAggregates` heat; the corpus format never
bakes in a feature layout. The reason is D12.1's own versioning rule: an `.npz` feature dump would
fossilize `FEATURE_VERSION=1` forever, silently going stale the moment the column set changes,
whereas re-featurizing raw pairs at load time survives every future `FEATURE_VERSION` bump for
free.

### 6. Data-regime honesty: a seeded synthetic acceptance bar, a real-fixture smoke test — never conflated

Only ~2 real Python trace-bearing fixtures exist in the repo. A 2-graph Spearman comparison between
model and baseline is noise, not evidence — an early design that tried to discriminate on real
fixtures alone was a flake trap. The pipeline splits the two roles cleanly:

- **The discriminating acceptance bar runs on a seeded synthetic corpus**
  (`tests/ml/synth.py`, test-only, never shipped under `src/`): 8 graphs (60–120 nodes,
  preferential-attachment call/import edges, one planted 3–5-node cycle), with ground-truth heat
  `count = max(round(expm1(1.5·log1p(in_call_deg) + 1.2·in_cycle + 0.5·is_method − 1.5·is_test +
  N(0, 0.3))), 0)`, forced to `0` wherever unreachable. The formula is built from `extract_features`'s
  own columns and *deliberately* carries non-degree signal (`in_cycle`, `is_method`, `is_test`) so
  a pure in-degree ranking is beatable **on principle**, not by luck. Split
  `val_count=2, seed=0` → train 300 epochs, seed 0 → assert
  `mean(model_spearman) >= mean(baseline_spearman) + 0.05` **and** `mean(model_spearman) > 0.5`
  over the 2 held-out graphs. A companion mutation test shuffles held-out labels and asserts the
  bar then fails — proof the bar isn't vacuously satisfiable. All seeded float64, so it's stable
  cross-OS; margins were measured locally across the CI matrix before the bar was picked, and per
  an explicit escape hatch, a future flake lowers the documented bar rather than unseeding anything.
- **Real fixtures get a smoke test, not an acceptance bar** — Python's `tiny-python-app` (the
  committed golden trace, zero tracing cost) plus the nn demo itself (parsed + traced at
  `NN_DEMO_EPOCHS=3`), asserting only: loss decreases, train-set Spearman on the demo exceeds 0.5,
  no NaNs — **no held-out baseline comparison**, because 2 graphs cannot support one. The polyglot
  Go/Rust/Node goldens are included here too, proving the pipeline is language-agnostic even though
  only Python fixtures currently carry real traces.

### 7. Cycle detection: an independent, iterative Tarjan reimplementation — not a shared import

`_tarjan_scc(adj, n)` (`features.py:110-168`, ~50 lines of algorithm body) is an explicit-stack
iterative Tarjan, chosen over Python recursion for graphs with long chains (recursion-depth safety)
and chosen as a **reimplementation** — independent of `graph_analysis.py`'s `_compute_cycles`, which
this module never imports, per the "`grackle_nn.ml` never imports `grackle`" iron rule (§ Context).
Self-loops are excluded from the adjacency and tracked separately via a boolean array, matching the
agent's algorithm exactly. A dev-only cross-check test (`grackle` importable in tests, never in
`src/`) parses a real fixture through both implementations and asserts agreement on cycle
membership — the two Tarjans are independent code, but not independently *trusted*; they're pinned
against each other. The BFS-from-entry-set logic (columns 25–26) shares the same discipline: the
entry set is every file node **or** every node with zero call/import in-degree, walked over
call+import edges only. Self-loops are excluded from `call_import_in_degree` specifically so a
self-recursive node with no other caller still qualifies as an entry point — an asymmetry between
"how the in-degree is counted" and "what the traversal can actually reach" that was found and fixed
during this chunk's adversarial review (a self-recursive node with no other callers was being
wrongly excluded from the BFS entry set), tested at
`test_self_loop_only_node_is_still_an_entry_point`.

### 8. Labels mirror the agent's own aggregation exactly, pinned by a cross-trace-golden tripwire

`heat_from_jsonl(path) -> dict[str, int]` (`labels.py:52`) is an **intentional line-for-line mirror**
of `packages/agent/src/grackle/python_runtime/aggregates.py`'s `_event_weight`/
`cumulative_heat_all` — same weight semantics (non-dict metadata → 1, `bool` explicitly rejected
even though `bool` is an `int` subclass, `int` floored at 1, finite `float` truncated then floored
at 1, everything else → 1), same "counts calls and returns" behavior. This is not merely commented
as intentional — it is pinned as an executable contract:
`test_heat_from_jsonl_matches_trace_aggregates_cumulative_heat_all`
(`tests/ml/test_labels.py:42-53`) runs `heat_from_jsonl` and the agent's live `TraceAggregates`
against the **same golden trace file** for all 5 of the repo's committed golden fixtures (Python,
Node, Go, Rust, and the value-capture fixture) and asserts byte-for-byte dict equality. If the two
implementations ever drift — a weight-semantics change on one side without the other — this test
fails immediately rather than silently training on a label that no longer means what
`predicted_heat`'s vs-actual comparison (ADR-0030, Phase 12.3) assumes it means.

### 9. The trainer reuses the Phase-11 `record_epoch` beacon — "grackle watches itself learn"

`train_heat_model` (`heat_model.py:173-181`) trains the **verbatim** Phase-11 architecture —
`Sequential(Linear(35,64), ReLU(), Linear(64,32), ReLU(), Linear(32,1))`, He init, `Adam`, default
200 epochs — in its own loop (not `grackle_nn.train.fit`, which is shaped for classification), and
calls `record_epoch(epoch, float(train_loss), float(val_loss))` (`heat_model.py:231`) once per
epoch, reusing ADR-0028's identity-passthrough beacon with `val_loss` in the "accuracy" slot. The
payoff: tracing a `grackle learn` invocation lights up the same `LossCurvePanel` a user would use
on any other traced program — grackle's own training loop is inspectable with grackle's own tools,
the same thesis Phase 11 established for the demo, now applied to the tool learning about the
user's own code. One seeded `np.random.default_rng(seed)` threads every `Linear` init and every
epoch's minibatch shuffle (D10 discipline, ADR-0028 §5), so a run is fully reproducible from `seed`
alone. Standardization stats (`mean`, `std` floored at `1e-8`) are computed once on the training
set and stored in the checkpoint (ADR-0030 §"Artifact format") — `predict` always standardizes
against the *stored* stats, never against whatever batch it's currently scoring, verified by
`test_predict_uses_stored_norm_stats_not_recomputed_from_input`.

## Consequences

- **The pipeline is a closed, independently-testable unit.** 84 test functions across 8 files
  (`packages/nn/tests/ml/`) cover every module; none of them requires the agent or a live server.
  `grackle_nn.ml` can be developed, tested, and reasoned about without touching `packages/agent`.
- **The label is only as good as the trace it comes from.** A short or unrepresentative trace
  produces a label that reflects that run, not the program's general hot paths — this is inherent
  to any self-supervised signal derived from observed execution, not a defect in the pipeline;
  documented here so a future reader doesn't mistake `predicted_heat` for a claim about worst-case
  or adversarial-input behavior.
- **Feature/label leakage is a permanent review focus**, not a one-time check — any future change
  to `dataset.py`'s split logic needs to preserve the whole-graph-moves-together invariant, and the
  executable test (§3) is the guard, not the docstring.
- **The synthetic acceptance bar is deliberately not chasing state-of-the-art.** `0.5` absolute
  Spearman and a `0.05` margin over baseline are modest, chosen to be stable across three OSes and
  BLAS implementations rather than to showcase headline accuracy — the point of Phase 12 is the
  closed loop (analyze → trace → learn → predict, inspectable end-to-end), not a competitive model.

## Constraints honored

- **No agent/frontend/wire-schema change** — `grackle_nn.ml` is standalone `nn`-package code;
  `check-parity` is a no-op for this chunk.
- **`grackle_nn.ml` never imports `grackle` at runtime** — enforced by a fresh-subprocess check and
  a static AST scan (not a regex — an earlier regex-based hygiene check missed comma-separated and
  aliased import forms, replaced with `ast.parse`-based scanning during this chunk's review).
- **Open strings, not enums (ADR-0004)** — the edge-kind and node-kind bucketing is prefix/exact
  match against open strings; an unrecognized kind falls to the `other` bucket rather than raising.
- **Cross-platform** — every random number in training and data generation flows through one seeded
  `numpy.random.Generator`; the `np.log1p`-only rule (§2) is specifically a cross-platform fix.

## Future work

- **A classification ("touched") head** — rejected for Phase 12 as scope creep on small data (§2);
  would need its own loss + gradient check.
- **A graph-convolution layer** (`H' = act(Â·H·W)`) in the same `Layer` protocol, using edges as a
  first-class training signal rather than only pre-aggregated structural features — noted in the
  Phase 12 plan as a stretch goal, not scheduled.
- **A larger real-trace corpus** — the synthetic/real split (§6) is a data-regime honesty measure
  given today's ~2 real trace-bearing fixtures; as more real traces accumulate (e.g. via the
  session store, ADR-0030 future work), the acceptance bar could migrate toward real data.
