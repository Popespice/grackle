import type { Graph, TraceEvent } from "@grackle/shared-types";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { MAX_FIRINGS } from "../graph/causalPath";
import { type UseFullTraceResult, useFullTrace } from "../graph/useFullTrace";
import { useGraphStore } from "../graph/useGraphStore";
import { CausalPathPanel } from "./CausalPathPanel";

vi.mock("../graph/useFullTrace");
const mockUseFullTrace = vi.mocked(useFullTrace);

function fullTrace(over: Partial<UseFullTraceResult> = {}): UseFullTraceResult {
  return {
    events: [],
    truncated: false,
    loading: false,
    error: false,
    loaded: false,
    load: vi.fn(),
    ...over,
  };
}

const GRAPH: Graph = {
  version: 1,
  language: "python",
  nodes: [
    { id: "a.py:main", kind: "function", name: "main", path: "a.py" },
    { id: "a.py:handle", kind: "function", name: "handle", path: "a.py" },
    { id: "a.py:validate", kind: "function", name: "validate", path: "a.py" },
    { id: "a.py:unused", kind: "function", name: "unused", path: "a.py" },
    { id: "a.py:helper", kind: "function", name: "helper", path: "a.py" },
    { id: "a.py:f", kind: "function", name: "f", path: "a.py" },
    { id: "a.py:hot", kind: "function", name: "hot", path: "a.py" },
  ],
  edges: [
    {
      source: "a.py:main",
      target: "a.py:handle",
      kind: "call",
      metadata: { line: 31 },
    },
    {
      source: "a.py:handle",
      target: "a.py:validate",
      kind: "call",
      metadata: { line: 14 },
    },
  ],
} as unknown as Graph;

function call(
  id: string,
  depth: number,
  values?: TraceEvent["values"],
  tsNs?: number,
  threadId = 1
): TraceEvent {
  const base: TraceEvent = {
    event: "call",
    node_id: id,
    ts_ns: tsNs ?? depth + 1,
    thread_id: threadId,
    frame_depth: depth,
  };
  return values ? { ...base, values } : base;
}

function ret(
  id: string,
  depth: number,
  tsNs: number,
  threadId = 1
): TraceEvent {
  return {
    event: "return",
    node_id: id,
    ts_ns: tsNs,
    thread_id: threadId,
    frame_depth: depth,
  };
}

// Captured once, before any test mutates the shared module-level store —
// the real (non-mocked) actions. Several tests below override an action
// (selectNode/setPlayhead/setHighlightedNodes/jumpToSourceLine) with a
// vi.fn() via a partial setState merge; a plain merge in beforeEach would
// never restore those, silently leaking a stub into every later test that
// doesn't re-override it. A full replace (setState(_, true)) resets the
// store to this pristine snapshot — actions included — before each test.
const INITIAL_STORE_STATE = useGraphStore.getState();

afterEach(cleanup);

beforeEach(() => {
  mockUseFullTrace.mockReturnValue(fullTrace());
  useGraphStore.setState(INITIAL_STORE_STATE, true);
  useGraphStore.setState({
    graph: null,
    selectedNodeId: null,
    highlightedNodeIds: null,
    traceEvents: [],
    traceSessionId: null,
    tracePlayhead: 0,
    traceSeekable: false,
    traceTotal: 0,
  });
});

