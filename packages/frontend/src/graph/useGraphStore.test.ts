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
    traceEvents: [],
    traceSessionId: null,
    traceSessionComplete: false,
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

  // -------------------------------------------------------------------------
  // Trace slice
  // -------------------------------------------------------------------------

  it("has correct initial trace state", () => {
    const state = useGraphStore.getState();
    expect(state.traceEvents).toEqual([]);
    expect(state.traceSessionId).toBeNull();
    expect(state.traceSessionComplete).toBe(false);
  });

  it("startTraceSession sets sessionId and clears prior events", () => {
    // Populate events first so we can verify they are cleared.
    useGraphStore.setState({
      traceEvents: [
        {
          event: "call",
          node_id: "old.py:f",
          ts_ns: 1,
          thread_id: 1,
          frame_depth: 0,
        },
      ],
      traceSessionComplete: true,
    });

    useGraphStore.getState().startTraceSession("session-1");
    const state = useGraphStore.getState();
    expect(state.traceSessionId).toBe("session-1");
    expect(state.traceEvents).toEqual([]);
    expect(state.traceSessionComplete).toBe(false);
  });

  it("addTraceEvent appends without mutation", () => {
    useGraphStore.getState().startTraceSession("s1");
    const ev = {
      event: "call",
      node_id: "app.py:main",
      ts_ns: 42,
      thread_id: 1,
      frame_depth: 0,
    };
    useGraphStore.getState().addTraceEvent(ev);
    const state = useGraphStore.getState();
    expect(state.traceEvents).toHaveLength(1);
    expect(state.traceEvents[0]).toEqual(ev);
  });

  it("addTraceEvent accumulates multiple events in order", () => {
    useGraphStore.getState().startTraceSession("s1");
    for (let i = 0; i < 5; i++) {
      useGraphStore.getState().addTraceEvent({
        event: "call",
        node_id: `app.py:fn_${i}`,
        ts_ns: i,
        thread_id: 1,
        frame_depth: i,
      });
    }
    const { traceEvents } = useGraphStore.getState();
    expect(traceEvents).toHaveLength(5);
    expect(traceEvents[4]?.node_id).toBe("app.py:fn_4");
  });

  it("endTraceSession marks traceSessionComplete", () => {
    useGraphStore.getState().startTraceSession("s1");
    useGraphStore.getState().endTraceSession({
      id: "e1",
      type: "trace_session_end",
      payload: { session_id: "s1", ended_ns: 9999, event_count: 0 },
    });
    expect(useGraphStore.getState().traceSessionComplete).toBe(true);
    // Events still retained (6.3 will allow scrubbing).
    expect(useGraphStore.getState().traceSessionId).toBe("s1");
  });
});
