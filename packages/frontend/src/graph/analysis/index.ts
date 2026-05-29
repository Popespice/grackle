import type { Graph, GraphNode } from "@grackle/shared-types";
import type { DegreeEntry, KindCount } from "../stats";
import { countByKind, orphans, topByInDegree } from "../stats";
import { useGraphStore } from "../useGraphStore";
import type { CycleEntry } from "./cycleDetection";
import { cycleDetection } from "./cycleDetection";
import type { HubEntry } from "./hubScore";
import { hubScore } from "./hubScore";
import { AnalysisRegistry } from "./registry";

export type { CycleEntry } from "./cycleDetection";
export type { HubEntry } from "./hubScore";
export type { Analysis } from "./registry";

export const analyses = new AnalysisRegistry();

analyses.register<KindCount[]>({
  id: "count-by-kind",
  compute: (graph: Graph) => countByKind(graph),
  cacheKey: (graph: Graph) => `count-by-kind:${graph.nodes.length}`,
});

analyses.register<DegreeEntry[]>({
  id: "top-in-degree",
  compute: (graph: Graph) => topByInDegree(graph),
  cacheKey: (graph: Graph) =>
    `top-in-degree:${graph.nodes.length}:${graph.edges.length}`,
});

analyses.register<GraphNode[]>({
  id: "orphans",
  compute: (graph: Graph) => orphans(graph),
  cacheKey: (graph: Graph) =>
    `orphans:${graph.nodes.length}:${graph.edges.length}`,
});

analyses.register<HubEntry[]>({
  id: "hub-score",
  compute: (graph: Graph) => {
    // Use agent-computed results from graph.metadata if present (Phase 8.3).
    const raw = graph.metadata?.hub_score;
    if (Array.isArray(raw) && raw.length > 0) {
      return raw as HubEntry[];
    }
    return hubScore(graph);
  },
  cacheKey: (graph: Graph) =>
    `hub-score:${graph.nodes.length}:${graph.edges.length}`,
});

analyses.register<CycleEntry[]>({
  id: "cycles",
  compute: (graph: Graph) => {
    // Use agent-computed results from graph.metadata if present (Phase 8.3).
    const raw = graph.metadata?.cycles;
    if (Array.isArray(raw) && raw.length > 0) {
      return raw as CycleEntry[];
    }
    return cycleDetection(graph);
  },
  cacheKey: (graph: Graph) =>
    `cycles:${graph.nodes.length}:${graph.edges.length}`,
});

/**
 * React hook: compute and cache an analysis result for the current graph.
 * Results are memoised by graph object reference (WeakMap).
 */
export function useAnalysis<T>(id: string): T | null {
  const graph = useGraphStore((s) => s.graph);
  if (!graph) return null;
  return analyses.computeCached<T>(graph, id);
}
