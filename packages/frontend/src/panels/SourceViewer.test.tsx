import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useGraphStore } from "../graph/useGraphStore";
import { _resetSourceCacheForTest } from "../source/useSource";
import { useGrackleClient } from "../ws/client";
import { SourceViewer } from "./SourceViewer";

afterEach(() => {
  cleanup();
  _resetSourceCacheForTest();
  vi.restoreAllMocks();
});

const MOCK_GRAPH = {
  version: 1,
  language: "python",
  nodes: [
    { id: "a.py:App", kind: "class", name: "App", path: "a.py", line: 5 },
    { id: "b.py:main", kind: "function", name: "main", path: "b.py" },
  ],
  edges: [],
};

// Mock the Shiki highlighter so tests don't load the real highlighter.
vi.mock("../source/highlighter", () => ({
  highlightPython: async (source: string) =>
    `<pre><code><span class="line">${source.replace(/\n/g, '</span><span class="line">')}</span></code></pre>`,
}));

beforeEach(() => {
  useGraphStore.setState({
    graph: MOCK_GRAPH,
    selectedNodeId: null,
    sourceViewerTarget: null,
    hiddenKinds: new Set<string>(),
    searchTerm: "",
    excludeGlobs: [],
  });
  useGrackleClient.setState({
    sendReadSource: () =>
      Promise.resolve({
        id: "1",
        type: "source_response" as const,
        payload: {
          path: "a.py",
          source: "class App:\n    pass\n",
          encoding: "utf-8",
        },
      }),
  });
});

describe("SourceViewer", () => {
  it("renders nothing when graph is null", () => {
    useGraphStore.setState({ graph: null });
    const { container } = render(<SourceViewer />);
    expect(container.firstChild).toBeNull();
  });

  it("shows empty-state prompt when no node is selected", () => {
    render(<SourceViewer />);
    expect(
      screen.getByText(/Click a node to view source/i)
    ).toBeInTheDocument();
  });

  it("shows skeleton while loading", () => {
    useGraphStore.setState({ selectedNodeId: "a.py:App" });
    useGrackleClient.setState({
      sendReadSource: () => new Promise(() => {}), // never resolves
    });
    render(<SourceViewer />);
    expect(screen.getByLabelText(/Loading source/i)).toBeInTheDocument();
  });

  it("shows the file path in the header once loaded", async () => {
    useGraphStore.setState({ selectedNodeId: "a.py:App" });
    render(<SourceViewer />);
    await waitFor(() =>
      expect(screen.getByLabelText(/Source viewer/i)).toBeInTheDocument()
    );
    expect(screen.getByText("a.py")).toBeInTheDocument();
  });

  it("renders highlighted lines after loading", async () => {
    useGraphStore.setState({ selectedNodeId: "a.py:App" });
    render(<SourceViewer />);
    await waitFor(() =>
      expect(screen.getByText("class App:")).toBeInTheDocument()
    );
  });

  it("shows error state when source load fails", async () => {
    useGrackleClient.setState({
      sendReadSource: () =>
        Promise.resolve({
          id: "2",
          type: "source_error" as const,
          payload: { path: "a.py", reason: "not_found" as const },
        }),
    });
    useGraphStore.setState({ selectedNodeId: "a.py:App" });
    render(<SourceViewer />);
    await waitFor(() =>
      expect(screen.getByText(/Could not load source/i)).toBeInTheDocument()
    );
  });

  it("shows empty-state when selection is cleared", async () => {
    useGraphStore.setState({ selectedNodeId: "a.py:App" });
    render(<SourceViewer />);
    await waitFor(() => expect(screen.getByText("a.py")).toBeInTheDocument());
    useGraphStore.setState({ selectedNodeId: null });
    await waitFor(() =>
      expect(
        screen.getByText(/Click a node to view source/i)
      ).toBeInTheDocument()
    );
  });

  // Edge evidence (Phase 10.4): an explicit sourceViewerTarget drives the view.

  it("renders from an explicit sourceViewerTarget with no node selected", async () => {
    useGraphStore.setState({
      selectedNodeId: null,
      sourceViewerTarget: { path: "b.py", line: 1 },
    });
    render(<SourceViewer />);
    await waitFor(() =>
      expect(screen.getByLabelText(/Source viewer/i)).toBeInTheDocument()
    );
    expect(screen.getByText("b.py")).toBeInTheDocument();
    expect(
      screen.queryByText(/Click a node to view source/i)
    ).not.toBeInTheDocument();
  });

  it("prefers sourceViewerTarget over the selected node's own file", async () => {
    // A selected node (a.py) AND an edge-evidence jump into a DIFFERENT file
    // (b.py) — the explicit target must win so the jump lands where clicked.
    useGraphStore.setState({
      selectedNodeId: "a.py:App",
      sourceViewerTarget: { path: "b.py", line: 3 },
    });
    render(<SourceViewer />);
    await waitFor(() =>
      expect(screen.getByLabelText(/Source viewer/i)).toBeInTheDocument()
    );
    // Header shows the TARGET file (b.py), not the selected node's file (a.py):
    // under a node-wins regression this would read "a.py" and fail.
    expect(screen.getByText("b.py")).toBeInTheDocument();
  });
});
