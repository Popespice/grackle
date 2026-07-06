import type {
  Graph,
  TraceEvent,
  TraceSessionEndMessage,
} from "@grackle/shared-types";
import { create } from "zustand";
import type { DiffStatus } from "./diff";

interface GraphStoreState {
  graph: Graph | null;
  selectedNodeId: string | null;
  /**
   * The edge the user picked in the graph (or is inspecting), identified by its
   * endpoints.  Mutually exclusive with ``selectedNodeId`` for single-selection
   * UX.  Drives the EdgeEvidencePanel (Phase 10.4, ADR-0026).
   */
  selectedEdge: { source: string; target: string } | null;
  /**
   * An explicit source-viewer jump target (path + 1-based line) that overrides
   * the node-derived path/line.  Set by ``jumpToSourceLine`` when the user
   * clicks edge evidence whose line is NOT a node definition line (e.g. a call
   * site deep in a function body).  Cleared on ``selectNode`` so a plain node
   * selection falls back to the node's definition line. (Phase 10.4.)
   */
  sourceViewerTarget: { path: string; line: number } | null;
  highlightedNodeIds: Set<string> | null;
  hiddenKinds: Set<string>;
  searchTerm: string;
  excludeGlobs: string[];
  // ---------------------------------------------------------------------------
  // Trace slice — populated by the runtime overlay (Phase 6.2+).
  // Append-only during a session.
  // Phase 7.1: addTraceEvents(batch) is the preferred path — one concat per
  // rAF frame instead of one spread per event, lowering ingest cost from
  // O(n²) to O(n²/B) where B = batch size.
  // ---------------------------------------------------------------------------
  traceEvents: TraceEvent[];
  traceSessionId: string | null;
  traceSessionComplete: boolean;
  // Playback state (Phase 6.3+)
  /** Absolute index into the trace (0..traceTotal in seekable mode, 0..traceEvents.length otherwise). */
  tracePlayhead: number;
  tracePlaying: boolean;
  tracePlaybackSpeed: number; // multiplier: 1, 2, 4, …
  traceEventTypeFilter: Set<string>; // empty = all event kinds count
  traceHeatMode: "cumulative" | "sliding";
  traceWindowSize: number; // event span for sliding mode
  // Server-side seek (Phase 7.3+)
  /** True when the server supports trace_seek_request for the current session. */
  traceSeekable: boolean;
  /** Total number of events in the trace file (for scrubber sizing in seekable mode). */
  traceTotal: number;
  /** Absolute index of the first event in the current traceEvents window (seekable mode). */
  traceWindowStart: number;
  /** Agent-computed cumulative heat from the last trace_query_response (seekable mode). */
  agentHeat: Record<string, number> | null;
  // Differential analysis (Phase 8.4+)
  /**
   * Baseline node→count snapshot captured by the user via "Set as baseline"
   * in DiffPanel.  When set, DiffPanel switches from trace-vs-static to
   * trace-vs-trace mode using the current session's counts vs. this baseline.
   */
  diffBaseline: Record<string, number> | null;
  /**
   * Per-node diff status overlay for GraphCanvas.  `null` = no overlay active.
   * Set by DiffPanel when it computes a diff; cleared on graph change or when
   * the user dismisses the overlay.
   */
  diffOverlay: Map<string, DiffStatus> | null;
  // Graph actions
  setGraph: (graph: Graph) => void;
  selectNode: (nodeId: string | null) => void;
  selectEdge: (edge: { source: string; target: string } | null) => void;
  /** Set an explicit source-viewer jump target (path + 1-based line). */
  jumpToSourceLine: (path: string, line: number) => void;
  setHighlightedNodes: (ids: string[] | null) => void;
  toggleKind: (kind: string) => void;
  showAllKinds: () => void;
  setSearch: (term: string) => void;
  setExcludes: (globs: string[]) => void;
  // Trace session actions
  startTraceSession: (sessionId: string, seekable?: boolean) => void;
  addTraceEvent: (ev: TraceEvent) => void;
  /** Batch append — O(n) single concat instead of O(n²) per-event spread. */
  addTraceEvents: (batch: TraceEvent[]) => void;
  endTraceSession: (msg: TraceSessionEndMessage) => void;
  /**
   * Replace the current event window with a seek result (seekable sessions).
   *
   * Sets ``traceWindowStart``, ``traceTotal``, and ``traceEvents`` to the
   * window returned by a ``trace_window`` response.  ``tracePlayhead`` is an
   * absolute index (0..traceTotal) and is preserved; it is only clamped down
   * to ``total`` if it somehow exceeds the total event count.
   */
  setTraceWindow: (start: number, events: TraceEvent[], total: number) => void;
  /**
   * Directly set ``traceSeekable``.  Used to fall back to non-seekable mode
   * if the initial seek request fails at session start.
   */
  setTraceSeekable: (seekable: boolean) => void;
  // Playback actions
  setPlayhead: (i: number) => void;
  play: () => void;
  pause: () => void;
  setSpeed: (speed: number) => void;
  toggleEventType: (kind: string) => void;
  setHeatMode: (mode: "cumulative" | "sliding") => void;
  setWindowSize: (n: number) => void;
  setAgentHeat: (heat: Record<string, number>) => void;
  clearAgentHeat: () => void;
  // Diff actions
  setDiffBaseline: (counts: Record<string, number>) => void;
  clearDiffBaseline: () => void;
  setDiffOverlay: (overlay: Map<string, DiffStatus>) => void;
  clearDiffOverlay: () => void;
}

