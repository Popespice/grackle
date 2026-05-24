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
  // NOTE: addTraceEvent uses [...spread] which is O(n²). Batching is deferred
  // to Phase 7 — render cadence is decoupled via the rAF playback loop so
  // individual appends do not trigger per-event Sigma refreshes.
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
  // Graph actions
  setGraph: (graph: Graph) => void;
  selectNode: (nodeId: string | null) => void;
  setHighlightedNodes: (ids: string[] | null) => void;
  toggleKind: (kind: string) => void;
  showAllKinds: () => void;
  setSearch: (term: string) => void;
  setExcludes: (globs: string[]) => void;
  // Trace session actions
  startTraceSession: (sessionId: string) => void;
  addTraceEvent: (ev: TraceEvent) => void;
  endTraceSession: (msg: TraceSessionEndMessage) => void;
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
  startTraceSession: (sessionId) =>
    set({
      traceSessionId: sessionId,
      traceEvents: [],
      traceSessionComplete: false,
      // Reset playback position; keep filter + heat mode across re-runs
      tracePlayhead: 0,
      tracePlaying: false,
    }),
  addTraceEvent: (ev) =>
    set((state) => ({ traceEvents: [...state.traceEvents, ev] })),
  endTraceSession: (_msg: TraceSessionEndMessage) =>
    set({ traceSessionComplete: true }),
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
