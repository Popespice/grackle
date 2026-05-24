import { useMemo } from "react";
import type { RuntimeCoverage } from "./runtimeCoverage";
import { runtimeCoverage } from "./runtimeCoverage";
import { useGraphStore } from "./useGraphStore";

/**
 * Compute session-level runtime coverage from the current graph + trace events.
 *
 * Returns `null` when no graph is loaded.
 *
 * NOTE: This is a hook rather than an AnalysisRegistry entry because the
 * registry caches by graph object reference and `compute(graph)` accepts only
 * a `Graph` argument — it has no access to mutable `traceEvents`. Registering
 * coverage would permanently cache the first (empty) result. See ADR-0015.
 */
export function useRuntimeCoverage(): RuntimeCoverage | null {
  const graph = useGraphStore((s) => s.graph);
  const traceEvents = useGraphStore((s) => s.traceEvents);

  return useMemo(() => {
    if (graph === null) return null;
    return runtimeCoverage(graph, traceEvents);
  }, [graph, traceEvents]);
}
