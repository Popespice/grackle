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
  // Append-only during a session; 6.3 will batch appends for render performance.
  // ---------------------------------------------------------------------------
  traceEvents: TraceEvent[];
  traceSessionId: string | null;
  traceSessionComplete: boolean;
  setGraph: (graph: Graph) => void;
  selectNode: (nodeId: string | null) => void;
  setHighlightedNodes: (ids: string[] | null) => void;
  toggleKind: (kind: string) => void;
  showAllKinds: () => void;
  setSearch: (term: string) => void;
  setExcludes: (globs: string[]) => void;
  startTraceSession: (sessionId: string) => void;
  addTraceEvent: (ev: TraceEvent) => void;
  endTraceSession: (msg: TraceSessionEndMessage) => void;
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
    }),
  addTraceEvent: (ev) =>
    set((state) => ({ traceEvents: [...state.traceEvents, ev] })),
  endTraceSession: (_msg: TraceSessionEndMessage) =>
    set({ traceSessionComplete: true }),
}));
