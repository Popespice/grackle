import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useGraphStore } from "../graph/useGraphStore";
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

beforeEach(() => {
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

  it("shows top-degree node name", () => {
    render(<StatsPanel />);
    const panel = screen.getByLabelText("Graph statistics");
    expect(panel.textContent).toMatch(/Foo|baz/);
  });

  it("shows Hub label", () => {
    render(<StatsPanel />);
    const panel = screen.getByLabelText("Graph statistics");
    expect(panel.textContent).toContain("Hub");
  });

  it("shows hub node name when score > 0", () => {
    // Foo and baz have inDegree=1, outDegree=0 → score=+1
    render(<StatsPanel />);
    const panel = screen.getByLabelText("Graph statistics");
    expect(panel.textContent).toMatch(/Foo|baz/);
  });
});
