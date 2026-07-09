import type { Graph } from "@grackle/shared-types";
import FA2Layout from "graphology-layout-forceatlas2/worker";
import type { JSX } from "react";
import { type RefObject, useEffect, useRef } from "react";
import Sigma from "sigma";
import type { EdgeDisplayData, NodeDisplayData } from "sigma/types";
import { useTheme } from "../theme/useTheme";
import {
  type ApplyGraphDiffResult,
  applyGraphDiff,
  isEmptyDiff,
} from "./applyGraphDiff";
import {
  buildGraphology,
  type EdgeAttributes,
  type GrackleMultiGraph,
  type NodeAttributes,
} from "./buildGraphology";
import type { DiffStatus } from "./diff";
import { DIFF_STATUS_COLORS } from "./diff";
import {
  type AnimationState,
  createAnimationState,
  ENTER_DURATION_MS,
  ENTER_FLASH_COLOR,
  EXIT_DURATION_MS,
  EXIT_FADE_COLOR,
  elapsedFraction,
  enterScale,
  exitScale,
  lerpHex,
  pulseEnvelope,
  recordDiffAnimations,
  tickAnimations,
} from "./graphAnimation";
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

const DEFAULT_NODE_COLOR = "#6366f1";
const DEFAULT_EDGE_COLOR = "#94a3b8";
const BASE_SIZE = 6;
const EDGE_BASE_SIZE = 1;
const EDGE_PULSE_PEAK_SIZE = 3;

// How long a bounded incremental reheat pins survivors and lets FA2 settle
// only the new/changed neighborhood — short relative to the 5s initial-load
// settle, since only a handful of nodes typically need to find a spot.
const REHEAT_DURATION_MS = 1500;

// Sigma can't parse the oklch `--color-text` token (ADR-0015) and its default
// label color is black — invisible on the dark canvas. Drive it from the theme.
const LABEL_COLOR_DARK = "#ffffff";
const LABEL_COLOR_LIGHT = "#0f172a";

function labelColorForTheme(theme: string): string {
  return theme === "dark" ? LABEL_COLOR_DARK : LABEL_COLOR_LIGHT;
}

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

/** `true` iff at least one incoming node id already exists in `live` — the
 * signal that this re-push is the SAME project (diff incrementally) rather
 * than a different one entirely (scratch rebuild, fresh layout + camera). */
function hasSurvivor(live: GrackleMultiGraph, incoming: Graph): boolean {
  for (const node of incoming.nodes) {
    if (live.hasNode(node.id)) return true;
  }
  return false;
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
  diffOverlay: Map<string, DiffStatus> | null,
  animRef: RefObject<AnimationState>
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
    let size = Math.max(BASE_SIZE, Math.log(inDegree + 1) * 8 + BASE_SIZE);

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
      const diffColor = status ? DIFF_STATUS_COLORS[status] : "";
      // Fall through to kind color when status is "same" or node not in overlay.
      color = diffColor || resolveNodeColor(data.kind, container);
    } else if (heatActive && maxHeat > 0) {
      const count = heat.get(node) ?? 0;
      color = count > 0 ? heatColor(count / maxHeat) : COLD_HEX;
    } else {
      color = resolveNodeColor(data.kind, container);
    }

    // Phase 10.7 enter/exit animation, layered on AFTER the cascade above so
    // highlight/dim/diff/heat semantics are unaffected — only size/color get
    // an animated multiplier/lerp on top of whatever the cascade decided.
    const anim = animRef.current;
    const enteringT0 = anim.entering.get(node);
    if (enteringT0 !== undefined) {
      const t = elapsedFraction(
        enteringT0,
        performance.now(),
        ENTER_DURATION_MS
      );
      size *= enterScale(t);
      color = lerpHex(ENTER_FLASH_COLOR, color, t);
    } else {
      const exitingT0 = anim.exiting.get(node);
      if (exitingT0 !== undefined) {
        const t = elapsedFraction(
          exitingT0,
          performance.now(),
          EXIT_DURATION_MS
        );
        size *= exitScale(t);
        color = lerpHex(color, EXIT_FADE_COLOR, t);
      }
    }

    // Sigma's nodeReducer contract REPLACES the node's attrs wholesale — it
    // is not merged with the graphology-stored data (Sigma addNode: "this
    // function must return a total object and won't be merged"). Spreading
    // `data` (which carries x/y) before overriding the computed display
    // fields is required — a bare `{ color, size, hidden }` silently drops
    // x/y and crashes Sigma's first-ever addNode with "could not find a
    // valid position" (pre-existing bug, confirmed present on main too;
    // found during 10.7 manual verification).
    return { ...data, color, size, hidden };
  };
}