export const useGraphStore = create<GraphStoreState>()((set) => ({
  graph: null,
  selectedNodeId: null,
  selectedEdge: null,
  sourceViewerTarget: null,
  highlightedNodeIds: null,
  hiddenKinds: new Set<string>(),
  searchTerm: "",
  excludeGlobs: [],
  traceEvents: [],
  traceSessionId: null,
  traceSessionComplete: false,
  tracePlayhead: 0,
  tracePlaying: false,
  tracePlaybackSpeed: 1,
  traceEventTypeFilter: new Set<string>(),
  traceHeatMode: "cumulative",
  traceWindowSize: 200,
  traceSeekable: false,
  traceTotal: 0,
  traceWindowStart: 0,
  agentHeat: null,
  diffBaseline: null,
  diffOverlay: null,
  setGraph: (graph) =>
    set({
      graph,
      selectedNodeId: null,
      selectedEdge: null,
      sourceViewerTarget: null,
      highlightedNodeIds: null,
      // Clear diff overlay AND baseline when the static graph changes — both
      // are keyed by node ID, which is graph-scoped. A baseline captured from a
      // different graph would classify its (now-absent) nodes as phantom "gone"
      // entries. (The baseline is deliberately PRESERVED across trace sessions
      // on the same graph — that is the trace-vs-trace compare feature.)
      diffOverlay: null,
      diffBaseline: null,
    }),
  selectNode: (nodeId) =>
    // A plain node selection clears any picked edge and any explicit source
    // jump target, so the SourceViewer falls back to the node's definition.
    set({
      selectedNodeId: nodeId,
      selectedEdge: null,
      sourceViewerTarget: null,
    }),
  selectEdge: (edge) =>
    // Picking an edge clears the node selection (single-selection UX) AND any
    // prior source-jump target. A line-bearing edge immediately re-sets the
    // target via jumpToSourceLine (called alongside in GraphCanvas.clickEdge);
    // a line-less edge (Go method-set, stale-cache cross-language) leaves it
    // cleared so the SourceViewer shows its placeholder instead of a stale
    // file/line from the previously-picked edge.
    set({ selectedEdge: edge, selectedNodeId: null, sourceViewerTarget: null }),
  jumpToSourceLine: (path, line) => set({ sourceViewerTarget: { path, line } }),
  setHighlightedNodes: (ids) =>
    set({ highlightedNodeIds: ids ? new Set(ids) : null }),
  toggleKind: (kind) =>
    set((state) => {
      const next = new Set(state.hiddenKinds);
      if (next.has(kind)) {
        next.delete(kind);
      } else {
        next.add(kind);
      }
      return { hiddenKinds: next };
    }),
  showAllKinds: () => set({ hiddenKinds: new Set<string>() }),
  setSearch: (term) => set({ searchTerm: term }),
  setExcludes: (globs) => set({ excludeGlobs: globs }),
  startTraceSession: (sessionId, seekable = false) =>
    set({
      traceSessionId: sessionId,
      traceEvents: [],
      traceSessionComplete: false,
      // Reset playback position; keep filter + heat mode across re-runs
      tracePlayhead: 0,
      tracePlaying: false,
      // Seek state — reset on each new session
      traceSeekable: seekable,
      traceTotal: 0,
      traceWindowStart: 0,
      agentHeat: null,
      // Clear diff overlay on new session — it will be recomputed by DiffPanel.
      diffOverlay: null,
    }),
  addTraceEvent: (ev) =>
    set((state) => ({ traceEvents: state.traceEvents.concat([ev]) })),
  addTraceEvents: (batch) =>
    set((state) => ({ traceEvents: state.traceEvents.concat(batch) })),
  endTraceSession: (_msg: TraceSessionEndMessage) =>
    set({ traceSessionComplete: true }),
  setTraceWindow: (start, events, total) =>
    set((state) => ({
      traceWindowStart: start,
      traceEvents: events,
      traceTotal: total,
      // Preserve the absolute playhead — only clamp it down to total if it
      // somehow exceeds the new total (e.g. server trace was truncated).
      // Do NOT clamp to events.length (the window size) — the playhead is an
      // absolute position in the full trace, not an index into the window.
      tracePlayhead: Math.min(state.tracePlayhead, total),
    })),
  setTraceSeekable: (seekable) => set({ traceSeekable: seekable }),
  setPlayhead: (i) =>
    set((state) => ({
      // In seekable mode the scrubber represents the full trace; clamp to
      // traceTotal.  In buffered mode clamp to the accumulated event count.
      tracePlayhead: Math.max(
        0,
        Math.min(
          i,
          state.traceSeekable ? state.traceTotal : state.traceEvents.length
        )
      ),
      tracePlaying: false,
    })),
  play: () =>
    set((state) => {
      // In seekable mode, "at the end" means the playhead is at or past
      // the full trace total; in buffered mode it means past the window.
      const bound = state.traceSeekable
        ? state.traceTotal
        : state.traceEvents.length;
      return {
        tracePlayhead: state.tracePlayhead >= bound ? 0 : state.tracePlayhead,
        tracePlaying: true,
      };
    }),
  pause: () => set({ tracePlaying: false }),
  setSpeed: (speed) => set({ tracePlaybackSpeed: speed }),
  toggleEventType: (kind) =>
    set((state) => {
      const next = new Set(state.traceEventTypeFilter);
      if (next.has(kind)) {
        next.delete(kind);
      } else {
        next.add(kind);
      }
      return { traceEventTypeFilter: next };
    }),
  setHeatMode: (mode) => set({ traceHeatMode: mode }),
  setWindowSize: (n) => set({ traceWindowSize: n }),
  setAgentHeat: (heat) => set({ agentHeat: heat }),
  clearAgentHeat: () => set({ agentHeat: null }),
  setDiffBaseline: (counts) => set({ diffBaseline: counts }),
  clearDiffBaseline: () => set({ diffBaseline: null }),
  setDiffOverlay: (overlay) => set({ diffOverlay: overlay }),
  clearDiffOverlay: () => set({ diffOverlay: null }),
}));
