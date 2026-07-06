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
    selectedEdge: null,
    sourceViewerTarget: null,
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
    // Phase 7.3 seek state — always reset so tests are hermetic.
    traceSeekable: false,
    traceTotal: 0,
    traceWindowStart: 0,
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

  // -------------------------------------------------------------------------
  // addTraceEvents (Phase 7.1 — batched ingest)
  // -------------------------------------------------------------------------

  it("addTraceEvents appends a batch in insertion order", () => {
    useGraphStore.getState().startTraceSession("s1");
    const batch = [
      { event: "call", node_id: "a", ts_ns: 1, thread_id: 1, frame_depth: 0 },
      { event: "call", node_id: "b", ts_ns: 2, thread_id: 1, frame_depth: 1 },
      { event: "return", node_id: "a", ts_ns: 3, thread_id: 1, frame_depth: 0 },
    ];
    useGraphStore.getState().addTraceEvents(batch);
    const { traceEvents } = useGraphStore.getState();
    expect(traceEvents).toHaveLength(3);
    expect(traceEvents[0]?.node_id).toBe("a");
    expect(traceEvents[1]?.node_id).toBe("b");
    expect(traceEvents[2]?.node_id).toBe("a");
  });

  it("addTraceEvents appends to existing events", () => {
    useGraphStore.getState().startTraceSession("s1");
    useGraphStore.getState().addTraceEvent({
      event: "call",
      node_id: "pre",
      ts_ns: 0,
      thread_id: 1,
      frame_depth: 0,
    });
    useGraphStore
      .getState()
      .addTraceEvents([
        { event: "call", node_id: "x", ts_ns: 1, thread_id: 1, frame_depth: 0 },
      ]);
    const { traceEvents } = useGraphStore.getState();
    expect(traceEvents).toHaveLength(2);
    expect(traceEvents[0]?.node_id).toBe("pre");
    expect(traceEvents[1]?.node_id).toBe("x");
  });

  it("addTraceEvents with empty batch is a no-op", () => {
    useGraphStore.getState().startTraceSession("s1");
    useGraphStore.getState().addTraceEvents([]);
    expect(useGraphStore.getState().traceEvents).toHaveLength(0);
  });

  it("addTraceEvents does not affect tracePlayhead", () => {
    useGraphStore.setState({
      tracePlayhead: 3,
      traceEvents: [
        { event: "call", node_id: "a", ts_ns: 0, thread_id: 1, frame_depth: 0 },
        { event: "call", node_id: "b", ts_ns: 1, thread_id: 1, frame_depth: 0 },
        { event: "call", node_id: "c", ts_ns: 2, thread_id: 1, frame_depth: 0 },
      ],
    });
    useGraphStore
      .getState()
      .addTraceEvents([
        { event: "call", node_id: "d", ts_ns: 3, thread_id: 1, frame_depth: 0 },
      ]);
    expect(useGraphStore.getState().tracePlayhead).toBe(3);
    expect(useGraphStore.getState().traceEvents).toHaveLength(4);
  });

  it("addTraceEvents produces same result as N addTraceEvent calls", () => {
    const evs = Array.from({ length: 5 }, (_, i) => ({
      event: "call",
      node_id: `fn_${i}`,
      ts_ns: i,
      thread_id: 1,
      frame_depth: i,
    }));

    // One by one
    useGraphStore.getState().startTraceSession("s-one-by-one");
    for (const ev of evs) useGraphStore.getState().addTraceEvent(ev);
    const oneByOne = useGraphStore.getState().traceEvents;

    // As a batch
    useGraphStore.getState().startTraceSession("s-batch");
    useGraphStore.getState().addTraceEvents(evs);
    const batched = useGraphStore.getState().traceEvents;

    expect(batched).toEqual(oneByOne);
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
    // Events still retained (6.3 allows scrubbing).
    expect(useGraphStore.getState().traceSessionId).toBe("s1");
  });

  // -------------------------------------------------------------------------
  // Playback slice (Phase 6.3)
  // -------------------------------------------------------------------------

  it("has correct initial playback state", () => {
    const state = useGraphStore.getState();
    expect(state.tracePlayhead).toBe(0);
    expect(state.tracePlaying).toBe(false);
    expect(state.tracePlaybackSpeed).toBe(1);
    expect(state.traceEventTypeFilter.size).toBe(0);
    expect(state.traceHeatMode).toBe("cumulative");
    expect(state.traceWindowSize).toBe(200);
  });

  it("startTraceSession resets playhead and playing, preserves heat mode", () => {
    useGraphStore.setState({
      traceHeatMode: "sliding",
      tracePlayhead: 5,
      tracePlaying: true,
    });
    useGraphStore.getState().startTraceSession("s2");
    const state = useGraphStore.getState();
    expect(state.tracePlayhead).toBe(0);
    expect(state.tracePlaying).toBe(false);
    expect(state.traceHeatMode).toBe("sliding"); // preserved
  });

  it("setPlayhead clamps to [0, events.length] and pauses", () => {
    useGraphStore.setState({
      traceEvents: [
        { event: "call", node_id: "a", ts_ns: 0, thread_id: 1, frame_depth: 0 },
        { event: "call", node_id: "b", ts_ns: 1, thread_id: 1, frame_depth: 0 },
      ],
      tracePlaying: true,
    });
    useGraphStore.getState().setPlayhead(10); // overshoot
    expect(useGraphStore.getState().tracePlayhead).toBe(2); // clamped
    expect(useGraphStore.getState().tracePlaying).toBe(false);

    useGraphStore.getState().setPlayhead(-5); // undershoot
    expect(useGraphStore.getState().tracePlayhead).toBe(0);
  });

  it("play sets tracePlaying:true and rewinds if at end", () => {
    useGraphStore.setState({
      traceEvents: [
        { event: "call", node_id: "a", ts_ns: 0, thread_id: 1, frame_depth: 0 },
      ],
      tracePlayhead: 1, // at end
    });
    useGraphStore.getState().play();
    expect(useGraphStore.getState().tracePlaying).toBe(true);
    expect(useGraphStore.getState().tracePlayhead).toBe(0); // rewound
  });

  it("play does not rewind if not at end", () => {
    useGraphStore.setState({
      traceEvents: [
        { event: "call", node_id: "a", ts_ns: 0, thread_id: 1, frame_depth: 0 },
        { event: "call", node_id: "b", ts_ns: 1, thread_id: 1, frame_depth: 0 },
      ],
      tracePlayhead: 1,
    });
    useGraphStore.getState().play();
    expect(useGraphStore.getState().tracePlayhead).toBe(1); // unchanged
    expect(useGraphStore.getState().tracePlaying).toBe(true);
  });

  it("pause sets tracePlaying:false", () => {
    useGraphStore.setState({ tracePlaying: true });
    useGraphStore.getState().pause();
    expect(useGraphStore.getState().tracePlaying).toBe(false);
  });

  it("setSpeed updates tracePlaybackSpeed", () => {
    useGraphStore.getState().setSpeed(4);
    expect(useGraphStore.getState().tracePlaybackSpeed).toBe(4);
  });

  it("toggleEventType adds and removes event kind", () => {
    useGraphStore.getState().toggleEventType("call");
    expect(useGraphStore.getState().traceEventTypeFilter.has("call")).toBe(
      true
    );
    useGraphStore.getState().toggleEventType("call");
    expect(useGraphStore.getState().traceEventTypeFilter.has("call")).toBe(
      false
    );
  });

  it("toggleEventType creates a new Set instance (immutability)", () => {
    const before = useGraphStore.getState().traceEventTypeFilter;
    useGraphStore.getState().toggleEventType("return");
    expect(useGraphStore.getState().traceEventTypeFilter).not.toBe(before);
  });

  it("setHeatMode updates traceHeatMode", () => {
    useGraphStore.getState().setHeatMode("sliding");
    expect(useGraphStore.getState().traceHeatMode).toBe("sliding");
    useGraphStore.getState().setHeatMode("cumulative");
    expect(useGraphStore.getState().traceHeatMode).toBe("cumulative");
  });

  it("setWindowSize updates traceWindowSize", () => {
    useGraphStore.getState().setWindowSize(500);
    expect(useGraphStore.getState().traceWindowSize).toBe(500);
  });

  // -------------------------------------------------------------------------
  // Server-side seek (Phase 7.3)
  // -------------------------------------------------------------------------

  it("startTraceSession with seekable=true sets traceSeekable and resets seek state", () => {
    useGraphStore.setState({ traceTotal: 999, traceWindowStart: 50 });
    useGraphStore.getState().startTraceSession("seek-session", true);
    const state = useGraphStore.getState();
    expect(state.traceSeekable).toBe(true);
    expect(state.traceTotal).toBe(0);
    expect(state.traceWindowStart).toBe(0);
    expect(state.tracePlayhead).toBe(0);
  });

  it("startTraceSession without seekable argument defaults to non-seekable", () => {
    useGraphStore.getState().startTraceSession("plain-session");
    expect(useGraphStore.getState().traceSeekable).toBe(false);
  });

  it("setTraceSeekable toggles traceSeekable flag", () => {
    useGraphStore.getState().setTraceSeekable(true);
    expect(useGraphStore.getState().traceSeekable).toBe(true);
    useGraphStore.getState().setTraceSeekable(false);
    expect(useGraphStore.getState().traceSeekable).toBe(false);
  });

  it("setTraceWindow updates window fields and preserves absolute playhead", () => {
    // Playhead at absolute position 5000; window is [3000..3200].
    useGraphStore.setState({ traceSeekable: true, tracePlayhead: 5000 });
    const window = [
      { event: "call", node_id: "a", ts_ns: 0, thread_id: 1, frame_depth: 0 },
    ];
    useGraphStore.getState().setTraceWindow(3000, window, 10000);
    const state = useGraphStore.getState();
    expect(state.traceWindowStart).toBe(3000);
    expect(state.traceEvents).toBe(window);
    expect(state.traceTotal).toBe(10000);
    // Absolute playhead is preserved — NOT clamped to window size (1).
    expect(state.tracePlayhead).toBe(5000);
  });

  it("setTraceWindow clamps playhead only when it exceeds the new total", () => {
    useGraphStore.setState({ traceSeekable: true, tracePlayhead: 99999 });
    useGraphStore.getState().setTraceWindow(0, [], 10000);
    expect(useGraphStore.getState().tracePlayhead).toBe(10000);
  });

  it("setPlayhead in seekable mode clamps to traceTotal, not window size", () => {
    useGraphStore.setState({
      traceSeekable: true,
      traceTotal: 10000,
      traceEvents: [
        { event: "call", node_id: "a", ts_ns: 0, thread_id: 1, frame_depth: 0 },
      ], // window size = 1
    });
    useGraphStore.getState().setPlayhead(5000);
    // Must not clamp to 1 (window size); must accept up to 10000 (total).
    expect(useGraphStore.getState().tracePlayhead).toBe(5000);
  });

  it("setPlayhead in seekable mode clamps above traceTotal", () => {
    useGraphStore.setState({ traceSeekable: true, traceTotal: 100 });
    useGraphStore.getState().setPlayhead(999);
    expect(useGraphStore.getState().tracePlayhead).toBe(100);
  });

  it("play in seekable mode rewinds based on traceTotal", () => {
    useGraphStore.setState({
      traceSeekable: true,
      traceTotal: 100,
      tracePlayhead: 100, // at end by total
      traceEvents: [
        { event: "call", node_id: "a", ts_ns: 0, thread_id: 1, frame_depth: 0 },
      ], // window size = 1 (not the bound)
    });
    useGraphStore.getState().play();
    expect(useGraphStore.getState().tracePlayhead).toBe(0); // rewound
    expect(useGraphStore.getState().tracePlaying).toBe(true);
  });

  // -------------------------------------------------------------------------
  // Edge evidence (Phase 10.4)
  // -------------------------------------------------------------------------

  it("selectEdge sets selectedEdge and clears selectedNodeId + sourceViewerTarget", () => {
    useGraphStore.getState().selectNode("a.py:App");
    // A prior jump target (from a previously-picked line-bearing edge) must not
    // survive picking a new edge — else clicking a line-less edge next leaves
    // the SourceViewer pinned to the old file/line (regression guard).
    useGraphStore.getState().jumpToSourceLine("a.py", 42);
    useGraphStore
      .getState()
      .selectEdge({ source: "a.py:App", target: "b.py:main" });
    const state = useGraphStore.getState();
    expect(state.selectedEdge).toEqual({
      source: "a.py:App",
      target: "b.py:main",
    });
    expect(state.selectedNodeId).toBeNull();
    expect(state.sourceViewerTarget).toBeNull();
  });

  it("jumpToSourceLine sets an explicit source-viewer target", () => {
    useGraphStore.getState().jumpToSourceLine("a.py", 7);
    expect(useGraphStore.getState().sourceViewerTarget).toEqual({
      path: "a.py",
      line: 7,
    });
  });

  it("selectNode clears a prior edge selection AND source-viewer target", () => {
    // Simulate clicking an edge row (sets a jump target) then picking a node.
    useGraphStore
      .getState()
      .selectEdge({ source: "a.py:App", target: "b.py:main" });
    useGraphStore.getState().jumpToSourceLine("b.py", 3);
    useGraphStore.getState().selectNode("a.py:App");
    const state = useGraphStore.getState();
    expect(state.selectedNodeId).toBe("a.py:App");
    // Both must clear so the SourceViewer falls back to the node's definition
    // (regression guard: a stale target would show the wrong file/line).
    expect(state.selectedEdge).toBeNull();
    expect(state.sourceViewerTarget).toBeNull();
  });

  it("setGraph clears selectedEdge and sourceViewerTarget", () => {
    useGraphStore
      .getState()
      .selectEdge({ source: "a.py:App", target: "b.py:main" });
    useGraphStore.getState().jumpToSourceLine("a.py", 2);
    useGraphStore.getState().setGraph(MOCK_GRAPH);
    const state = useGraphStore.getState();
    expect(state.selectedEdge).toBeNull();
    expect(state.sourceViewerTarget).toBeNull();
  });
});
