import type { JSX } from "react";
import { NodeInspector } from "../graph/NodeInspector";
import { useGraphStore } from "../graph/useGraphStore";

export function NodeInspectorPanel(): JSX.Element | null {
  const selectedNodeId = useGraphStore((s) => s.selectedNodeId);
  const graph = useGraphStore((s) => s.graph);
  const selectNode = useGraphStore((s) => s.selectNode);

  if (!selectedNodeId || !graph) return null;

  const node = graph.nodes.find((n) => n.id === selectedNodeId) ?? null;

  return <NodeInspector node={node} onClose={() => selectNode(null)} />;
}
