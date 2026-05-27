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
  tracePlayhead: number; // index into traceEvents, 0..N
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
   * window returned by a ``trace_window`` response.  ``tracePlayhead`` is
   * clamped to ``[0, events.length]`` so it stays within the new window.
   */
  setTraceWindow: (start: number, events: TraceEvent[], total: number) => void;
  // Playback actions
  setPlayhead: (i: number) => void;
  play: () => void;
  pause: () => void;
  setSpeed: (speed: number) => void;
  toggleEventType: (kind: string) => void;
  setHeatMode: (mode: "cumulative" | "sliding") => void;
  setWindowSize: (n: number) => void;
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
      // Clamp playhead into the new window
      tracePlayhead: Math.max(0, Math.min(state.tracePlayhead, events.length)),
    })),
  setPlayhead: (i) =>
    set((state) => ({
      tracePlayhead: Math.max(0, Math.min(i, state.traceEvents.length)),
      tracePlaying: false,
    })),
  play: () =>
    set((state) => ({
      // Rewind to 0 if already at the end
      tracePlayhead:
        state.tracePlayhead >= state.traceEvents.length
          ? 0
          : state.tracePlayhead,
      tracePlaying: true,
    })),
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
}));
