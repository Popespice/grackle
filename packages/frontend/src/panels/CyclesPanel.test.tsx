import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useGraphStore } from "../graph/useGraphStore";
import { restoreInitialState } from "../test/storeReset";
import { CyclesPanel } from "./CyclesPanel";

afterEach(cleanup);

const MOCK_GRAPH = {
  version: 1 as const,
  language: "typescript",
  nodes: [
    { id: "a", kind: "function", name: "alpha", path: "src/a.ts" },
    { id: "b", kind: "function", name: "beta", path: "src/b.ts" },
    { id: "c", kind: "function", name: "gamma", path: "src/c.ts" },
    { id: "d", kind: "function", name: "delta", path: "src/d.ts" },
  ],
  edges: [
    { source: "a", target: "b", kind: "call" },
    { source: "b", target: "c", kind: "call" },
    { source: "c", target: "a", kind: "call" },
    // d has no cycle
  ],
};

/**
 * MOCK_GRAPH's 3-cycle plus a 2-cycle whose nodes are declared FIRST, so
 * Tarjan emits the smaller cycle first — only the size sort can put the
 * 3-cycle on top.
 */
const TWO_CYCLE_GRAPH = {
  ...MOCK_GRAPH,
  nodes: [
    { id: "e", kind: "function", name: "epsilon", path: "src/e.ts" },
    { id: "z", kind: "function", name: "zeta", path: "src/z.ts" },
    ...MOCK_GRAPH.nodes,
  ],
  edges: [
    { source: "e", target: "z", kind: "call" },
    { source: "z", target: "e", kind: "call" },
    ...MOCK_GRAPH.edges,
  ],
};

/**
 * Each cycle row as rendered, top to bottom: its size badge and the member
 * names in its preview. Members are sorted — the order WITHIN a cycle is
 * Tarjan's pop order, an implementation detail no assertion should pin.
 */
function cycleRows(): { size: string; members: string[] }[] {
  return screen.getAllByRole("button").map((button) => {
    const [size = "", preview = ""] = Array.from(
      button.children,
      (child) => child.textContent ?? ""
    );
    return { size, members: preview.split(" → ").sort() };
  });
}

beforeEach(() => {
  // Full-replace first (campaign T11-4): the click tests below stub the
  // setHighlightedNodes ACTION via a partial merge, and a partial-merge reset
  // would never restore it — every later test would run against the stub.
  restoreInitialState(useGraphStore);
  useGraphStore.setState({
    graph: MOCK_GRAPH,
    selectedNodeId: null,
    highlightedNodeIds: null,
    hiddenKinds: new Set<string>(),
    searchTerm: "",
    excludeGlobs: [],
  });
});

describe("CyclesPanel", () => {
  it("renders null when graph is null", () => {
    useGraphStore.setState({ graph: null });
    const { container } = render(<CyclesPanel />);
    expect(container.firstChild).toBeNull();
  });

  it("renders null when there are no cycles", () => {
    useGraphStore.setState({
      graph: {
        ...MOCK_GRAPH,
        edges: [
          { source: "a", target: "b", kind: "call" },
          { source: "b", target: "c", kind: "call" },
        ],
      },
    });
    const { container } = render(<CyclesPanel />);
    expect(container.firstChild).toBeNull();
  });

  it("renders the cycle count header", () => {
    render(<CyclesPanel />);
    expect(screen.getByLabelText("Cycles")).toBeInTheDocument();
    expect(screen.getByText(/Cycles \(1\)/)).toBeInTheDocument();
  });

  it("renders node names for cycle members", () => {
    render(<CyclesPanel />);
    // alpha, beta, gamma are in the cycle — all three, and nothing else
    // (delta has no cycle).
    expect(cycleRows()).toEqual([
      { size: "3", members: ["alpha", "beta", "gamma"] },
    ]);
  });

  it("lists cycles largest first", () => {
    useGraphStore.setState({ graph: TWO_CYCLE_GRAPH });
    render(<CyclesPanel />);
    expect(screen.getByText(/Cycles \(2\)/)).toBeInTheDocument();
    expect(cycleRows()).toEqual([
      { size: "3", members: ["alpha", "beta", "gamma"] },
      { size: "2", members: ["epsilon", "zeta"] },
    ]);
  });

  it("calls setHighlightedNodes with cycle nodes on click", () => {
    const setHighlightedNodes = vi.fn();
    useGraphStore.setState({ setHighlightedNodes });
    render(<CyclesPanel />);
    const buttons = screen.getAllByRole("button");
    if (buttons.length === 0) throw new Error("No buttons rendered");
    fireEvent.click(buttons[0] as HTMLElement);
    expect(setHighlightedNodes).toHaveBeenCalledWith(
      expect.arrayContaining(["a", "b", "c"])
    );
  });

  it("toggles off highlight when clicking an already-active cycle", () => {
    const cycleNodes = ["a", "b", "c"];
    useGraphStore.setState({ highlightedNodeIds: new Set(cycleNodes) });
    const setHighlightedNodes = vi.fn();
    useGraphStore.setState({ setHighlightedNodes });
    render(<CyclesPanel />);
    const buttons = screen.getAllByRole("button");
    if (buttons.length === 0) throw new Error("No buttons rendered");
    fireEvent.click(buttons[0] as HTMLElement);
    expect(setHighlightedNodes).toHaveBeenCalledWith(null);
  });

  it("restores the real store actions between tests (no cross-test mock leakage)", () => {
    // The two PRECEDING tests stubbed setHighlightedNodes with a vi.fn();
    // beforeEach's full replace must have restored the real action, or this
    // click would hit the leftover stub and the store would never change.
    expect(useGraphStore.getState().setHighlightedNodes).toBe(
      useGraphStore.getInitialState().setHighlightedNodes
    );
    render(<CyclesPanel />);
    fireEvent.click(screen.getByRole("button"));
    expect(useGraphStore.getState().highlightedNodeIds).toEqual(
      new Set(["a", "b", "c"])
    );
  });
});
