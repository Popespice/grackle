import type { Graph, GraphNode } from "@grackle/shared-types";

export interface HubEntry {
  node: GraphNode;
  score: number;
}

/**
 * Compute hub score for each node: score = in-degree - out-degree.
 * Higher score → more "imported" than "importing" = a hub.
 * Returns descending-sorted entries.
 */
export function hubScore(graph: Graph): HubEntry[] {
  const inDegree = new Map<string, number>();
  const outDegree = new Map<string, number>();

  for (const node of graph.nodes) {
    inDegree.set(node.id, 0);
    outDegree.set(node.id, 0);
  }

  for (const edge of graph.edges) {
    inDegree.set(edge.target, (inDegree.get(edge.target) ?? 0) + 1);
    outDegree.set(edge.source, (outDegree.get(edge.source) ?? 0) + 1);
  }

  return graph.nodes
    .map((node) => ({
      node,
      score: (inDegree.get(node.id) ?? 0) - (outDegree.get(node.id) ?? 0),
    }))
    .sort((a, b) => b.score - a.score);
}
