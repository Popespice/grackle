import type {
  Graph,
  TraceEvent,
  TraceSessionEndMessage,
} from "@grackle/shared-types";
import { create } from "zustand";

interface GraphStoreState {
  graph: Graph | null;
  selectedNodeId: string | null;
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
  /** Agent-computed coverage count from the last trace_query_response (seekable mode). */
  agentCoverageCount: number | null;
  // Graph actions
  setGraph: (graph: Graph) => void;
  selectNode: (nodeId: string | null) => void;
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
  setAgentHeat: (heat: Record<string, number>, coverageCount?: number) => void;
  clearAgentHeat: () => void;
}

export const useGraphStore = create<GraphStoreState>()((set) => ({
  graph: null,
  selectedNodeId: null,
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
  agentCoverageCount: null,
  setGraph: (graph) =>
    set({ graph, selectedNodeId: null, highlightedNodeIds: null }),
  selectNode: (nodeId) => set({ selectedNodeId: nodeId }),
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
      agentCoverageCount: null,
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
  setAgentHeat: (heat, coverageCount) =>
    set({ agentHeat: heat, agentCoverageCount: coverageCount ?? null }),
  clearAgentHeat: () => set({ agentHeat: null, agentCoverageCount: null }),
}));
