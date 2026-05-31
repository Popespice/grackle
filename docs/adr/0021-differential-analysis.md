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
   `diffTraceVsTrace(diffBaseline, currentCounts)` and updates the graph overlay.

`agentHeat` is preferred over `traceEvents` counting for seekable sessions (it
reflects the full-session aggregate, not just the current window).

### Graph colour overlay

`DiffPanel` sets `diffOverlay: Map<string, DiffStatus> | null` in the store.
`GraphCanvas.makeNodeReducer` applies it as a new branch in the existing colour
cascade — between `dimmed` and `heat`:

```
highlighted → dimmed → diff overlay → heat → kind color
```

Diff colours (all hex — ADR-0015):

| status  | color   |
|---------|---------|
| hotter  | #ef4444 |
| new     | #22c55e |
| colder  | #3b82f6 |
| gone    | #6b7280 |
| cold    | #f59e0b |
| touched | #10b981 |
| same    | (kind color — no override) |

The overlay is cleared on new session start and on graph change.

### CLI

```
grackle diff A.jsonl B.jsonl [--format text|json] [--only STATUS]
```

- Default output: human-readable summary with counts per status and a table of
  changed nodes.
- `--format json`: full `list[DiffEntry]` to stdout (machine-readable).
- `--only STATUS`: filter output to a single status (e.g. `--only hotter`).
- Exit 0: no regression.  Exit 1: at least one `hotter` node.

Both files are scanned via `TraceAggregates.build()` (single-pass, no server needed).

### SessionLibraryPanel registration

`SessionLibraryPanel` (shipped in 8.3) was missing from `init.ts`.  It is registered
here as `left-sidebar` order 5, below `search-filter`.

---

## Consequences

- `diff.py` and `diff.ts` are pure — easy to test, no server dependency.
- `grackle diff` is CI-usable without a running server.
- The frontend baseline approach is simple but ephemeral (not persisted across
  page reloads).  A future improvement could save the baseline to `sessionStorage`
  or the server-side session store.
- Diff overlay displaces heat overlay when active.  Users who want heat while a
  diff baseline is set must click "Clear overlay".  This is acceptable given the
  low frequency of simultaneous use.
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
