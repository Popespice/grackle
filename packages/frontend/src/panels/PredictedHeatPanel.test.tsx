import type { Graph, TraceEvent } from "@grackle/shared-types";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useGraphStore } from "../graph/useGraphStore";
import { PredictedHeatPanel } from "./PredictedHeatPanel";

function callEvent(nodeId: string, tsNs: number): TraceEvent {
  return {
    event: "call",
    node_id: nodeId,
    ts_ns: tsNs,
    thread_id: 1,
    frame_depth: 0,
  };
}

function graphWithPredictedHeat(
  scores: Array<{ node_id: string; score: number }>,
  modelVersion = 3
): Graph {
  return {
    version: 1,
    language: "python",
    nodes: scores.map((s) => ({
      id: s.node_id,
      kind: "function",
      name: s.node_id,
      path: `${s.node_id}.py`,
    })),
    edges: [],
    metadata: {
      predicted_heat: { model_version: modelVersion, scores },
    },
  } as unknown as Graph;
}

const NO_METADATA_GRAPH: Graph = {
  version: 1,
  language: "python",
  nodes: [{ id: "a", kind: "function", name: "a", path: "a.py" }],
  edges: [],
} as unknown as Graph;

// Full reset to the pristine store snapshot (CausalPathPanel/LossCurvePanel
// precedent) — automatically covers predictedOverlay and every other field,
// so no manually-enumerated list can drift stale.
const INITIAL_STORE_STATE = useGraphStore.getState();

afterEach(cleanup);

beforeEach(() => {
  useGraphStore.setState(INITIAL_STORE_STATE, true);
  useGraphStore.setState({
    graph: null,
    traceSessionId: null,
    traceEvents: [],
    agentHeat: null,
  });
});

