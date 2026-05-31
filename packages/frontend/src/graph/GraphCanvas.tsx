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
import type { DiffStatus } from "./diff";
import { COLD_HEX, heatColor } from "./heatColor";
import { isNodeVisible } from "./matching";
import { useGraphStore } from "./useGraphStore";
import { useHeatmap } from "./useHeatmap";

const KIND_COLORS: Record<string, string> = {
  file: "#3b82f6",
  class: "#8b5cf6",
  function: "#10b981",
  method: "#f59e0b",
};

/**
 * Hex colours for diff overlay statuses.
 * All values are #rrggbb — never oklch/hsl/CSS-var (ADR-0015).
 */
const DIFF_COLORS: Record<DiffStatus, string> = {
  hotter: "#ef4444", // red   — regression
  new: "#22c55e", // green — new coverage
  colder: "#3b82f6", // blue  — reduced calls
  gone: "#6b7280", // gray  — no longer called
  cold: "#f59e0b", // amber — never called
  touched: "#10b981", // emerald — covered
  same: "", // empty = fall through to kind color
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
  excludeGlobs: string[],
  selectedNodeId: string | null,
  highlightedNodeIds: Set<string> | null,
  container: HTMLElement,
  heat: Map<string, number>,
  maxHeat: number,
  heatActive: boolean,
  diffOverlay: Map<string, DiffStatus> | null
) {
  return (node: string, data: NodeAttributes): Partial<NodeDisplayData> => {
    const hidden = !isNodeVisible(
      {
        id: node,
        kind: data.kind,
        name: data.name,
        path: data.path,
        ...(data.metadata !== undefined ? { metadata: data.metadata } : {}),
      },
      { hiddenKinds, searchTerm, excludeGlobs }
    );

    const inDegree = graphology.inDegree(node);
    const size = Math.max(BASE_SIZE, Math.log(inDegree + 1) * 8 + BASE_SIZE);

    const highlightActive =
      highlightedNodeIds !== null && highlightedNodeIds.size > 0;
    const isHighlighted =
      highlightActive && (highlightedNodeIds?.has(node) ?? false);
    const dimmed =
      (highlightActive && !isHighlighted) ||
      (!highlightActive && selectedNodeId !== null && node !== selectedNodeId);

    // Color cascade:
    //   highlighted → dimmed → diff overlay (if active) → heat (if active + data) → resolved kind color
    // All colors passed to Sigma must be hex/rgb — never oklch/hsl/CSS-var.
    // See ADR-0015: Sigma 3.x parseColor silently maps unknown formats to black.
    let color: string;
    if (isHighlighted) {
      // --color-highlight-cycle is hex since the ADR-0015 token fix.
      color = cssVar(container, "--color-highlight-cycle") || "#e6863c";
    } else if (dimmed) {
      color = "#cbd5e1";
    } else if (diffOverlay !== null) {
      const status = diffOverlay.get(node);
      const diffColor = status ? DIFF_COLORS[status] : "";
      // Fall through to kind color when status is "same" or node not in overlay.
      color = diffColor || resolveNodeColor(data.kind, container);
    } else if (heatActive && maxHeat > 0) {
      const count = heat.get(node) ?? 0;
      color = count > 0 ? heatColor(count / maxHeat) : COLD_HEX;
    } else {
      color = resolveNodeColor(data.kind, container);
    }

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
  const excludeGlobs = useGraphStore((s) => s.excludeGlobs);
  const selectedNodeId = useGraphStore((s) => s.selectedNodeId);
  const highlightedNodeIds = useGraphStore((s) => s.highlightedNodeIds);
  const traceSessionId = useGraphStore((s) => s.traceSessionId);
  const diffOverlay = useGraphStore((s) => s.diffOverlay);
  const selectNode = useGraphStore((s) => s.selectNode);
  const setHighlightedNodes = useGraphStore((s) => s.setHighlightedNodes);

  const { heat, maxHeat } = useHeatmap();
  const heatActive = traceSessionId !== null;

  // Rebuild sigma + FA2 when the graph data changes.
  // Filter/heat state is handled by effect 2 so sigma is not torn down.
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
          excludeGlobs,
          selectedNodeId,
          highlightedNodeIds,
          container,
          heat,
          maxHeat,
          heatActive,
          diffOverlay
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
      setHighlightedNodes(null);
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

  // Update node reducer + refresh when filter/heat/diff state changes,
  // without rebuilding sigma. Reuses the setSetting("nodeReducer") +
  // sigma.refresh() path — same mechanism as cycle-highlight wiring.
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
        excludeGlobs,
        selectedNodeId,
        highlightedNodeIds,
        container,
        heat,
        maxHeat,
        heatActive,
        diffOverlay
      )
    );
    sigma.refresh();
  }, [
    hiddenKinds,
    searchTerm,
    excludeGlobs,
    selectedNodeId,
    highlightedNodeIds,
    heat,
    maxHeat,
    heatActive,
    diffOverlay,
  ]);

  return (
    <div
      ref={containerRef}
      role="img"
      aria-label="Code graph"
      style={{ position: "absolute", inset: 0 }}
    />
  );
}
