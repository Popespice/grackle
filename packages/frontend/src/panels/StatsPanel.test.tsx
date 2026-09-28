import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useGraphStore } from "../graph/useGraphStore";
import { restoreInitialState } from "../test/storeReset";
import { StatsPanel } from "./StatsPanel";

afterEach(cleanup);

const MOCK_GRAPH = {
  version: 1,
  language: "python",
  nodes: [
    { id: "a.py", kind: "file", name: "a.py", path: "a.py" },
    { id: "b.py", kind: "file", name: "b.py", path: "b.py" },
    { id: "a.py:Foo", kind: "class", name: "Foo", path: "a.py" },
    { id: "a.py:bar", kind: "function", name: "bar", path: "a.py" },
    { id: "a.py:baz", kind: "function", name: "baz", path: "a.py" },
  ],
  edges: [
    { source: "a.py", target: "b.py", kind: "import" },
    { source: "a.py:bar", target: "a.py:baz", kind: "call" },
    { source: "a.py:bar", target: "a.py:Foo", kind: "call" },
  ],
};

/**
 * Distinct, non-tied ranks — every node MOCK_GRAPH shows under Top or Hub
 * ties at 1, so no ordering assertion over it can tell a correct ranking from
 * a reversed or missing one (campaign T4-5/T11-3). Nodes are declared out of
 * rank order so an unsorted list fails too.
 *
 *   in-degree: alpha 4, beta 3, gamma 2, s1..s4 1   → Top: alpha, beta, gamma
 *   hub (in - out): beta +3, gamma +2, alpha 0 (4 in, 4 out), s4 0,
 *                   s3 -1, s1/s2 -2                 → Hub: beta, gamma only
 */
const RANKED_GRAPH = {
  version: 1,
  language: "python",
  nodes: [
    { id: "s1", kind: "function", name: "s1", path: "r.py" },
    { id: "gamma", kind: "function", name: "gamma", path: "r.py" },
    { id: "s2", kind: "function", name: "s2", path: "r.py" },
    { id: "beta", kind: "function", name: "beta", path: "r.py" },
    { id: "s3", kind: "function", name: "s3", path: "r.py" },
    { id: "alpha", kind: "function", name: "alpha", path: "r.py" },
    { id: "s4", kind: "function", name: "s4", path: "r.py" },
  ],
  edges: [
    { source: "s1", target: "alpha", kind: "call" },
    { source: "s1", target: "beta", kind: "call" },
    { source: "s1", target: "gamma", kind: "call" },
    { source: "s2", target: "alpha", kind: "call" },
    { source: "s2", target: "beta", kind: "call" },
    { source: "s2", target: "gamma", kind: "call" },
    { source: "s3", target: "alpha", kind: "call" },
    { source: "s3", target: "beta", kind: "call" },
    { source: "s4", target: "alpha", kind: "call" },
    { source: "alpha", target: "s1", kind: "call" },
    { source: "alpha", target: "s2", kind: "call" },
    { source: "alpha", target: "s3", kind: "call" },
    { source: "alpha", target: "s4", kind: "call" },
  ],
};

/**
 * The rendered entries of one labelled section ("Top:" / "Hub:"), in order:
 * the label's following siblings up to the next (text-less) separator.
 * Scoping to the section matters — the whole panel's text would let a name
 * shown under the OTHER section satisfy the assertion.
 */
function sectionEntries(label: string): string[] {
  const entries: string[] = [];
  let el = screen.getByText(label).nextElementSibling;
  while (el && el.textContent !== "") {
    entries.push(el.textContent ?? "");
    el = el.nextElementSibling;
  }
  return entries;
}

beforeEach(() => {
  // Full-replace first (campaign T11-4), then the fields these tests assume.
  restoreInitialState(useGraphStore);
  useGraphStore.setState({
    graph: MOCK_GRAPH,
    selectedNodeId: null,
    hiddenKinds: new Set<string>(),
    searchTerm: "",
    excludeGlobs: [],
  });
});

describe("StatsPanel", () => {
  it("renders nothing when graph is null", () => {
    useGraphStore.setState({ graph: null });
    const { container } = render(<StatsPanel />);
    expect(container.firstChild).toBeNull();
  });

  it("shows kind counts for each node kind", () => {
    render(<StatsPanel />);
    const panel = screen.getByLabelText("Graph statistics");
    expect(panel).toBeInTheDocument();
    expect(panel.textContent).toContain("file");
    expect(panel.textContent).toContain("class");
    expect(panel.textContent).toContain("function");
  });

  it("shows the orphan count", () => {
    render(<StatsPanel />);
    const panel = screen.getByLabelText("Graph statistics");
    expect(panel.textContent).toContain("Orphan");
  });

  it("ranks Top by in-degree, highest first, capped at three", () => {
    // MOCK_GRAPH: b.py, Foo and baz tie at in-degree 1 — exact membership,
    // no order (a tie has no right order to assert).
    const { unmount } = render(<StatsPanel />);
    expect(sectionEntries("Top:").sort()).toEqual(["Foo×1", "b.py×1", "baz×1"]);
    unmount();

    useGraphStore.setState({ graph: RANKED_GRAPH });
    render(<StatsPanel />);
    // s1..s4 (in-degree 1) rank below the cap.
    expect(sectionEntries("Top:")).toEqual(["alpha×4", "beta×3", "gamma×2"]);
  });

  it("shows Hub label", () => {
    render(<StatsPanel />);
    const panel = screen.getByLabelText("Graph statistics");
    expect(panel.textContent).toContain("Hub");
  });

  it("ranks Hub by in-minus-out score, highest first, positive scores only", () => {
    // MOCK_GRAPH: b.py, Foo and baz tie at +1 (in 1, out 0); a.py and bar
    // are negative — exact membership, no order.
    const { unmount } = render(<StatsPanel />);
    expect(sectionEntries("Hub:").sort()).toEqual(["Foo+1", "b.py+1", "baz+1"]);
    unmount();

    // alpha tops the in-degree ranking but also calls out 4 times (score 0),
    // so it must NOT appear here — nor must s4 at 0 or the negative s1..s3.
    useGraphStore.setState({ graph: RANKED_GRAPH });
    render(<StatsPanel />);
    expect(sectionEntries("Hub:")).toEqual(["beta+3", "gamma+2"]);
  });

  it("shows Cycles label with count", () => {
    render(<StatsPanel />);
    const panel = screen.getByLabelText("Graph statistics");
    expect(panel.textContent).toContain("Cycles:");
  });

  it("shows non-zero cycle count when cycles exist", () => {
    useGraphStore.setState({
      graph: {
        ...MOCK_GRAPH,
        edges: [
          ...MOCK_GRAPH.edges,
          { source: "a.py:baz", target: "a.py:bar", kind: "call" }, // creates a cycle
        ],
      },
    });
    render(<StatsPanel />);
    const panel = screen.getByLabelText("Graph statistics");
    // cycle count should be > 0
    expect(panel.textContent).toMatch(/Cycles:\s*[1-9]/);
  });
});
