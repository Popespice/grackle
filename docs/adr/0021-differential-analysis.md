# ADR-0021 — Differential Analysis Model

**Status:** Accepted  
**Date:** 2026-05-31  
**Phase:** 8.4

---

## Context

Phase 8.3 shipped `TraceAggregates` — a per-node sorted hit-list index that answers
cumulative heat and coverage queries over a seekable session in O(log N).  The natural
next step is to _compare_ sessions: "which nodes got hotter since last week?", "what
code never ran in this test run?", "did my optimisation make things colder or just
shift work elsewhere?".  Two orthogonal diff modes cover the practical use-cases:

1. **Trace-vs-static** — how much of the static graph did a single run cover?
   Every node is classified `touched` (≥1 hit) or `cold` (0 hits).  This is
   "coverage gap / dead code detection".  `runtimeCoverage.ts` already computed
   `touched` and `cold` sets; this ADR formalises the shape and adds a graph overlay.

2. **Trace-vs-trace** — how did a node's call count _change_ between two runs?
   Each node is classified `new` / `gone` / `hotter` / `colder` / `same` by comparing
   cumulative hit counts at the full-session index.  `hotter` is treated as a
   regression signal; `gone` and `new` indicate coverage drift.

A headless `grackle diff A.jsonl B.jsonl` CLI command makes the regression signal
CI-usable: exit 0 = no regression, exit 1 = at least one `hotter` node.

---

## Decision

### Data model

Both modes share a single `DiffEntry` shape:

```
{ node_id, status, count_a, count_b, delta }
```

`status` is an open string (ADR-0004) drawn from:
`"touched" | "cold" | "new" | "gone" | "hotter" | "colder" | "same"`

**Python** (`grackle.diff`):
- `diff_trace_vs_static(node_ids, aggregates, at_index?) → list[DiffEntry]`
- `diff_trace_vs_trace(agg_a, agg_b, node_ids?, at_index_a?, at_index_b?) → list[DiffEntry]`
- `has_regression(entries) → bool` — true iff any entry is `"hotter"`

**TypeScript** (`graph/diff.ts`):
- `diffTraceVsStatic(graph, coverage) → DiffEntry[]`
- `diffTraceVsTrace(countsA, countsB, graphNodeIds?) → DiffEntry[]`
- `diffToOverlay(entries) → Map<string, DiffStatus>` — for GraphCanvas
- `diffCounts(entries) → Record<DiffStatus, number>` — for DiffPanel summary

Both implementations are **pure functions** (no I/O, no side effects).  Callers own
the file-scan and aggregates-build lifecycle.

### Classification rules

| count_a | count_b | status  |
|---------|---------|---------|
| 0       | > 0     | new     |
| > 0     | 0       | gone    |
| n       | > n     | hotter  |
| n       | < n     | colder  |
| n       | = n     | same    |

Threshold for "regression": any `hotter` node (delta > 0).  No percentage-based
threshold was added; this can be a future CLI flag if needed.

Output is sorted by severity: `hotter → new → gone → colder → same`, then
alphabetically by `node_id` within each bucket for determinism.

### Baseline snapshot (frontend)

Trace-vs-trace on the frontend requires two sessions.  Rather than a complex
two-session picker, the panel uses a lightweight **baseline snapshot**:

1. The user loads a trace session and clicks **"Set as baseline"** in `DiffPanel`.
2. The current per-node counts (`agentHeat ?? countEvents(traceEvents)`) are stored
   in `useGraphStore` as `diffBaseline: Record<string, number> | null`.
3. On the current or any subsequent session, `DiffPanel` computes
   `diffTraceVsTrace(diffBaseline, currentCounts)`.

`agentHeat` is preferred over `traceEvents` counting for seekable sessions (it
reflects the full-session aggregate, not just the current window).

The baseline is **session-independent but graph-scoped**: it deliberately
persists across trace sessions (that is the whole point of comparing two runs of
the same project), and is cleared by `setGraph` because its node IDs are only
meaningful within one static graph — a baseline carried into a different graph
would classify its now-absent nodes as phantom `gone` entries.

### Graph colour overlay (opt-in)

`DiffPanel` writes `diffOverlay: Map<string, DiffStatus> | null` to the store.
`GraphCanvas.makeNodeReducer` applies it as a branch in the colour cascade,
**before** `heat`:

```
highlighted → dimmed → diff overlay → heat → kind color
```

Because the overlay sits before heat, a non-null overlay fully suppresses the
Phase-6 runtime heat-map.  To avoid silently hijacking heat the moment a session
loads, painting the graph is **opt-in**:

