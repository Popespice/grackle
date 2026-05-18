export type { Graph, GraphEdge, GraphNode } from "./generated/graph.js";

export const KNOWN_NODE_KINDS = [
  "file",
  "class",
  "function",
  "method",
  "interface",
  "type_alias",
  "enum",
  "struct",
] as const;
export type KnownNodeKind = (typeof KNOWN_NODE_KINDS)[number];

export const KNOWN_EDGE_KINDS = [
  "import",
  "call",
  "inherit",
  "implements",
] as const;
export type KnownEdgeKind = (typeof KNOWN_EDGE_KINDS)[number];
