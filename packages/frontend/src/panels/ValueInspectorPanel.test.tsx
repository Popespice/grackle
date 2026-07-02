import type { Graph, TraceEvent } from "@grackle/shared-types";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { type UseFullTraceResult, useFullTrace } from "../graph/useFullTrace";
import { useGraphStore } from "../graph/useGraphStore";
import { ValueInspectorPanel } from "./ValueInspectorPanel";

vi.mock("../graph/useFullTrace");
const mockUseFullTrace = vi.mocked(useFullTrace);

/** A `useFullTrace` return, overridable per test. */
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

const graph = (ids: string[]): Graph =>
  ({ nodes: ids.map((id) => ({ id })), edges: [] }) as unknown as Graph;

const call = (
  id: string,
  depth: number,
  args?: TraceEvent["values"]
): TraceEvent =>
  args
    ? {
        event: "call",
        node_id: id,
        ts_ns: depth + 1,
        thread_id: 1,
        frame_depth: depth,
        values: args,
      }
    : {
        event: "call",
        node_id: id,
        ts_ns: depth + 1,
        thread_id: 1,
        frame_depth: depth,
      };

afterEach(cleanup);

beforeEach(() => {
  mockUseFullTrace.mockReturnValue(fullTrace());
  useGraphStore.setState({
    graph: null,
    selectedNodeId: null,
    highlightedNodeIds: null,
    traceEvents: [],
    traceSessionId: null,
    tracePlayhead: 0,
    traceSeekable: false,
    traceTotal: 0,
    traceWindowStart: 0,
  });
});

