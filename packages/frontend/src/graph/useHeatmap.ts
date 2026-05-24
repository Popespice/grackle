import { useMemo } from "react";
import { computeHeat } from "./heatmap";
import { useGraphStore } from "./useGraphStore";

/**
 * Derive the current heat map from store state.
 *
 * Recalculates only when the relevant slice changes, keeping Sigma
 * refreshes decoupled from the store's append-only `traceEvents` array
 * (which updates on every incoming trace event).
 */
export function useHeatmap(): {
  heat: Map<string, number>;
  maxHeat: number;
} {
  const traceEvents = useGraphStore((s) => s.traceEvents);
  const tracePlayhead = useGraphStore((s) => s.tracePlayhead);
  const traceEventTypeFilter = useGraphStore((s) => s.traceEventTypeFilter);
  const traceHeatMode = useGraphStore((s) => s.traceHeatMode);
  const traceWindowSize = useGraphStore((s) => s.traceWindowSize);

  return useMemo(
    () =>
      computeHeat(
        traceEvents,
        tracePlayhead,
        traceEventTypeFilter,
        traceHeatMode,
        traceWindowSize
      ),
    // eslint-disable-next-line react-hooks/exhaustive-deps
    [
      traceEvents,
      tracePlayhead,
      traceEventTypeFilter,
      traceHeatMode,
      traceWindowSize,
    ]
  );
}
