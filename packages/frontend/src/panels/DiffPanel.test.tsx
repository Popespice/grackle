import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { DiffPanel } from "./DiffPanel";

// ---------------------------------------------------------------------------
// Module mocks
// ---------------------------------------------------------------------------

// Stub useRuntimeCoverage — real implementation depends on Zustand store
vi.mock("../graph/useRuntimeCoverage", () => ({
  useRuntimeCoverage: vi.fn(() => null),
}));

// ---------------------------------------------------------------------------
// Store helpers
// ---------------------------------------------------------------------------

import { useGraphStore } from "../graph/useGraphStore";

function resetStore() {
  useGraphStore.setState({
    graph: null,
    traceSessionId: null,
    traceEvents: [],
    agentHeat: null,
    diffBaseline: null,
    diffOverlay: null,
  });
}

const SIMPLE_GRAPH = {
  version: 1 as const,
  language: "python",
  nodes: [
    { id: "a", kind: "function", name: "a", path: "a.py" },
    { id: "b", kind: "function", name: "b", path: "b.py" },
  ],
  edges: [] as never[],
};

afterEach(() => {
  cleanup();
  resetStore();
  vi.clearAllMocks();
});

// ---------------------------------------------------------------------------
// Tests
// ---------------------------------------------------------------------------

describe("DiffPanel", () => {
  it("shows placeholder when no trace session", () => {
    render(<DiffPanel />);
    expect(screen.getByText(/Load a trace session/i)).toBeTruthy();
  });

  it("shows placeholder when graph is null even with session", () => {
    useGraphStore.setState({ traceSessionId: "s1", graph: null });
    render(<DiffPanel />);
    expect(screen.getByText(/Load a trace session/i)).toBeTruthy();
  });

  it("shows diff panel when session and graph are set", () => {
    useGraphStore.setState({ traceSessionId: "s1", graph: SIMPLE_GRAPH });
    render(<DiffPanel />);
    // Should show mode label
    expect(screen.getByText(/trace-vs-static/i)).toBeTruthy();
  });

  it("shows 'Set as baseline' button by default", () => {
    useGraphStore.setState({ traceSessionId: "s1", graph: SIMPLE_GRAPH });
    render(<DiffPanel />);
    expect(
      screen.getByRole("button", { name: /set as baseline/i })
    ).toBeTruthy();
  });

  it("switches to 'Clear baseline' when baseline is set", () => {
    useGraphStore.setState({
      traceSessionId: "s1",
      graph: SIMPLE_GRAPH,
      diffBaseline: { a: 5 },
    });
    render(<DiffPanel />);
    expect(
      screen.getByRole("button", { name: /clear baseline/i })
    ).toBeTruthy();
  });

  it("shows trace-vs-trace mode label when baseline is set", () => {
    useGraphStore.setState({
      traceSessionId: "s1",
      graph: SIMPLE_GRAPH,
      diffBaseline: { a: 5 },
      agentHeat: { a: 10 },
    });
    render(<DiffPanel />);
    expect(screen.getByText(/trace-vs-trace/i)).toBeTruthy();
  });

  it("shows regression banner when hotter entries exist", () => {
    useGraphStore.setState({
      traceSessionId: "s1",
      graph: SIMPLE_GRAPH,
      // baseline: a=1, current: a=5 -> hotter
      diffBaseline: { a: 1 },
      agentHeat: { a: 5 },
    });
    render(<DiffPanel />);
    expect(screen.getByText(/regression detected/i)).toBeTruthy();
  });

  it("does not show regression banner when no regressions", () => {
    useGraphStore.setState({
      traceSessionId: "s1",
      graph: SIMPLE_GRAPH,
      diffBaseline: { a: 5 },
      agentHeat: { a: 3 }, // colder, not hotter
    });
    render(<DiffPanel />);
    expect(screen.queryByText(/regression detected/i)).toBeNull();
  });

  it("shows 'Clear overlay' button", () => {
    useGraphStore.setState({ traceSessionId: "s1", graph: SIMPLE_GRAPH });
    render(<DiffPanel />);
    expect(screen.getByRole("button", { name: /clear overlay/i })).toBeTruthy();
  });
});
