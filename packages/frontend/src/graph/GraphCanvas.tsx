import FA2Layout from "graphology-layout-forceatlas2/worker";
import type { JSX } from "react";
import { useEffect, useRef } from "react";
import Sigma from "sigma";
import type { NodeDisplayData } from "sigma/types";
import {
  buildGraphology,
  type EdgeAttributes,
  type GrackleMultiGraph,
  type NodeAttributes,
} from "./buildGraphology";
import { useGraphStore } from "./useGraphStore";

const KIND_COLORS: Record<string, string> = {
  file: "#3b82f6",
  class: "#8b5cf6",
  function: "#10b981",
  method: "#f59e0b",
};

const DEFAULT_NODE_COLOR = "#6366f1";
const DEFAULT_EDGE_COLOR = "#94a3b8";
const BASE_SIZE = 6;

function cssVar(el: HTMLElement, name: string): string {
  return getComputedStyle(el).getPropertyValue(name).trim();
}

function resolveNodeColor(kind: string, container: HTMLElement): string {
  return (
    cssVar(container, `--color-node-${kind}`) ||
    KIND_COLORS[kind] ||
    DEFAULT_NODE_COLOR
  );
}

function resolveEdgeColor(kind: string, container: HTMLElement): string {
  return cssVar(container, `--color-edge-${kind}`) || DEFAULT_EDGE_COLOR;
}

function makeNodeReducer(
  graphology: GrackleMultiGraph,
  hiddenKinds: Set<string>,
  searchTerm: string,
  selectedNodeId: string | null,
  container: HTMLElement
) {
  return (node: string, data: NodeAttributes): Partial<NodeDisplayData> => {
    const hidden =
      hiddenKinds.has(data.kind) ||
      (searchTerm.length > 0 &&
        !data.name.toLowerCase().includes(searchTerm.toLowerCase()) &&
        !data.path.toLowerCase().includes(searchTerm.toLowerCase()));

    const inDegree = graphology.inDegree(node);
    const size = Math.max(BASE_SIZE, Math.log(inDegree + 1) * 8 + BASE_SIZE);
    const dimmed = selectedNodeId !== null && node !== selectedNodeId;
    const color = dimmed ? "#cbd5e1" : resolveNodeColor(data.kind, container);

    return { color, size, hidden };
  };
}

export function GraphCanvas(): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);
  const sigmaRef = useRef<Sigma<NodeAttributes, EdgeAttributes> | null>(null);
  const fa2Ref = useRef<FA2Layout<NodeAttributes, EdgeAttributes> | null>(null);
  const graphologyRef = useRef<GrackleMultiGraph | null>(null);

  const graph = useGraphStore((s) => s.graph);
  const hiddenKinds = useGraphStore((s) => s.hiddenKinds);
  const searchTerm = useGraphStore((s) => s.searchTerm);
  const selectedNodeId = useGraphStore((s) => s.selectedNodeId);
  const selectNode = useGraphStore((s) => s.selectNode);

  // Rebuild sigma + FA2 when the graph data changes.
  // hiddenKinds/searchTerm/selectedNodeId are intentionally omitted from deps:
  // effect 2 below handles those changes without tearing down the sigma instance.
  // biome-ignore lint/correctness/useExhaustiveDependencies: intentional two-effect pattern
  useEffect(() => {
    if (!containerRef.current || !graph) return;
    const container = containerRef.current;

    // StrictMode-safe: kill stale instances from the first (discarded) mount
    fa2Ref.current?.kill();
    fa2Ref.current = null;
    sigmaRef.current?.kill();
    sigmaRef.current = null;

    const graphology = buildGraphology(graph);
    graphologyRef.current = graphology;

    const sigma = new Sigma<NodeAttributes, EdgeAttributes>(
      graphology,
      container,
      {
        nodeReducer: makeNodeReducer(
          graphology,
          hiddenKinds,
          searchTerm,
          selectedNodeId,
          container
        ),
        edgeReducer: (_edge, data) => ({
          color: resolveEdgeColor(data.kind, container),
        }),
      }
    );

    sigma.on("clickNode", ({ node }) => {
      selectNode(node);
    });
    sigma.on("clickStage", () => {
      selectNode(null);
    });

    sigmaRef.current = sigma;

    const fa2 = new FA2Layout<NodeAttributes, EdgeAttributes>(graphology, {
      settings: { barnesHutOptimize: true, gravity: 1, slowDown: 10 },
    });
    fa2.start();
    fa2Ref.current = fa2;

    const stopTimer = setTimeout(() => {
      fa2Ref.current?.stop();
    }, 5000);

    return () => {
      clearTimeout(stopTimer);
      fa2Ref.current?.kill();
      fa2Ref.current = null;
      sigmaRef.current?.kill();
      sigmaRef.current = null;
      graphologyRef.current = null;
    };
  }, [graph]);

  // Update node reducer + refresh when filter state changes, without rebuilding sigma.
  useEffect(() => {
    const sigma = sigmaRef.current;
    const graphology = graphologyRef.current;
    const container = containerRef.current;
    if (!sigma || !graphology || !container) return;

    sigma.setSetting(
      "nodeReducer",
      makeNodeReducer(
        graphology,
        hiddenKinds,
        searchTerm,
        selectedNodeId,
        container
      )
    );
    sigma.refresh();
  }, [hiddenKinds, searchTerm, selectedNodeId]);

  return (
    <div
      ref={containerRef}
      role="img"
      aria-label="Code graph"
      style={{ position: "absolute", inset: 0 }}
    />
  );
}
