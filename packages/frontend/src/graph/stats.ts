import type { Graph, GraphNode } from "@grackle/shared-types";

export interface KindCount {
  kind: string;
  count: number;
}

export interface DegreeEntry {
  node: GraphNode;
  inDegree: number;
}

export function countByKind(graph: Graph): KindCount[] {
  const counts = new Map<string, number>();
  for (const node of graph.nodes) {
    counts.set(node.kind, (counts.get(node.kind) ?? 0) + 1);
  }
  return [...counts.entries()]
    .map(([kind, count]) => ({ kind, count }))
    .sort((a, b) => b.count - a.count);
}

export function topByInDegree(graph: Graph, n = 10): DegreeEntry[] {
  const inDegree = new Map<string, number>();
  for (const node of graph.nodes) {
    inDegree.set(node.id, 0);
  }
  for (const edge of graph.edges) {
    inDegree.set(edge.target, (inDegree.get(edge.target) ?? 0) + 1);
  }

  return graph.nodes
    .map((node) => ({ node, inDegree: inDegree.get(node.id) ?? 0 }))
    .sort((a, b) => b.inDegree - a.inDegree)
    .slice(0, n);
}

// Orphans: nodes with no inbound edges that are non-import (i.e., nothing calls/inherits them).
export function orphans(graph: Graph): GraphNode[] {
  const nonImportInbound = new Set<string>();
  for (const edge of graph.edges) {
    if (edge.kind !== "import") {
      nonImportInbound.add(edge.target);
    }
  }
  return graph.nodes.filter((n) => !nonImportInbound.has(n.id));
}