function makeEdgeReducer(
  container: HTMLElement,
  animRef: RefObject<AnimationState>
) {
  return (edge: string, data: EdgeAttributes): Partial<EdgeDisplayData> => {
    const baseColor = resolveEdgeColor(data.kind, container);
    const t0 = animRef.current.enteringEdges.get(edge);
    if (t0 === undefined) return { color: baseColor };

    // A pulse, not a settle: the edge doesn't grow permanently, it flashes
    // 1 -> 3 -> 1 to draw the eye, while its color settles flash -> base.
    const t = elapsedFraction(t0, performance.now(), ENTER_DURATION_MS);
    return {
      color: lerpHex(ENTER_FLASH_COLOR, baseColor, t),
      size:
        EDGE_BASE_SIZE +
        (EDGE_PULSE_PEAK_SIZE - EDGE_BASE_SIZE) * pulseEnvelope(t),
    };
  };
}

export function GraphCanvas(): JSX.Element {
  const containerRef = useRef<HTMLDivElement>(null);
  const sigmaRef = useRef<Sigma<NodeAttributes, EdgeAttributes> | null>(null);
  const fa2Ref = useRef<FA2Layout<NodeAttributes, EdgeAttributes> | null>(null);
  const graphologyRef = useRef<GrackleMultiGraph | null>(null);
  const animRef = useRef<AnimationState>(createAnimationState());
  // Two distinct FA2-stop timers, deliberately NOT shared (they mean different
  // things): the initial-settle timer owns the one-time 5s full-graph layout
  // after a scratch build; the reheat timer owns the bounded per-re-push
  // settle. Sharing one ref let an early re-push clear the initial settle and
  // freeze a half-settled layout (10.7 review finding).
  const initialSettleTimerRef = useRef<ReturnType<typeof setTimeout> | null>(
    null
  );
  const reheatTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const rafIdRef = useRef<number | null>(null);

  const graph = useGraphStore((s) => s.graph);
  const hiddenKinds = useGraphStore((s) => s.hiddenKinds);
  const searchTerm = useGraphStore((s) => s.searchTerm);
  const excludeGlobs = useGraphStore((s) => s.excludeGlobs);
  const selectedNodeId = useGraphStore((s) => s.selectedNodeId);
  const highlightedNodeIds = useGraphStore((s) => s.highlightedNodeIds);
  const traceSessionId = useGraphStore((s) => s.traceSessionId);
  const diffOverlay = useGraphStore((s) => s.diffOverlay);
  const selectNode = useGraphStore((s) => s.selectNode);
  const selectEdge = useGraphStore((s) => s.selectEdge);
  const jumpToSourceLine = useGraphStore((s) => s.jumpToSourceLine);
  const setHighlightedNodes = useGraphStore((s) => s.setHighlightedNodes);

  const theme = useTheme((s) => s.theme);

  const { heat, maxHeat } = useHeatmap();
  const heatActive = traceSessionId !== null;

  // Unmount-only teardown. Kept as its own zero-dep effect so the
  // rebuild/apply effect below never tears anything down on a dep change —
  // that split is what lets an incremental re-push reuse the live Sigma
  // instance instead of rebuilding (see that effect for the branch).
  useEffect(() => {
    return () => {
      if (initialSettleTimerRef.current !== null)
        clearTimeout(initialSettleTimerRef.current);
      if (reheatTimerRef.current !== null) clearTimeout(reheatTimerRef.current);
      if (rafIdRef.current !== null) cancelAnimationFrame(rafIdRef.current);
      fa2Ref.current?.kill();
      fa2Ref.current = null;
      sigmaRef.current?.kill();
      sigmaRef.current = null;
      graphologyRef.current = null;
    };
  }, []);

  function scratchBuild(container: HTMLElement, graphData: Graph) {
    // StrictMode-safe (and safe when a genuinely different project replaces
    // the current one): kill stale instances before rebuilding.
    if (initialSettleTimerRef.current !== null) {
      clearTimeout(initialSettleTimerRef.current);
      initialSettleTimerRef.current = null;
    }
    if (reheatTimerRef.current !== null) {
      clearTimeout(reheatTimerRef.current);
      reheatTimerRef.current = null;
    }
    if (rafIdRef.current !== null) {
      cancelAnimationFrame(rafIdRef.current);
      rafIdRef.current = null;
    }
    animRef.current = createAnimationState();
    fa2Ref.current?.kill();
    fa2Ref.current = null;
    sigmaRef.current?.kill();
    sigmaRef.current = null;

    const graphology = buildGraphology(graphData);
    graphologyRef.current = graphology;

    const sigma = new Sigma<NodeAttributes, EdgeAttributes>(
      graphology,
      container,
      {
        // The container is an absolutely-positioned fill that may not have
        // been measured yet on the first commit (fast WS → graph arrives the
        // same frame the canvas mounts). Sigma's ResizeObserver corrects the
        // dimensions once layout settles; without this it throws on init.
        allowInvalidContainer: true,
        labelColor: { color: labelColorForTheme(theme) },
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
          diffOverlay,
          animRef
        ),
        edgeReducer: makeEdgeReducer(container, animRef),
      }
    );

    sigma.on("clickNode", ({ node }) => {
      selectNode(node);
    });
    sigma.on("clickEdge", ({ edge }) => {
      const g = graphologyRef.current;
      if (!g) return;
      const source = g.source(edge);
      const target = g.target(edge);
      selectEdge({ source, target });
      // Jump to this edge's exact evidence line (ADR-0026), read off the
      // clicked edge's own attribute so parallel edges disambiguate. The
      // path is read from the LIVE graphology node attribute, not the store
      // graph — this Sigma instance can outlive many watch-mode re-pushes
      // (10.7), so a closure over the store `graph` here would go stale.
      const line = g.getEdgeAttribute(edge, "line");
      if (typeof line === "number") {
        const path = g.getNodeAttribute(source, "path");
        if (path) jumpToSourceLine(path, line);
      }
    });
    sigma.on("clickStage", () => {
      // selectNode(null) also clears selectedEdge + sourceViewerTarget.
      selectNode(null);
      setHighlightedNodes(null);
    });

    sigmaRef.current = sigma;

    const fa2 = new FA2Layout<NodeAttributes, EdgeAttributes>(graphology, {
      settings: { barnesHutOptimize: true, gravity: 1, slowDown: 10 },
    });
    fa2.start();
    fa2Ref.current = fa2;

    initialSettleTimerRef.current = setTimeout(() => {
      fa2Ref.current?.stop();
      initialSettleTimerRef.current = null;
    }, 5000);
  }

  function reheat(live: GrackleMultiGraph, result: ApplyGraphDiffResult) {
    const fa2 = fa2Ref.current;
    if (!fa2) return;

    // During the initial full-graph settle there is no stable layout to
    // preserve yet — pinning survivors mid-settle (and stopping FA2 after the
    // short reheat window) would freeze a half-settled arrangement. So while
    // the initial settle is still pending, do nothing here: applyGraphDiff's
    // node/edge mutations already flow into the still-running FA2 (which
    // respawns on them), and the initial-settle timer owns the eventual stop.
    if (initialSettleTimerRef.current !== null) return;

    // Pin everyone except the nodes that just arrived or are fading —
    // makes "existing nodes don't scramble" a structural guarantee, not a
    // tuning outcome. Ghosts are deliberately left unpinned (they still
    // exert/receive FA2 forces for their short fade window) rather than
    // also pinning them, per the phase plan's accepted trade-off.
    const unpinned = new Set([...result.addedNodes, ...result.removedNodes]);
    live.updateEachNodeAttributes((id, attrs) => ({
      ...attrs,
      fixed: !unpinned.has(id),
    }));

    // stop()+start() (not a bare start()) forces FA2 to rebuild its position
    // matrix from the current graph — which is the ONLY place it reads the
    // `fixed` flags (graphToByteArrays). A bare start() no-ops when FA2 is
    // already running (e.g. a prior reheat still in its window), so the new
    // pins would silently not take effect until an incidental worker respawn.
    if (reheatTimerRef.current !== null) clearTimeout(reheatTimerRef.current);
    fa2.stop();
    fa2.start();
    reheatTimerRef.current = setTimeout(() => {
      fa2.stop();
      live.updateEachNodeAttributes((_id, attrs) => ({
        ...attrs,
        fixed: false,
      }));
      reheatTimerRef.current = null;
    }, REHEAT_DURATION_MS);
  }

  function tick() {
    const sigma = sigmaRef.current;
    const live = graphologyRef.current;
    if (!sigma || !live) {
      rafIdRef.current = null;
      return;
    }

    const { settledExits, active } = tickAnimations(
      animRef.current,
      performance.now()
    );
    for (const id of settledExits) {
      if (live.hasNode(id)) live.dropNode(id);
    }

    sigma.refresh();
    rafIdRef.current = active ? requestAnimationFrame(tick) : null;
  }

  function ensureAnimationLoopRunning() {
    if (rafIdRef.current === null) {
      rafIdRef.current = requestAnimationFrame(tick);
    }
  }

  // Rebuild-or-apply when the graph data changes. A fresh Sigma/FA2 pair is
  // built only on first load or when the incoming graph shares no node ids
  // with the live one (a genuinely different project — hasSurvivor gate);
  // otherwise this is a watch-mode re-push (ADR-0027) of the SAME project,
  // diffed and applied to the live graphology instead of rebuilt from
  // scratch, so FA2 positions and the camera survive. Filter/heat/diff
  // state is handled by the next effect so sigma is not torn down for that.
  // biome-ignore lint/correctness/useExhaustiveDependencies: intentional two-effect(-plus) pattern; scratchBuild/reheat close over the same store state read at effect-run time
  useEffect(() => {
    if (!containerRef.current || !graph) return;
    const container = containerRef.current;

    const live = graphologyRef.current;
    const sigma = sigmaRef.current;
    if (!sigma || !live || !hasSurvivor(live, graph)) {
      scratchBuild(container, graph);
      return;
    }

    const result = applyGraphDiff(live, graph);
    if (isEmptyDiff(result)) return; // e.g. a WS-reconnect re-push of an unchanged graph

    const now = performance.now();
    const active = recordDiffAnimations(animRef.current, result, now);
    if (active) {
      ensureAnimationLoopRunning();
    } else {
      // Reduced motion, or a change with nothing to animate (e.g. a pure
      // edge removal) — drop removed nodes synchronously; Sigma repaints
      // edge/attribute-only changes for free via its own event subscription.
      for (const id of result.removedNodes) {
        if (live.hasNode(id)) live.dropNode(id);
      }
    }

    if (result.addedNodes.length > 0 || result.removedNodes.length > 0) {
      reheat(live, result);
    }
  }, [graph]);

  // Update node reducer + refresh when filter/heat/diff state changes,
  // without rebuilding sigma. Reuses the setSetting("nodeReducer") +
  // sigma.refresh() path — same mechanism as cycle-highlight wiring. The
  // edge reducer is never re-set here: it closes over the stable `animRef`
  // object (not a snapshot), so it always sees live animation state without
  // needing to be recreated.
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
        diffOverlay,
        animRef
      )
    );
    // Labels follow the theme: white on the dark canvas, slate on light.
    sigma.setSetting("labelColor", { color: labelColorForTheme(theme) });
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
    theme,
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
