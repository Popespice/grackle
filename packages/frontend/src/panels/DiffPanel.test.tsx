import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { RuntimeCoverage } from "../graph/runtimeCoverage";
import { DiffPanel } from "./DiffPanel";

// ---------------------------------------------------------------------------
// Module mocks
// ---------------------------------------------------------------------------

// Stub useRuntimeCoverage — real implementation depends on Zustand store.
// Default return is set in beforeEach so each test starts from a known state.
vi.mock("../graph/useRuntimeCoverage", () => ({
  useRuntimeCoverage: vi.fn(),
}));

// ---------------------------------------------------------------------------
// Store helpers
// ---------------------------------------------------------------------------

import { useGraphStore } from "../graph/useGraphStore";
import { useRuntimeCoverage } from "../graph/useRuntimeCoverage";

const mockedCoverage = vi.mocked(useRuntimeCoverage);

function coverage(touched: string[], cold: string[]): RuntimeCoverage {
  return {
    touched: new Set(touched),
    cold: new Set(cold),
    hot: new Set<string>(),
    touchedCount: touched.length,
    coldCount: cold.length,
    hotCount: 0,
  };
}

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

beforeEach(() => {
  mockedCoverage.mockReturnValue(null);
});

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

  it("shows 'Show overlay' toggle (overlay off by default)", () => {
    useGraphStore.setState({ traceSessionId: "s1", graph: SIMPLE_GRAPH });
    render(<DiffPanel />);
    // Default off → button reads "Show overlay", and the store overlay is null
    // so the runtime heat-map is NOT suppressed.
    expect(screen.getByRole("button", { name: /show overlay/i })).toBeTruthy();
    expect(useGraphStore.getState().diffOverlay).toBeNull();
  });

  it("does not write a graph overlay until the user enables it", async () => {
    // Coverage present → trace-vs-static entries exist, but overlay must stay
    // off until the user opts in (otherwise the heat-map is hijacked).
    mockedCoverage.mockReturnValue(coverage(["a"], ["b"]));
    useGraphStore.setState({ traceSessionId: "s1", graph: SIMPLE_GRAPH });
    render(<DiffPanel />);
    // Mounting DiffPanel must not hijack the heat-map: overlay stays null.
    expect(useGraphStore.getState().diffOverlay).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /show overlay/i }));
    // After enabling, the debounced effect writes the overlay (150 ms).
    await waitFor(
      () => expect(useGraphStore.getState().diffOverlay).not.toBeNull(),
      { timeout: 1000 }
    );
  });

  it("'Set as baseline' auto-enables the overlay", async () => {
    useGraphStore.setState({
      traceSessionId: "s1",
      graph: SIMPLE_GRAPH,
      agentHeat: { a: 3 },
    });
    render(<DiffPanel />);
    fireEvent.click(screen.getByRole("button", { name: /set as baseline/i }));
    // Toggle flips to "Hide overlay" and the overlay is written (debounced).
    expect(screen.getByRole("button", { name: /hide overlay/i })).toBeTruthy();
    await waitFor(
      () => expect(useGraphStore.getState().diffOverlay).not.toBeNull(),
      { timeout: 1000 }
    );
  });
});
