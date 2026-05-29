# ADR-0018 — Server-side aggregation engine

**Status:** Accepted  
**Date:** 2026-05-29  
**Phase:** 8.3

---

## Context

Phase 7.3 added server-side seek (ADR-0017): the browser requests windows of events on demand instead of holding the full trace in memory.  A documented limitation was left open: **cumulative heat and runtime coverage still require the browser to buffer all events**, because both metrics need the full event history up to the current playhead, not just the current window.  At 1M+ events this means the browser either holds a huge array or the scrubber only shows heat for the visible window — neither is acceptable.

Two additional Phase-7 debts closed here (per ADR-0017 "Phase 8 candidates"):
- **Double-scan on startup**: `JsonlIndex.build` scans the file once; `_replay_trace` in non-seekable mode calls `read_jsonl` which scans again. Seekable mode already avoided this; the aggregation path is always seekable, so the debt is addressed.
- **Agent-side analysis**: ADR-0008 reserved the path of running hub-score and Tarjan SCC on the agent when graphs grow large.  Now that the agent already parses the static graph on every connect, injecting results into `graph.metadata` costs nothing additional.

---

## Decision

### `TraceAggregates` — one-pass index over hit events

`packages/agent/src/grackle/python_runtime/aggregates.py` builds a compact in-memory structure from a JSONL trace file:

- **Per-node hit list** (`dict[str, list[int]]`): for each `node_id`, a sorted list of event indices where an event occurred.  Built in a single forward pass; O(total_events) time and space.
- **First-seen index** (`dict[str, int]`): `node_id → first event index`.  Used for coverage queries.
- **Coverage sorted list**: `sorted_first_seen: list[int]` — the sorted first-seen indices for all nodes.  `coverage_count(at_index)` = `bisect_right(sorted_first_seen, at_index - 1)`.

Query complexity: `cumulative_heat(node_id, at_index)` = `bisect_right(hit_list[node_id], at_index - 1)` — O(log N) where N = events for that node.  `coverage_count` = O(log M) where M = distinct nodes.  `top_k(k, at_index)` = O(M log M) worst case (small M in practice).

**Sparse index option** (`sparse_k > 1`): only record every K-th event index per node. Reduces memory by ~K× at the cost of approximate counts (floor to nearest multiple of sparse_k). Not used by default; exposed for profiling if 10M+ event traces become common.

### `trace_query_request` / `trace_query_response` — new message pair

Added to `messages.schema.json` and `messages.ts`. `KNOWN_MESSAGE_TYPES` 12 → 17 (aggregation + session library in one codegen pass — see ADR-0020).

```
trace_query_request  { session_id, kind, at_index, k? }
trace_query_response { session_id, kind, at_index, data, error? }
```

`kind` values (open string per ADR-0004):
- `"cumulative_heat"` — `data = { node_id: count, … }` for all nodes with count > 0
- `"coverage"` — `data = { count: N }` where N = distinct nodes seen by at_index
- `"top_k"` — `data = { entries: [{ node_id, count }, …] }`, up to k entries descending

The server handles `trace_query_request` in `_receive_loop` identically to `trace_seek_request`: I/O is offloaded to `run_in_executor`, errors return an error-flagged `trace_query_response` (not a separate error type — the response payload carries an optional `error` field).

### Frontend integration

- `useGrackleClient`: `requestTraceQuery(sessionId, kind, atIndex, k?)` — mirrors `requestTraceWindow` (pending-map + 5 s timeout).
- `useGraphStore`: `agentHeat: Record<string, number> | null` + `setAgentHeat` / `clearAgentHeat`.
- `useHeatmap`: in seekable + cumulative mode, returns `agentHeat` directly (O(M) Map construction) instead of running `computeHeat` over the window (which is only accurate for the window, not the full trace).
- `TimelinePanel`: `useEffect` on `[traceSeekable, traceHeatMode, traceSessionId, tracePlayhead]` fires `requestTraceQuery(..., "cumulative_heat", playhead)` and calls `setAgentHeat`.

### Agent-side graph analysis (`graph.metadata`)

`packages/agent/src/grackle/graph_analysis.py` — `enrich_metadata(graph)` injects hub-score and cycle data before `static_graph` is pushed to each client:

```python
graph["metadata"]["hub_score"]  # list[{node_id, score}] top-50, descending
graph["metadata"]["cycles"]     # list[{id, nodes, size, edge_kinds}] SCCs > 1, max 100
```

Frontend: `graph/analysis/index.ts` checks `graph.metadata?.hub_score` / `graph.metadata?.cycles` before running local compute.  Local compute remains as a fallback for graphs without metadata (e.g. loaded from a file replay where the agent version pre-dates 8.3).

---

## Consequences

**Positive:**
- Cumulative heat in seekable mode is now accurate over the full trace, not just the current window — closes the Phase-7.3 documented limitation.
- Hub-score and cycle detection run once on the agent, not re-derived in every browser tab.
- `top_k` query enables a "hot functions" summary without shipping all events to the browser.

**Negative / known limits:**
- `TraceAggregates` is built at server startup and held in memory: O(total_events) for the hit lists.  At 10M events this is ~80–160 MiB (same order as `JsonlIndex`).  Sparse index (`sparse_k`) mitigates this when needed.
- Cumulative-heat queries are per-playhead-scrub, so rapid scrubbing issues many requests.  The existing 5 s timeout and silent-drop semantics handle races; a debounce in `TimelinePanel` (≥ 150 ms, already present for seek) bounds the request rate.
- Coverage at arbitrary index requires a `bisect` over all first-seen entries — O(log M).  This is fast enough for interactive use; a prefix-sum array over `sorted_first_seen` would make it O(1) if needed.