describe("CausalPathPanel", () => {
  it("renders nothing without a node selection", () => {
    useGraphStore.setState({ graph: GRAPH, traceSessionId: "s1" });
    const { container } = render(<CausalPathPanel />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing without a trace session", () => {
    useGraphStore.setState({ graph: GRAPH, selectedNodeId: "a.py:main" });
    const { container } = render(<CausalPathPanel />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing without a graph", () => {
    useGraphStore.setState({
      graph: null,
      selectedNodeId: "a.py:main",
      traceSessionId: "s1",
    });
    const { container } = render(<CausalPathPanel />);
    expect(container.firstChild).toBeNull();
  });

  it("reports that the node did not fire in the loaded trace", () => {
    const events = [call("a.py:main", 0)];
    mockUseFullTrace.mockReturnValue(fullTrace({ events, loaded: true }));
    useGraphStore.setState({
      graph: GRAPH,
      selectedNodeId: "a.py:unused",
      traceSessionId: "s1",
    });
    render(<CausalPathPanel />);
    expect(
      screen.getByText("This node did not fire in the loaded trace.")
    ).toBeInTheDocument();
  });

  it("renders the causal path outermost-first with THIS highlighted and per-hop args", () => {
    const events: TraceEvent[] = [
      call("a.py:main", 0, { args: [] }),
      call("a.py:handle", 1, { args: [{ name: "req", repr: "<Request>" }] }),
      call("a.py:validate", 2, { args: [{ name: "email", repr: '"a@b"' }] }),
    ];
    mockUseFullTrace.mockReturnValue(fullTrace({ events, loaded: true }));
    useGraphStore.setState({
      graph: GRAPH,
      selectedNodeId: "a.py:validate",
      traceSessionId: "s1",
      tracePlayhead: 2,
    });
    render(<CausalPathPanel />);

    expect(screen.getByText("d0")).toBeInTheDocument();
    expect(screen.getByText("d1")).toBeInTheDocument();
    expect(screen.getByText("d2")).toBeInTheDocument();
    expect(screen.getByText("main")).toBeInTheDocument();
    expect(screen.getByText("handle")).toBeInTheDocument();
    expect(screen.getByText("validate (THIS)")).toBeInTheDocument();
    expect(screen.getByText("(req=<Request>)")).toBeInTheDocument();
    expect(screen.getByText('(email="a@b")')).toBeInTheDocument();

    // handle was called from main at line 31; validate from handle at line 14.
    expect(screen.getByText("↳ call site a.py:31")).toBeInTheDocument();
    expect(screen.getByText("↳ call site a.py:14")).toBeInTheDocument();
    // The root hop (main) has no parent → exactly 2 call-site buttons, not 3.
    expect(screen.getAllByRole("button", { name: /↳ call site/ })).toHaveLength(
      2
    );
  });

  it("steps between firings when a node fired more than once", () => {
    const events: TraceEvent[] = [
      call("a.py:main", 0),
      call("a.py:helper", 1, { args: [{ name: "x", repr: "1" }] }, 2),
      ret("a.py:helper", 1, 3),
      call("a.py:helper", 1, { args: [{ name: "x", repr: "2" }] }, 4),
    ];
    mockUseFullTrace.mockReturnValue(fullTrace({ events, loaded: true }));
    useGraphStore.setState({
      graph: GRAPH,
      selectedNodeId: "a.py:helper",
      traceSessionId: "s1",
      tracePlayhead: 0, // before both firings → default is the earliest (index 0)
    });
    render(<CausalPathPanel />);

    expect(screen.getByText("1 / 2")).toBeInTheDocument();
    expect(screen.getByText("(x=1)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "◀ firing" })).toBeDisabled();

    fireEvent.click(screen.getByRole("button", { name: "firing ▶" }));
    expect(screen.getByText("2 / 2")).toBeInTheDocument();
    expect(screen.getByText("(x=2)")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "firing ▶" })).toBeDisabled();
  });

  it("time-travels the playhead without selecting the node or jumping source", () => {
    const setPlayhead = vi.fn();
    const selectNode = vi.fn();
    const jumpToSourceLine = vi.fn();
    const events: TraceEvent[] = [call("a.py:main", 0), call("a.py:handle", 1)];
    mockUseFullTrace.mockReturnValue(fullTrace({ events, loaded: true }));
    useGraphStore.setState({
      graph: GRAPH,
      selectedNodeId: "a.py:handle",
      traceSessionId: "s1",
      tracePlayhead: 1,
      setPlayhead,
      selectNode,
      jumpToSourceLine,
    });
    render(<CausalPathPanel />);

    fireEvent.click(
      screen.getAllByRole("button", { name: "→ time-travel" })[0] as HTMLElement
    );
    expect(setPlayhead).toHaveBeenCalledWith(0); // main's callIndex
    expect(selectNode).not.toHaveBeenCalled();
    expect(jumpToSourceLine).not.toHaveBeenCalled();
  });

  it("selects an in-graph hop and disables selection for one absent from the graph", () => {
    const selectNode = vi.fn();
    const setHighlightedNodes = vi.fn();
    const events: TraceEvent[] = [
      call("stdlib:builtin", 0), // not a node in GRAPH
      call("a.py:handle", 1),
    ];
    mockUseFullTrace.mockReturnValue(fullTrace({ events, loaded: true }));
    useGraphStore.setState({
      graph: GRAPH,
      selectedNodeId: "a.py:handle",
      traceSessionId: "s1",
      tracePlayhead: 1,
      selectNode,
      setHighlightedNodes,
    });
    render(<CausalPathPanel />);

    const selectButtons = screen.getAllByRole("button", { name: "select" });
    // Row 0 = stdlib:builtin (not in graph) → disabled.
    expect(selectButtons[0]).toBeDisabled();
    fireEvent.click(selectButtons[0] as HTMLElement);
    expect(selectNode).not.toHaveBeenCalled();

    // Row 1 = a.py:handle (in graph) → enabled.
    expect(selectButtons[1]).toBeEnabled();
    fireEvent.click(selectButtons[1] as HTMLElement);
    expect(setHighlightedNodes).toHaveBeenCalledWith(null);
    expect(selectNode).toHaveBeenCalledWith("a.py:handle");
  });

  it("jumps to the parent's call site via the per-hop call-site button", () => {
    const jumpToSourceLine = vi.fn();
    const events: TraceEvent[] = [
      call("a.py:main", 0),
      call("a.py:handle", 1),
      call("a.py:validate", 2),
    ];
    mockUseFullTrace.mockReturnValue(fullTrace({ events, loaded: true }));
    useGraphStore.setState({
      graph: GRAPH,
      selectedNodeId: "a.py:validate",
      traceSessionId: "s1",
      tracePlayhead: 2,
      jumpToSourceLine,
    });
    render(<CausalPathPanel />);

    fireEvent.click(screen.getByText("↳ call site a.py:14"));
    expect(jumpToSourceLine).toHaveBeenCalledWith("a.py", 14);
  });

  it("restores the real store actions between tests (no cross-test mock leakage)", () => {
    // The PRECEDING test overrode jumpToSourceLine with a vi.fn(); beforeEach's
    // full-replace must have restored the real action before this test ran, or
    // this would see the leftover mock instead.
    expect(useGraphStore.getState().jumpToSourceLine).toBe(
      INITIAL_STORE_STATE.jumpToSourceLine
    );
  });

  it("ignores a non-call edge (e.g. import) sharing the same source/target as the real call edge", () => {
    // The import edge appears FIRST and has a line — a naive "first
    // line-bearing match regardless of kind" scan would wrongly report it as
    // the call site instead of the actual `call` edge's line.
    const g: Graph = {
      version: 1,
      language: "python",
      nodes: [
        { id: "a.py:main", kind: "function", name: "main", path: "a.py" },
        {
          id: "a.py:handle",
          kind: "function",
          name: "handle",
          path: "a.py",
        },
      ],
      edges: [
        {
          source: "a.py:main",
          target: "a.py:handle",
          kind: "import",
          metadata: { line: 1 },
        },
        {
          source: "a.py:main",
          target: "a.py:handle",
          kind: "call",
          metadata: { line: 31 },
        },
      ],
    } as unknown as Graph;
    const events: TraceEvent[] = [call("a.py:main", 0), call("a.py:handle", 1)];
    mockUseFullTrace.mockReturnValue(fullTrace({ events, loaded: true }));
    useGraphStore.setState({
      graph: g,
      selectedNodeId: "a.py:handle",
      traceSessionId: "s1",
      tracePlayhead: 1,
    });
    render(<CausalPathPanel />);

    expect(screen.getByText("↳ call site a.py:31")).toBeInTheDocument();
    expect(screen.queryByText("↳ call site a.py:1")).not.toBeInTheDocument();
  });

  it("shows the capped caveat regardless of playhead position (no spurious playhead-dependent warning)", () => {
    const events: TraceEvent[] = [];
    for (let i = 0; i < MAX_FIRINGS + 5; i++) {
      events.push(call("a.py:hot", 0, undefined, i));
    }
    mockUseFullTrace.mockReturnValue(fullTrace({ events, loaded: true }));
    useGraphStore.setState({
      graph: GRAPH,
      selectedNodeId: "a.py:hot",
      traceSessionId: "s1",
      // At the last COLLECTED firing's callIndex — the old buggy warning fired
      // spuriously here; the single capped caveat now conveys the bound
      // playhead-independently.
      tracePlayhead: MAX_FIRINGS - 1,
    });
    render(<CausalPathPanel />);

    // Exactly ONE capped caveat, not a second playhead-relative warning.
    expect(
      screen.getAllByText(/later\s+firings are not collected/)
    ).toHaveLength(1);
    expect(
      screen.getByText(/one nearer the current playhead may exist beyond them/)
    ).toBeInTheDocument();
  });

  it("steps to the last collected firing while capped without adding a warning", () => {
    // Regression: defaultMayBeStale used to fire only for the LAST collected
    // firing at/past the playhead, so stepping onto it spuriously toggled a
    // warning even though the user chose it. The caveat is now stable.
    const events: TraceEvent[] = [];
    for (let i = 0; i < MAX_FIRINGS + 5; i++) {
      events.push(call("a.py:hot", 0, undefined, i));
    }
    mockUseFullTrace.mockReturnValue(fullTrace({ events, loaded: true }));
    useGraphStore.setState({
      graph: GRAPH,
      selectedNodeId: "a.py:hot",
      traceSessionId: "s1",
      tracePlayhead: 0,
    });
    render(<CausalPathPanel />);

    const before = screen.getAllByText(
      /later\s+firings are not collected/
    ).length;
    // Step all the way to the last firing.
    for (let i = 0; i < MAX_FIRINGS - 1; i++) {
      fireEvent.click(screen.getByRole("button", { name: "firing ▶" }));
    }
    expect(
      screen.getByText(`${MAX_FIRINGS} / ${MAX_FIRINGS}`)
    ).toBeInTheDocument();
    // No extra warning appeared from stepping onto the last firing.
    expect(
      screen.getAllByText(/later\s+firings are not collected/)
    ).toHaveLength(before);
  });

  it("shows a completeness banner (not a hard gate) when the prefix is truncated", () => {
    const events: TraceEvent[] = [call("a.py:main", 0), call("a.py:handle", 1)];
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events, loaded: true, truncated: true })
    );
    useGraphStore.setState({
      graph: GRAPH,
      selectedNodeId: "a.py:handle",
      traceSessionId: "s1",
      traceSeekable: true,
      tracePlayhead: 1,
    });
    render(<CausalPathPanel />);

    expect(
      screen.getByText(
        /1 firing within the first 2 events — later firings not shown\./
      )
    ).toBeInTheDocument();
    // Unlike ValueInspectorPanel's hard gate, the path is still fully shown.
    expect(
      screen.getAllByRole("button", { name: "select" }).length
    ).toBeGreaterThan(0);
    expect(screen.queryByText(/unavailable/)).not.toBeInTheDocument();
  });

  it("distinguishes 'did not fire' from 'not within the loaded prefix' when truncated", () => {
    // A node with zero firings in a TRUNCATED seekable prefix might simply
    // fire past the 50k cliff — "did not fire" would be misleading.
    const events: TraceEvent[] = [call("a.py:main", 0)];
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events, loaded: true, truncated: true })
    );
    useGraphStore.setState({
      graph: GRAPH,
      selectedNodeId: "a.py:unused",
      traceSessionId: "s1",
      traceSeekable: true,
    });
    render(<CausalPathPanel />);

    expect(
      screen.getByText(/did not fire within the first 1 events/)
    ).toBeInTheDocument();
    expect(
      screen.queryByText("This node did not fire in the loaded trace.")
    ).not.toBeInTheDocument();
  });

  it("re-defaults the firing stepper to nearest-to-playhead on a new session with the same node selected", () => {
    const sessionAEvents: TraceEvent[] = [
      call("a.py:helper", 0, undefined, 10),
      call("a.py:helper", 0, undefined, 20),
    ];
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: sessionAEvents, loaded: true })
    );
    useGraphStore.setState({
      graph: GRAPH,
      selectedNodeId: "a.py:helper",
      traceSessionId: "sA",
      tracePlayhead: 0, // nearest is firing 0 → "1 / 2"
    });
    const { rerender } = render(<CausalPathPanel />);
    expect(screen.getByText("1 / 2")).toBeInTheDocument();

    // Step to firing 2/2 so the sticky index is non-default.
    fireEvent.click(screen.getByRole("button", { name: "firing ▶" }));
    expect(screen.getByText("2 / 2")).toBeInTheDocument();

    // A brand-new session (same node selected) with the playhead near the
    // second firing must RE-DEFAULT via nearestFiring, not carry index 1
    // blindly. Here both firings are <= playhead so nearest is the 2nd — but
    // the key point is the re-derivation fires; make session B's playhead land
    // on firing 1 (index 0) to prove it re-defaults rather than sticking.
    const sessionBEvents: TraceEvent[] = [
      call("a.py:helper", 0, undefined, 100),
      call("a.py:helper", 0, undefined, 200),
    ];
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: sessionBEvents, loaded: true })
    );
    useGraphStore.setState({ traceSessionId: "sB", tracePlayhead: 0 });
    rerender(<CausalPathPanel />);
    expect(screen.getByText("1 / 2")).toBeInTheDocument();
  });

  it("shows a loading state while paging a seekable session", () => {
    mockUseFullTrace.mockReturnValue(fullTrace({ loading: true }));
    useGraphStore.setState({
      graph: GRAPH,
      selectedNodeId: "a.py:main",
      traceSessionId: "s1",
      traceSeekable: true,
    });
    render(<CausalPathPanel />);
    expect(screen.getByText(/Reconstructing call stack/)).toBeInTheDocument();
  });

  it("shows an error with retry when paging fails", () => {
    const load = vi.fn();
    mockUseFullTrace.mockReturnValue(fullTrace({ error: true, load }));
    useGraphStore.setState({
      graph: GRAPH,
      selectedNodeId: "a.py:main",
      traceSessionId: "s1",
      traceSeekable: true,
    });
    render(<CausalPathPanel />);
    expect(screen.getByRole("alert")).toHaveTextContent(/Failed to load/);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(load).toHaveBeenCalled();
  });

  it("offers a Load call stack trigger for an unloaded seekable session", () => {
    const load = vi.fn();
    mockUseFullTrace.mockReturnValue(fullTrace({ loaded: false, load }));
    useGraphStore.setState({
      graph: GRAPH,
      selectedNodeId: "a.py:main",
      traceSessionId: "s1",
      traceSeekable: true,
    });
    render(<CausalPathPanel />);
    fireEvent.click(screen.getByRole("button", { name: /Load call stack/ }));
    expect(load).toHaveBeenCalled();
  });

  it("hints to enable capture when no values are present", () => {
    const events: TraceEvent[] = [call("a.py:main", 0), call("a.py:handle", 1)];
    mockUseFullTrace.mockReturnValue(fullTrace({ events, loaded: true }));
    useGraphStore.setState({
      graph: GRAPH,
      selectedNodeId: "a.py:handle",
      traceSessionId: "s1",
      tracePlayhead: 1,
    });
    render(<CausalPathPanel />);
    expect(screen.getByText(/--capture-values/)).toBeInTheDocument();
  });

  it("keeps recursive invocations distinct without crashing", () => {
    const events: TraceEvent[] = [
      call("a.py:f", 0),
      call("a.py:f", 1),
      call("a.py:f", 2),
    ];
    mockUseFullTrace.mockReturnValue(fullTrace({ events, loaded: true }));
    useGraphStore.setState({
      graph: GRAPH,
      selectedNodeId: "a.py:f",
      traceSessionId: "s1",
      tracePlayhead: 2, // nearest-to-playhead default → the deepest firing
    });
    render(<CausalPathPanel />);

    expect(screen.getByText("d0")).toBeInTheDocument();
    expect(screen.getByText("d1")).toBeInTheDocument();
    expect(screen.getByText("d2")).toBeInTheDocument();
    expect(screen.getAllByRole("listitem")).toHaveLength(3);
  });
});
