import type { Graph, TraceEvent } from "@grackle/shared-types";

/** Summary of runtime coverage for a trace session. */
export interface RuntimeCoverage {
  /** Node IDs present in the graph that received ≥1 trace event. */
  touched: Set<string>;
  /** Node IDs present in the graph that received 0 trace events. */
  cold: Set<string>;
  /**
   * Top-quartile call-count nodes — nodes whose event count is at or above
   * the 75th percentile of counts, subject to a small floor so that trivially
   * small sessions don't inflate the hot set.
   */
  hot: Set<string>;
  touchedCount: number;
  coldCount: number;
  hotCount: number;
}

const HOT_FLOOR = 2; // minimum count to ever be considered "hot"

/**
 * Compute session-level runtime coverage — playhead-independent.
 *
 * Only events whose `node_id` exists in `graph.nodes` are counted
 * (stdlib frames and external callees are excluded).
 *
 * NOTE: This is intentionally NOT an AnalysisRegistry entry.
 * The registry caches by graph object reference (`WeakMap<Graph, T>`) and
 * `compute(graph)` takes only a `Graph` argument — it has no access to
 * mutable `traceEvents`. Registering coverage would cache the first (empty)
 * result for the session's entire lifetime. Instead it lives as a dedicated
 * `useRuntimeCoverage()` hook that wraps this function in `useMemo`.
 */
export function runtimeCoverage(
  graph: Graph,
  events: TraceEvent[]
): RuntimeCoverage {
  // Build a Set of all node IDs in the graph for fast lookup.
  const graphNodeIds = new Set(graph.nodes.map((n) => n.id));

  // Count calls per node, restricted to graph nodes.
  const counts = new Map<string, number>();
  for (const ev of events) {
    if (!graphNodeIds.has(ev.node_id)) continue;
    counts.set(ev.node_id, (counts.get(ev.node_id) ?? 0) + 1);
  }

  const touched = new Set(counts.keys());
  const cold = new Set<string>();
  for (const n of graph.nodes) {
    if (!touched.has(n.id)) cold.add(n.id);
  }

  // Hot = top-quartile by call count, with a minimum floor.
  const hot = new Set<string>();
  if (counts.size > 0) {
    const values = [...counts.values()].sort((a, b) => a - b);
    const p75Idx = Math.floor(values.length * 0.75);
    const threshold = Math.max(HOT_FLOOR, values[p75Idx] ?? 0);
    for (const [id, cnt] of counts) {
      if (cnt >= threshold) hot.add(id);
    }
  }

  return {
    touched,
    cold,
    hot,
    touchedCount: touched.size,
    coldCount: cold.size,
    hotCount: hot.size,
  };
}
