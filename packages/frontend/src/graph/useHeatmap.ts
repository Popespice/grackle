import { useMemo } from "react";
import { computeHeat } from "./heatmap";
import { useGraphStore } from "./useGraphStore";

/**
 * Derive the current heat map from store state.
 *
 * Recalculates only when the relevant slice changes, keeping Sigma
 * refreshes decoupled from the store's append-only `traceEvents` array
 * (which updates on every incoming trace event).
 *
 * In seekable mode, ``traceWindowStart`` is passed to ``computeHeat`` so that
 * the absolute ``tracePlayhead`` is translated to a window-relative position
 * before indexing into the (partial) ``traceEvents`` window.
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
  const traceWindowStart = useGraphStore((s) => s.traceWindowStart);

  return useMemo(
    () =>
      computeHeat(
        traceEvents,
        tracePlayhead,
        traceEventTypeFilter,
        traceHeatMode,
        traceWindowSize,
        traceWindowStart
      ),
    [
      traceEvents,
      tracePlayhead,
      traceEventTypeFilter,
      traceHeatMode,
      traceWindowSize,
      traceWindowStart,
    ]
  );
}