describe("PredictedHeatPanel", () => {
  it("renders nothing when the graph has no predicted_heat metadata", () => {
    useGraphStore.setState({ graph: NO_METADATA_GRAPH });
    const { container } = render(<PredictedHeatPanel />);
    expect(container.firstChild).toBeNull();
  });

  it("renders nothing when there is no graph at all", () => {
    const { container } = render(<PredictedHeatPanel />);
    expect(container.firstChild).toBeNull();
  });

  it("renders the header when predicted_heat metadata IS present (mutation guard)", () => {
    useGraphStore.setState({
      graph: graphWithPredictedHeat([{ node_id: "a", score: 0.9 }], 3),
    });
    render(<PredictedHeatPanel />);
    expect(screen.getByText("(model v3, 1 nodes)")).toBeInTheDocument();
  });

  it("starts in Off mode and writes no overlay by default", () => {
    useGraphStore.setState({
      graph: graphWithPredictedHeat([{ node_id: "a", score: 0.9 }]),
    });
    render(<PredictedHeatPanel />);
    expect(screen.getByRole("button", { name: "Off" })).toHaveAttribute(
      "aria-pressed",
      "true"
    );
    expect(useGraphStore.getState().predictedOverlay).toBeNull();
  });

  it("clicking Predicted writes a scores-mode overlay after the debounce", async () => {
    useGraphStore.setState({
      graph: graphWithPredictedHeat([{ node_id: "a", score: 0.9 }]),
    });
    render(<PredictedHeatPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Predicted" }));
    await waitFor(
      () => {
        const overlay = useGraphStore.getState().predictedOverlay;
        expect(overlay?.kind).toBe("scores");
      },
      { timeout: 1000 }
    );
  });

  it("clicking Vs actual writes a status-mode overlay after the debounce", async () => {
    useGraphStore.setState({
      graph: graphWithPredictedHeat([{ node_id: "a", score: 0.9 }]),
      traceSessionId: "s1",
      agentHeat: { a: 5 },
    });
    render(<PredictedHeatPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Vs actual" }));
    await waitFor(
      () => {
        const overlay = useGraphStore.getState().predictedOverlay;
        expect(overlay?.kind).toBe("status");
      },
      { timeout: 1000 }
    );
  });

  it("clicking Off clears a previously-written overlay", async () => {
    useGraphStore.setState({
      graph: graphWithPredictedHeat([{ node_id: "a", score: 0.9 }]),
    });
    render(<PredictedHeatPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Predicted" }));
    await waitFor(() =>
      expect(useGraphStore.getState().predictedOverlay).not.toBeNull()
    );
    fireEvent.click(screen.getByRole("button", { name: "Off" }));
    await waitFor(() =>
      expect(useGraphStore.getState().predictedOverlay).toBeNull()
    );
  });

  it("unmounting the panel clears the overlay", async () => {
    useGraphStore.setState({
      graph: graphWithPredictedHeat([{ node_id: "a", score: 0.9 }]),
    });
    const { unmount } = render(<PredictedHeatPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Predicted" }));
    await waitFor(() =>
      expect(useGraphStore.getState().predictedOverlay).not.toBeNull()
    );
    unmount();
    expect(useGraphStore.getState().predictedOverlay).toBeNull();
  });

  it("disables the Vs actual button without a trace session", () => {
    useGraphStore.setState({
      graph: graphWithPredictedHeat([{ node_id: "a", score: 0.9 }]),
      traceSessionId: null,
    });
    render(<PredictedHeatPanel />);
    expect(screen.getByRole("button", { name: "Vs actual" })).toBeDisabled();
  });

  it("enables the Vs actual button with a trace session", () => {
    useGraphStore.setState({
      graph: graphWithPredictedHeat([{ node_id: "a", score: 0.9 }]),
      traceSessionId: "s1",
    });
    render(<PredictedHeatPanel />);
    expect(
      screen.getByRole("button", { name: "Vs actual" })
    ).not.toBeDisabled();
  });

  it("orders the surprise-hotspots (under) list by actual count, descending", async () => {
    // "hot_predicted" is the only predicted node (max=0.9); surprise1/surprise2
    // are entirely absent from predictions but have high actual counts, so
    // both classify "under" — surprise1 (100 hits) must rank above surprise2
    // (50 hits).
    useGraphStore.setState({
      graph: graphWithPredictedHeat([{ node_id: "hot_predicted", score: 0.9 }]),
      traceSessionId: "s1",
      agentHeat: { surprise1: 100, surprise2: 50 },
    });
    render(<PredictedHeatPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Vs actual" }));

    await waitFor(() =>
      expect(useGraphStore.getState().predictedOverlay?.kind).toBe("status")
    );

    const heading = screen.getByText("Surprise hotspots (under-predicted)");
    const list = heading.nextElementSibling as HTMLElement;
    const items = list.querySelectorAll("button[title]");
    expect(items).toHaveLength(2);
    expect(items[0]).toHaveAttribute("title", "surprise1");
    expect(items[1]).toHaveAttribute("title", "surprise2");
  });

  it("regression: predicted mode still paints while traceEvents keeps changing (streaming must not starve the debounce)", async () => {
    // Before the fix, the push effect depended on BOTH scoresOverlay and
    // statusOverlay unconditionally — so in "predicted" mode, a churning
    // statusOverlay (driven by currentCounts, which used to recompute on
    // every traceEvents change regardless of mode) re-armed the debounce
    // timer every batch, and setPredictedOverlay never survived long
    // enough to fire.
    useGraphStore.setState({
      graph: graphWithPredictedHeat([{ node_id: "a", score: 0.9 }]),
      traceSessionId: "s1",
    });
    const { rerender } = render(<PredictedHeatPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Predicted" }));

    for (let i = 0; i < 5; i++) {
      useGraphStore.setState({ traceEvents: [callEvent(`n${i}`, i)] });
      rerender(<PredictedHeatPanel />);
      await new Promise((resolve) => setTimeout(resolve, 40)); // < 150ms debounce
    }

    await waitFor(
      () => {
        const overlay = useGraphStore.getState().predictedOverlay;
        expect(overlay?.kind).toBe("scores");
      },
      { timeout: 1000 }
    );
  });

  it("shows the top-predicted list with the highest score first", () => {
    useGraphStore.setState({
      graph: graphWithPredictedHeat([
        { node_id: "low", score: 0.1 },
        { node_id: "high", score: 0.9 },
      ]),
    });
    render(<PredictedHeatPanel />);
    const heading = screen.getByText("Top predicted");
    const list = heading.nextElementSibling as HTMLElement;
    const items = list.querySelectorAll("button[title]");
    expect(items[0]).toHaveAttribute("title", "high");
    expect(items[1]).toHaveAttribute("title", "low");
  });
});
