import type { Graph, GraphEdge, GraphNode } from "@grackle/shared-types";
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
  /**
   * Pins the node against FA2 displacement (graphology-layout-forceatlas2
   * reads this directly). Never set by buildGraphology/applyGraphDiff —
   * only GraphCanvas's bounded reheat (phase 10.7) toggles it, to hold
   * survivors still while new nodes settle in.
   */
  fixed?: boolean;
}

export interface EdgeAttributes {
  kind: string;
  label: string;
  color: string;
  size: number;
  /**
   * 1-based justifying source line of this edge (edge evidence, ADR-0026),
   * carried from the wire edge's open ``metadata.line``.  Absent when the
   * adapter emitted no line (cross-language on a stale cache, Go method-set
   * synthesis).  Read on ``clickEdge`` to jump to the exact call/import site.
   */
  line?: number;
}

export type GrackleMultiGraph = MultiDirectedGraph<
  NodeAttributes,
  EdgeAttributes
>;

/**
 * Node attributes sourced from the wire Graph payload, excluding presentation
 * state (x/y/size/color/hidden) that a diff-apply must never overwrite for a
 * surviving node. Shared by buildGraphology (scratch build) and
 * applyGraphDiff (incremental apply, phase 10.7) so the two paths can't drift.
 */
export type WireNodeAttributes = Pick<
  NodeAttributes,
  "kind" | "name" | "path" | "label" | "line" | "metadata"
>;

export function wireNodeAttributes(node: GraphNode): WireNodeAttributes {
  return {
    kind: node.kind,
    name: node.name,
    path: node.path,
    label: node.name,
    ...(node.line !== undefined ? { line: node.line } : {}),
    ...(node.metadata !== undefined
      ? { metadata: node.metadata as Record<string, unknown> }
      : {}),
  };
}

/**
 * Edge attributes have no persisted presentation state across a diff-apply —
 * a changed edge is always a drop+add (see applyGraphDiff.ts), so this covers
 * the full attribute set, not just a "wire subset" like wireNodeAttributes.
 */
export function wireEdgeAttributes(edge: GraphEdge): EdgeAttributes {
  const line = edge.metadata?.line;
  return {
    kind: edge.kind,
    label: edge.kind,
    color: "#94a3b8",
    size: 1,
    ...(typeof line === "number" ? { line } : {}),
  };
}

export function buildGraphology(graph: Graph): GrackleMultiGraph {
  const g = new MultiDirectedGraph<NodeAttributes, EdgeAttributes>();

  for (const node of graph.nodes) {
    // Guard against duplicate node IDs. The static graph contract requires
    // uniqueness, but adapters can still emit a collision (e.g. a Python
    // @property getter/setter pair sharing a name) — graphology's addNode
    // throws on a repeat, which would otherwise crash the whole canvas.
    if (g.hasNode(node.id)) {
      console.warn(`buildGraphology: duplicate node id "${node.id}", skipping`);
      continue;
    }
    g.addNode(node.id, {
      ...wireNodeAttributes(node),
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
    g.addEdge(edge.source, edge.target, wireEdgeAttributes(edge));
  }

  return g;
}