describe("ValueInspectorPanel", () => {
  it("renders nothing without a trace session", () => {
    const { container } = render(<ValueInspectorPanel />);
    expect(container.firstChild).toBeNull();
  });

  it("shows the arguments on a call event, with a redaction badge", () => {
    const events: TraceEvent[] = [
      call("a.py:main", 0),
      call("a.py:handle", 1, {
        args: [
          { name: "x", repr: "42" },
          { name: "token", repr: "<redacted>", redacted: true },
        ],
      }),
    ];
    mockUseFullTrace.mockReturnValue(fullTrace({ events, loaded: true }));
    useGraphStore.setState({
      traceSessionId: "s1",
      traceEvents: events,
      tracePlayhead: 1,
      graph: graph(["a.py:main", "a.py:handle"]),
    });

    render(<ValueInspectorPanel />);
    expect(screen.getByText("x")).toBeInTheDocument();
    expect(screen.getByText("42")).toBeInTheDocument();
    expect(screen.getByText("token")).toBeInTheDocument();
    expect(screen.getAllByText("redacted").length).toBeGreaterThan(0);
    // Values present → no capture-off hint.
    expect(screen.queryByText(/--capture-values/)).toBeNull();
  });

  it("shows the return value and a truncation badge on a return event", () => {
    const events: TraceEvent[] = [
      call("a.py:main", 0),
      {
        event: "return",
        node_id: "a.py:main",
        ts_ns: 2,
        thread_id: 1,
        frame_depth: 0,
        values: { ret: "'ok'", ret_truncated: true },
      },
    ];
    mockUseFullTrace.mockReturnValue(fullTrace({ events, loaded: true }));
    useGraphStore.setState({
      traceSessionId: "s1",
      traceEvents: events,
      tracePlayhead: 1,
      graph: graph(["a.py:main"]),
    });

    render(<ValueInspectorPanel />);
    expect(screen.getByText("'ok'")).toBeInTheDocument();
    expect(screen.getByText("trunc")).toBeInTheDocument();
  });

  it("selects the node when an in-graph stack frame is clicked", () => {
    const selectNode = vi.fn();
    const setHighlightedNodes = vi.fn();
    const events = [call("a.py:main", 0), call("a.py:handle", 1)];
    mockUseFullTrace.mockReturnValue(fullTrace({ events, loaded: true }));
    useGraphStore.setState({
      traceSessionId: "s1",
      traceEvents: events,
      tracePlayhead: 1,
      graph: graph(["a.py:main", "a.py:handle"]),
      selectNode,
      setHighlightedNodes,
    });

    render(<ValueInspectorPanel />);
    fireEvent.click(
      screen.getByRole("button", { name: "Select handle frame" })
    );
    expect(setHighlightedNodes).toHaveBeenCalledWith(null);
    expect(selectNode).toHaveBeenCalledWith("a.py:handle");
  });

  it("disables selection for a frame absent from the static graph", () => {
    const selectNode = vi.fn();
    const events = [call("a.py:main", 0), call("a.py:handle", 1)];
    mockUseFullTrace.mockReturnValue(fullTrace({ events, loaded: true }));
    useGraphStore.setState({
      traceSessionId: "s1",
      traceEvents: events,
      tracePlayhead: 1,
      graph: graph(["a.py:main"]), // handle is NOT in the graph
      selectNode,
    });

    render(<ValueInspectorPanel />);
    const btn = screen.getByRole("button", { name: "Select handle frame" });
    expect(btn).toBeDisabled();
    fireEvent.click(btn);
    expect(selectNode).not.toHaveBeenCalled();
  });

  it("jumps the playhead to a frame's call", () => {
    const setPlayhead = vi.fn();
    const events = [call("a.py:main", 0), call("a.py:handle", 1)];
    mockUseFullTrace.mockReturnValue(fullTrace({ events, loaded: true }));
    useGraphStore.setState({
      traceSessionId: "s1",
      traceEvents: events,
      tracePlayhead: 1,
      graph: graph(["a.py:main", "a.py:handle"]),
      setPlayhead,
    });

    render(<ValueInspectorPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Jump to main call" }));
    expect(setPlayhead).toHaveBeenCalledWith(0); // main opened at index 0
  });

  it("steps to the next call/return boundary", () => {
    const setPlayhead = vi.fn();
    const events: TraceEvent[] = [
      call("a.py:f", 0),
      {
        event: "line",
        node_id: "a.py:f",
        ts_ns: 2,
        thread_id: 1,
        frame_depth: 0,
      },
      call("a.py:g", 1),
    ];
    mockUseFullTrace.mockReturnValue(fullTrace({ events, loaded: true }));
    useGraphStore.setState({
      traceSessionId: "s1",
      traceEvents: events,
      tracePlayhead: 0,
      graph: graph(["a.py:f", "a.py:g"]),
      setPlayhead,
    });

    render(<ValueInspectorPanel />);
    fireEvent.click(screen.getByRole("button", { name: /next/ }));
    expect(setPlayhead).toHaveBeenCalledWith(2); // skips the line at index 1
  });

  it("offers a Load call stack button for an unloaded seekable session", () => {
    const load = vi.fn();
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: [], loaded: false, load })
    );
    useGraphStore.setState({
      traceSessionId: "s1",
      traceSeekable: true,
      traceTotal: 100,
    });

    render(<ValueInspectorPanel />);
    fireEvent.click(screen.getByRole("button", { name: /Load call stack/ }));
    expect(load).toHaveBeenCalled();
  });

  it("shows an unavailable state past the paged 50k prefix", () => {
    const events = [call("a.py:main", 0), call("a.py:handle", 1)];
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events, loaded: true, truncated: true })
    );
    useGraphStore.setState({
      traceSessionId: "s1",
      traceSeekable: true,
      traceTotal: 100000,
      tracePlayhead: 50001, // beyond events.length
    });

    render(<ValueInspectorPanel />);
    expect(
      screen.getByText(/Call stack unavailable beyond/)
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Select/ })).toBeNull();
  });

  it("marks the stack unavailable at exactly the truncated prefix boundary", () => {
    // playhead === events.length with a TRUNCATED prefix: the next event was
    // never paged, so the stack is unknown (guards the `>=`-not-`>` gate).
    const events = [call("a.py:main", 0), call("a.py:handle", 1)];
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events, loaded: true, truncated: true })
    );
    useGraphStore.setState({
      traceSessionId: "s1",
      traceSeekable: true,
      traceTotal: 100000,
      tracePlayhead: 2, // === events.length
    });

    render(<ValueInspectorPanel />);
    expect(
      screen.getByText(/Call stack unavailable beyond/)
    ).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: /Select/ })).toBeNull();
  });

  it("shows the end-of-trace stack at the boundary when the prefix is complete", () => {
    // playhead === events.length but NOT truncated: this is the legitimate
    // "after the final event" position and its stack is reconstructable.
    const events = [call("a.py:main", 0), call("a.py:handle", 1)];
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events, loaded: true, truncated: false })
    );
    useGraphStore.setState({
      traceSessionId: "s1",
      traceSeekable: true,
      traceTotal: 2,
      tracePlayhead: 2, // === events.length, whole trace paged
    });

    render(<ValueInspectorPanel />);
    expect(screen.queryByText(/Call stack unavailable beyond/)).toBeNull();
    expect(
      screen.getByRole("button", { name: "Select handle frame" })
    ).toBeInTheDocument();
  });

  it("shows a loading state while paging a seekable session", () => {
    mockUseFullTrace.mockReturnValue(fullTrace({ loading: true }));
    useGraphStore.setState({
      traceSessionId: "s1",
      traceSeekable: true,
      traceTotal: 100,
    });

    render(<ValueInspectorPanel />);
    expect(screen.getByText(/Reconstructing call stack/)).toBeInTheDocument();
  });

  it("shows an error with retry when paging fails", () => {
    const load = vi.fn();
    mockUseFullTrace.mockReturnValue(fullTrace({ error: true, load }));
    useGraphStore.setState({
      traceSessionId: "s1",
      traceSeekable: true,
      traceTotal: 100,
    });

    render(<ValueInspectorPanel />);
    expect(screen.getByRole("alert")).toHaveTextContent(/Failed to load/);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(load).toHaveBeenCalled();
  });

  it("hints to enable capture when no values are present", () => {
    const events = [call("a.py:main", 0), call("a.py:handle", 1)];
    mockUseFullTrace.mockReturnValue(fullTrace({ events, loaded: true }));
    useGraphStore.setState({
      traceSessionId: "s1",
      traceEvents: events,
      tracePlayhead: 0,
      graph: graph(["a.py:main", "a.py:handle"]),
    });

    render(<ValueInspectorPanel />);
    expect(screen.getByText(/--capture-values/)).toBeInTheDocument();
  });
});
