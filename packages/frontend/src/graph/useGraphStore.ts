import type { Graph } from "@grackle/shared-types";
import { create } from "zustand";

interface GraphStoreState {
  graph: Graph | null;
  selectedNodeId: string | null;
  hiddenKinds: Set<string>;
  searchTerm: string;
  excludeGlobs: string[];
  setGraph: (graph: Graph) => void;
  selectNode: (nodeId: string | null) => void;
  toggleKind: (kind: string) => void;
  setSearch: (term: string) => void;
  setExcludes: (globs: string[]) => void;
}

export const useGraphStore = create<GraphStoreState>()((set) => ({
  graph: null,
  selectedNodeId: null,
  hiddenKinds: new Set<string>(),
  searchTerm: "",
  excludeGlobs: [],
  setGraph: (graph) => set({ graph, selectedNodeId: null }),
  selectNode: (nodeId) => set({ selectedNodeId: nodeId }),
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
  setSearch: (term) => set({ searchTerm: term }),
  setExcludes: (globs) => set({ excludeGlobs: globs }),
}));
