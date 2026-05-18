import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useGraphStore } from "../graph/useGraphStore";
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

beforeEach(() => {
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
    const panel = screen.getByLabelText("Cycles");
    // alpha, beta, gamma are in the cycle
    expect(panel.textContent).toMatch(/alpha|beta|gamma/);
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
});