- The panel always shows the diff *summary* (status chips + node lists).
- The graph overlay is written only when the user enables it via the **"Show
  overlay"** toggle.  Clicking **"Set as baseline"** auto-enables it (an explicit
  request to see the diff).  Default is off → heat remains the default view.
- The store write is **debounced (150 ms)**, mirroring `TimelinePanel`'s
  cumulative-heat query, so live streaming (which grows `traceEvents` and
  recomputes the diff every batch) does not reset Sigma's `nodeReducer` per frame.

Diff colours (all hex — ADR-0015) live in `graph/diff.ts` as
`DIFF_STATUS_COLORS`, the single source shared by `GraphCanvas` (overlay) and
`DiffPanel` (chips) so they cannot drift:

| status  | color   |
|---------|---------|
| hotter  | #ef4444 |
| new     | #22c55e |
| colder  | #3b82f6 |
| gone    | #6b7280 |
| cold    | #f59e0b |
| touched | #10b981 |
| same    | (kind color — no override) |

The overlay is cleared on new session start, on graph change, and on panel unmount.

### CLI

```
grackle diff A.jsonl B.jsonl [--format text|json] [--only STATUS]
```

- Default output: human-readable summary with counts per status and a table of
  changed nodes.
- `--format json`: full `list[DiffEntry]` to stdout (machine-readable).
- `--only STATUS`: filter the displayed table to a single status (e.g. `--only hotter`).
  The summary counts and the exit code always reflect the **full** set, so when
  `--only` hides the hotter rows the output prints an explicit note that the
  non-zero exit is due to hidden regressions.
- Exit 0: no regression.  Exit 1: at least one `hotter` node.

Both files are scanned via `TraceAggregates.build()` (single-pass, no server needed).

### SessionLibraryPanel registration

`SessionLibraryPanel` (shipped in 8.3) was missing from `init.ts`.  It is registered
here as `left-sidebar` order 5, below `search-filter`.

---

## Amendment — Phase 9.3 (2026-06-30)

The frontend baseline now persists to `sessionStorage`, closing the "ephemeral" limitation noted below. `packages/frontend/src/graph/diffBaselinePersistence.ts` keys each entry `grackle:diff-baseline:<graphCacheKey(graph)>` — `graphCacheKey` (an existing, previously-unconsumed SHA-256 content hash over the graph's nodes/edges) keys persistence **per project** so a baseline from one project never restores onto another.

Persistence is deliberately driven from `DiffPanel`'s "Set as baseline" / "Clear baseline" click handlers, never from a store subscriber on `diffBaseline`: `setGraph` unconditionally clears `diffBaseline` to `null` on every `static_graph` push (the existing graph-scoped invariant above), and a blind subscriber would observe that clear and delete the persisted entry before a restore effect could read it back. A separate `useEffect` keyed on `[graph]` restores from storage after each graph (re)load, guarded so it neither clobbers a baseline the user just set (`diffBaseline === null` check) nor applies a stale resolution from a since-replaced graph (`useGraphStore.getState().graph === graph` identity check). No wire-schema change, no store-shape change.

## Consequences

- `diff.py` and `diff.ts` are pure — easy to test, no server dependency.
- `grackle diff` is CI-usable without a running server.
- **(Phase 9.3)** The frontend baseline now persists to `sessionStorage`, keyed
  per project — see the Amendment above. It still does not persist to the
  server-side session store; that remains a possible future improvement.
- The diff overlay displaces the heat-map when painted, so painting is opt-in
  (the "Show overlay" toggle) and the heat-map stays the default.  The trade-off
  is one extra click to see the graph coloured by the diff; the summary chips and
  node lists are always visible without it.
- `diffTraceVsStatic` sets `countA`/`countB`/`delta` to 0 (the status carries the
  signal): `RuntimeCoverage` exposes set membership, not per-node counts, so a
  synthetic count would read as a real hit count to any consumer that rendered it.
- The `node_ids` property added to `TraceAggregates` is public API; it returns a
  `frozenset[str]` of all node IDs observed in the trace.

---

## Alternatives considered

**Percentage threshold for "hotter"** — rejected as premature; most CI uses want
"any increase = regression" semantics, and a threshold can be a future `--threshold`
flag without touching the data model.

**Two-session picker UI** — rejected in favour of the baseline snapshot.  The picker
would require holding two sessions simultaneously in the store, complicating state
management.  The snapshot is simpler and covers the primary use case.

**New wire message pair (`diff_request` / `diff_response`)** — rejected; the diff
computation is fast (O(N nodes)) and the frontend has `agentHeat` already.  Adding a
round-trip would complicate the protocol without a performance benefit.
