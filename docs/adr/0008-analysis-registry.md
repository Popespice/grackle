# ADR-0008 — Analysis registry

**Status:** accepted, implemented in Phase 4

## Context

Phase 3 introduces graph-level analyses: `countByKind`, `topByInDegree`, and `orphans`. These are pure functions over a `Graph` object that the StatsPanel calls on each render. As graph size grows toward the 2k-node stress target, computing them synchronously on the JS main thread is acceptable; beyond that, the analyses become candidate work for the agent.

A forward-compatible interface is needed so that analyses can move from frontend-side to agent-side in a non-breaking way, and so new analyses (e.g., cycle detection, hot-path identification for Phase 6) can be added without modifying existing callers.

## Decision

**Phase 3 implementation: plain functions, no registry.** The three analysis functions (`countByKind`, `topByInDegree`, `orphans`) live in `packages/frontend/src/graph/stats.ts` as pure exports with no registry indirection. This keeps the code straightforward and avoids premature abstraction — the rule-of-three has not yet fired for analyses.

**Phase 4+ interface (reserved, not implemented):** When a fourth analysis is needed or when agent-side scheduling becomes necessary, introduce an `Analysis<T>` interface:

```typescript
interface Analysis<T> {
  id: string;
  compute(graph: Graph): T;
  cacheKey(graph: Graph): string;
}
```

An `AnalysisRegistry` (same pattern as `PanelRegistry`, ADR-0007) would let callers register analyses by ID and retrieve results via a shared memoisation layer keyed by `cacheKey(graph)`. The cache key is intentionally separate from the graph object to permit content-hash–based keying (e.g., SHA-256 of node IDs + edge count) without requiring the graph object to be stable across React renders.

**Agent-side scheduling.** Heavy analyses (cycle detection on 10k+ nodes) should eventually run in the agent and be pushed to the frontend as supplemental graph metadata. The `graph.metadata` bag (ADR-0004 open metadata) is the natural landing zone; the frontend's `Analysis<T>` interface can transparently read from it when the agent provides the result, or fall back to local computation when offline.

**Caching strategy.** The simplest cache key for Phase 3 is `graph.nodes.length + ':' + graph.edges.length`. A proper content-hash key (SHA-256 of serialized graph) is scheduled for Phase 4 when the analysis results need to survive graph re-fetches without recomputation.

## Consequences

- Stats are computed synchronously on the render thread; jank is expected only at >10k nodes (not in scope until Phase 6).
- No registry indirection today means no dynamic registration — adding a new analysis requires editing `stats.ts` directly. This is acceptable while the count is below three.
- The `cacheKey` seam in the future interface preserves the option to move expensive analyses to a Web Worker or the agent without changing callers.
- Cross-refs: ADR-0004 (open metadata), ADR-0005 (kind registry pattern), ADR-0007 (panel/slot system using the same registry shape).

## Phase 4 implementation note

The `Analysis<T>` interface and `AnalysisRegistry` were implemented in Phase 4 at `packages/frontend/src/graph/analysis/`. The chosen cache-key implementation is:

- **In-memory cache:** `WeakMap<Graph, Map<analysisId, result>>` keyed by graph object reference. Same render = same Graph reference = zero recomputation. Garbage-collected automatically when the graph is discarded.
- **Content-hash utility:** `graphCacheKey(graph): Promise<string>` in `cacheKey.ts` computes SHA-256 over canonical JSON (sorted node IDs + sorted `source|target|kind` edge tuples). Stable across array reordering; differs on any structural graph change.
- **Registered analyses:** `count-by-kind`, `top-in-degree`, `orphans`, `hub-score` (4th analysis proves rule-of-three).
- **`useAnalysis<T>(id): T | null`** hook exposes registry results to React components; `StatsPanel` refactored to use it.
