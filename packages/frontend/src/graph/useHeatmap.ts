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
 *
 * In seekable + cumulative mode, ``agentHeat`` from the store is used if
 * available — the agent returns absolute cumulative counts that are more
 * accurate than the client-side window-local computation.
 */
export function useHeatmap(): { heat: Map<string, number>; maxHeat: number } {
  const traceEvents = useGraphStore((s) => s.traceEvents);
  const tracePlayhead = useGraphStore((s) => s.tracePlayhead);
  const traceEventTypeFilter = useGraphStore((s) => s.traceEventTypeFilter);
  const traceHeatMode = useGraphStore((s) => s.traceHeatMode);
  const traceWindowSize = useGraphStore((s) => s.traceWindowSize);
  const traceWindowStart = useGraphStore((s) => s.traceWindowStart);
  const traceSeekable = useGraphStore((s) => s.traceSeekable);
  const agentHeat = useGraphStore((s) => s.agentHeat);

  return useMemo(() => {
    // In seekable + cumulative mode, use agent-computed heat if available.
    // The agent returns absolute cumulative counts — convert to the same
    // {heat: Map, maxHeat} shape that computeHeat returns.
    if (traceSeekable && traceHeatMode === "cumulative" && agentHeat !== null) {
      const heat = new Map<string, number>(Object.entries(agentHeat));
      let maxHeat = 0;
      for (const v of heat.values()) if (v > maxHeat) maxHeat = v;
      return { heat, maxHeat };
    }
    return computeHeat(
      traceEvents,
      tracePlayhead,
      traceEventTypeFilter,
      traceHeatMode,
      traceWindowSize,
      traceWindowStart
    );
  }, [
    traceEvents,
    tracePlayhead,
    traceEventTypeFilter,
    traceHeatMode,
    traceWindowSize,
    traceWindowStart,
    traceSeekable,
    agentHeat,
  ]);
}
