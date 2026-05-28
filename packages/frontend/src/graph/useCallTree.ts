import type { TraceEvent } from "@grackle/shared-types";
import { useMemo } from "react";
import {
  aggregateCallTree,
  buildCallTree,
  type CallFrame,
  type CallTree,
  hotPath,
} from "./callTree";
import { useGraphStore } from "./useGraphStore";

/**
 * Derive the flame-graph data model from the trace events in the store
 * (Phase 8.2). Mirrors `useHeatmap`: one store selector per slice, all derived
 * work in `useMemo`.
 *
 * Unlike the heat map, the flame graph is an aggregate-over-the-run view, not a
 * playhead-driven one — it reconstructs over the full loaded event buffer, not
 * `events[0..playhead]`. In buffered (live) sessions that buffer is the whole
 * trace; in seekable (file-replay) sessions the store holds only a window, so
 * the panel may pass a fully-paged `overrideEvents` array to reconstruct the
 * entire run. When neither is the whole trace, `hadSynthetic`/`orphanReturns`
 * on the returned `CallTree` flag the partial reconstruction.
 */
export function useCallTree(overrideEvents?: TraceEvent[] | null): {
  tree: CallTree;
  aggregated: CallFrame[];
  hot: Set<CallFrame>;
} {
  const storeEvents = useGraphStore((s) => s.traceEvents);
  const events = overrideEvents ?? storeEvents;

  const tree = useMemo(() => buildCallTree(events), [events]);
  const aggregated = useMemo(() => aggregateCallTree(tree.roots), [tree]);
  const hot = useMemo(() => hotPath(aggregated), [aggregated]);

  return { tree, aggregated, hot };
}
