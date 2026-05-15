import type { Graph } from "@grackle/shared-types";
import { MultiDirectedGraph } from "graphology";

export interface NodeAttributes {
  kind: string;
  name: string;
  path: string;
  line?: number;
  metadata?: Record<string, unknown>;
  label: string;
  x: number;
  y: number;
  size: number;
  color: string;
  hidden: boolean;
}

export interface EdgeAttributes {
  kind: string;
  label: string;
  color: string;
  size: number;
}

export type GrackleMultiGraph = MultiDirectedGraph<
  NodeAttributes,
  EdgeAttributes
>;

export function buildGraphology(graph: Graph): GrackleMultiGraph {
  const g = new MultiDirectedGraph<NodeAttributes, EdgeAttributes>();

  for (const node of graph.nodes) {
    g.addNode(node.id, {
      kind: node.kind,
      name: node.name,
      path: node.path,
      ...(node.line !== undefined ? { line: node.line } : {}),
      ...(node.metadata !== undefined
        ? { metadata: node.metadata as Record<string, unknown> }
        : {}),
      label: node.name,
      x: Math.random(),
      y: Math.random(),
      size: 6,
      color: "#6366f1",
      hidden: false,
    });
  }

  for (const edge of graph.edges) {
    // Guard against edges referencing nodes absent from the graph
    if (!g.hasNode(edge.source) || !g.hasNode(edge.target)) continue;
    g.addEdge(edge.source, edge.target, {
      kind: edge.kind,
      label: edge.kind,
      color: "#94a3b8",
      size: 1,
    });
  }

  return g;
}
