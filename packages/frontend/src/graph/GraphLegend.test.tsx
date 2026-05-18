import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";
import { GraphLegend } from "./GraphLegend";

afterEach(cleanup);

const MOCK_GRAPH = {
  version: 1,
  language: "python",
  nodes: [
    { id: "a", kind: "file", name: "app.py", path: "app.py" },
    { id: "b", kind: "file", name: "util.py", path: "util.py" },
    { id: "c", kind: "class", name: "App", path: "app.py" },
    { id: "d", kind: "function", name: "main", path: "app.py" },
  ],
  edges: [
    { source: "a", target: "b", kind: "import" },
    { source: "c", target: "d", kind: "call" },
    { source: "c", target: "a", kind: "import" },
  ],
};

describe("GraphLegend", () => {
  it("renders nothing when graph is null", () => {
    const { container } = render(
      <GraphLegend
        graph={null}
        hiddenKinds={new Set()}
        onToggleKind={vi.fn()}
        onShowAll={vi.fn()}
      />
    );
    expect(container.firstChild).toBeNull();
  });

  it("shows node and edge counts", () => {
    render(
      <GraphLegend
        graph={MOCK_GRAPH}
        hiddenKinds={new Set()}
        onToggleKind={vi.fn()}
        onShowAll={vi.fn()}
      />
    );
    expect(screen.getByText(/4 nodes/)).toBeInTheDocument();
    expect(screen.getByText(/3 edges/)).toBeInTheDocument();
  });

  it("renders kind chip buttons for each node kind", () => {
    render(
      <GraphLegend
        graph={MOCK_GRAPH}
        hiddenKinds={new Set()}
        onToggleKind={vi.fn()}
        onShowAll={vi.fn()}
      />
    );
    expect(
      screen.getByRole("button", { name: /hide file nodes/i })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /hide class nodes/i })
    ).toBeInTheDocument();
  });

  it("calls onToggleKind when a kind chip is clicked", () => {
    const onToggleKind = vi.fn();
    render(
      <GraphLegend
        graph={MOCK_GRAPH}
        hiddenKinds={new Set()}
        onToggleKind={onToggleKind}
        onShowAll={vi.fn()}
      />
    );
    fireEvent.click(screen.getByRole("button", { name: /hide file nodes/i }));
    expect(onToggleKind).toHaveBeenCalledWith("file");
  });

  it("shows 'show all' button when kinds are hidden and calls onShowAll", () => {
    const onShowAll = vi.fn();
    render(
      <GraphLegend
        graph={MOCK_GRAPH}
        hiddenKinds={new Set(["file"])}
        onToggleKind={vi.fn()}
        onShowAll={onShowAll}
      />
    );
    const showAllBtn = screen.getByRole("button", { name: /show all/i });
    fireEvent.click(showAllBtn);
    expect(onShowAll).toHaveBeenCalledOnce();
  });

  it("hides 'show all' button when no kinds are hidden", () => {
    render(
      <GraphLegend
        graph={MOCK_GRAPH}
        hiddenKinds={new Set()}
        onToggleKind={vi.fn()}
        onShowAll={vi.fn()}
      />
    );
    expect(
      screen.queryByRole("button", { name: /show all/i })
    ).not.toBeInTheDocument();
  });

  it("renders 8 node-kind chips and 4 edge-kind rows", () => {
    render(
      <GraphLegend
        graph={MOCK_GRAPH}
        hiddenKinds={new Set()}
        onToggleKind={vi.fn()}
        onShowAll={vi.fn()}
      />
    );
    for (const label of [
      "file",
      "class",
      "function",
      "method",
      "interface",
      "type alias",
      "enum",
      "struct",
    ]) {
      expect(
        screen.getByRole("button", {
          name: new RegExp(`hide ${label} nodes`, "i"),
        })
      ).toBeInTheDocument();
    }
    for (const label of ["Import", "Call", "Inherits", "Implements"]) {
      expect(screen.getByText(label)).toBeInTheDocument();
    }
  });

  it("shows layout stats when provided", () => {
    render(
      <GraphLegend
        graph={MOCK_GRAPH}
        hiddenKinds={new Set()}
        onToggleKind={vi.fn()}
        onShowAll={vi.fn()}
        layoutStats={{ nodeCount: 4, layoutMs: 42 }}
      />
    );
    expect(screen.getByText(/42ms layout/)).toBeInTheDocument();
  });
});
