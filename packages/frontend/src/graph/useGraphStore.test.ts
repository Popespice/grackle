import { beforeEach, describe, expect, it } from "vitest";
import { useGraphStore } from "./useGraphStore";

const MOCK_GRAPH = {
  version: 1,
  language: "python",
  nodes: [
    { id: "a.py:App", kind: "class", name: "App", path: "a.py" },
    { id: "b.py:main", kind: "function", name: "main", path: "b.py" },
  ],
  edges: [{ source: "a.py:App", target: "b.py:main", kind: "call" }],
};

beforeEach(() => {
  useGraphStore.setState({
    graph: null,
    selectedNodeId: null,
    hiddenKinds: new Set<string>(),
    searchTerm: "",
    excludeGlobs: [],
  });
});

describe("useGraphStore", () => {
  it("has correct initial state", () => {
    const state = useGraphStore.getState();
    expect(state.graph).toBeNull();
    expect(state.selectedNodeId).toBeNull();
    expect(state.hiddenKinds.size).toBe(0);
    expect(state.searchTerm).toBe("");
    expect(state.excludeGlobs).toEqual([]);
  });

  it("setGraph stores the graph and resets selectedNodeId", () => {
    useGraphStore.getState().selectNode("a.py:App");
    useGraphStore.getState().setGraph(MOCK_GRAPH);
    const state = useGraphStore.getState();
    expect(state.graph).toBe(MOCK_GRAPH);
    expect(state.selectedNodeId).toBeNull();
  });

  it("selectNode updates selectedNodeId", () => {
    useGraphStore.getState().selectNode("a.py:App");
    expect(useGraphStore.getState().selectedNodeId).toBe("a.py:App");
  });

  it("selectNode accepts null to deselect", () => {
    useGraphStore.getState().selectNode("a.py:App");
    useGraphStore.getState().selectNode(null);
    expect(useGraphStore.getState().selectedNodeId).toBeNull();
  });

  it("toggleKind adds a kind to hiddenKinds", () => {
    useGraphStore.getState().toggleKind("class");
    expect(useGraphStore.getState().hiddenKinds.has("class")).toBe(true);
  });

  it("toggleKind removes a kind already in hiddenKinds", () => {
    useGraphStore.getState().toggleKind("class");
    useGraphStore.getState().toggleKind("class");
    expect(useGraphStore.getState().hiddenKinds.has("class")).toBe(false);
  });

  it("toggleKind creates a new Set instance (immutability)", () => {
    const before = useGraphStore.getState().hiddenKinds;
    useGraphStore.getState().toggleKind("function");
    const after = useGraphStore.getState().hiddenKinds;
    expect(after).not.toBe(before);
  });

  it("toggleKind preserves other hidden kinds when toggling one", () => {
    useGraphStore.getState().toggleKind("class");
    useGraphStore.getState().toggleKind("function");
    useGraphStore.getState().toggleKind("class"); // remove class
    const { hiddenKinds } = useGraphStore.getState();
    expect(hiddenKinds.has("class")).toBe(false);
    expect(hiddenKinds.has("function")).toBe(true);
  });

  it("setSearch updates searchTerm", () => {
    useGraphStore.getState().setSearch("auth");
    expect(useGraphStore.getState().searchTerm).toBe("auth");
  });

  it("setExcludes updates excludeGlobs", () => {
    useGraphStore.getState().setExcludes(["**/test/**", "*.spec.ts"]);
    expect(useGraphStore.getState().excludeGlobs).toEqual([
      "**/test/**",
      "*.spec.ts",
    ]);
  });

  it("showAllKinds clears all hidden kinds", () => {
    useGraphStore.getState().toggleKind("class");
    useGraphStore.getState().toggleKind("function");
    useGraphStore.getState().showAllKinds();
    expect(useGraphStore.getState().hiddenKinds.size).toBe(0);
  });

  it("showAllKinds creates a new Set instance", () => {
    useGraphStore.getState().toggleKind("class");
    const before = useGraphStore.getState().hiddenKinds;
    useGraphStore.getState().showAllKinds();
    expect(useGraphStore.getState().hiddenKinds).not.toBe(before);
  });
});
