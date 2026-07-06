import type { Graph } from "@grackle/shared-types";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useGraphStore } from "../graph/useGraphStore";
import { type SourceState, useSource } from "../source/useSource";
import { EdgeEvidencePanel } from "./EdgeEvidencePanel";

vi.mock("../source/useSource");
const mockUseSource = vi.mocked(useSource);

// a.py source: `helper()` calls sit on lines 3 and 7. Snippets are single
// tokens so testing-library's whitespace normalization can't blur the match.
const A_SRC = [
  "import b",
  "",
  "alpha_call()",
  "x = 1",
  "",
  "",
  "beta_call()",
].join("\n");

function loaded(path: string, source: string): SourceState {
  return { status: "loaded", path, source };
}

const GRAPH: Graph = {
  version: 1,
  language: "python",
  nodes: [
    { id: "a.py:caller", kind: "function", name: "caller", path: "a.py" },
    { id: "b.py:helper", kind: "function", name: "helper", path: "b.py" },
  ],
  edges: [
    {
      source: "a.py:caller",
      target: "b.py:helper",
      kind: "call",
      metadata: { line: 3 },
    },
    {
      source: "a.py:caller",
      target: "b.py:helper",
      kind: "call",
      metadata: { line: 7 },
    },
  ],
} as unknown as Graph;

afterEach(cleanup);

beforeEach(() => {
  mockUseSource.mockReturnValue(loaded("a.py", A_SRC));
  useGraphStore.setState({
    graph: null,
    selectedNodeId: null,
    selectedEdge: null,
    sourceViewerTarget: null,
  });
});

describe("EdgeEvidencePanel", () => {
  it("renders nothing without a selection", () => {
    useGraphStore.setState({ graph: GRAPH });
    const { container } = render(<EdgeEvidencePanel />);
    expect(container.firstChild).toBeNull();
  });

  it("lists a node's out-edges with distinct lines and snippets", () => {
    useGraphStore.setState({ graph: GRAPH, selectedNodeId: "a.py:caller" });
    render(<EdgeEvidencePanel />);

    // Two call edges to the same target on different lines → two rows.
    expect(screen.getByText("a.py:3")).toBeInTheDocument();
    expect(screen.getByText("a.py:7")).toBeInTheDocument();
    // Snippets are derived from the loaded source line.
    expect(screen.getByText("alpha_call()")).toBeInTheDocument();
    expect(screen.getByText("beta_call()")).toBeInTheDocument();
  });

  it("jumps to the exact source line on row click", () => {
    useGraphStore.setState({ graph: GRAPH, selectedNodeId: "a.py:caller" });
    render(<EdgeEvidencePanel />);

    fireEvent.click(
      screen.getByText("a.py:7").closest("button") as HTMLElement
    );
    expect(useGraphStore.getState().sourceViewerTarget).toEqual({
      path: "a.py",
      line: 7,
    });
  });

  it("shows the pair heading in edge-selected mode", () => {
    useGraphStore.setState({
      graph: GRAPH,
      selectedEdge: { source: "a.py:caller", target: "b.py:helper" },
    });
    render(<EdgeEvidencePanel />);
    expect(screen.getByText("caller → helper")).toBeInTheDocument();
  });

  it("degrades cleanly for a line-less edge (no snippet, disabled jump)", () => {
    const g: Graph = {
      version: 1,
      language: "python",
      nodes: [
        { id: "a.py:caller", kind: "function", name: "caller", path: "a.py" },
        { id: "svc.ts:route", kind: "function", name: "route", path: "svc.ts" },
      ],
      edges: [
        {
          source: "a.py:caller",
          target: "svc.ts:route",
          kind: "cross_language_call",
          metadata: { resolved: true },
        },
      ],
    } as unknown as Graph;
    useGraphStore.setState({ graph: g, selectedNodeId: "a.py:caller" });
    render(<EdgeEvidencePanel />);

    // The row renders (kind verb + other node), but the jump button is disabled
    // and no source line is shown.
    const btn = screen
      .getByText("route")
      .closest("button") as HTMLButtonElement;
    expect(btn).toBeInTheDocument();
    expect(btn.disabled).toBe(true);
    fireEvent.click(btn);
    expect(useGraphStore.getState().sourceViewerTarget).toBeNull();
  });
});
