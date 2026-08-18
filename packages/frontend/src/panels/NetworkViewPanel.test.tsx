import type { TraceEvent } from "@grackle/shared-types";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import {
  afterAll,
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { type UseFullTraceResult, useFullTrace } from "../graph/useFullTrace";
import { useGraphStore } from "../graph/useGraphStore";
import { NetworkViewPanel } from "./NetworkViewPanel";

vi.mock("../graph/useFullTrace");
const mockUseFullTrace = vi.mocked(useFullTrace);

function fullTrace(over: Partial<UseFullTraceResult> = {}): UseFullTraceResult {
  return {
    events: [],
    truncated: false,
    loading: false,
    error: false,
    loaded: false,
    load: vi.fn(),
    ...over,
  };
}

// jsdom reports 0 for both — give the container a real size so layoutNetwork
// yields a real, hit-testable layout. getContext is stubbed to null
// (FlameGraphPanel/LossCurvePanel precedent) so the paint effect no-ops — we
// test the data/controls/interaction layer, not canvas pixels.
let originalClientWidth: PropertyDescriptor | undefined;
let originalClientHeight: PropertyDescriptor | undefined;
let originalGetContext: typeof HTMLCanvasElement.prototype.getContext;

beforeAll(() => {
  originalClientWidth = Object.getOwnPropertyDescriptor(
    HTMLElement.prototype,
    "clientWidth"
  );
  originalClientHeight = Object.getOwnPropertyDescriptor(
    HTMLElement.prototype,
    "clientHeight"
  );
  originalGetContext = HTMLCanvasElement.prototype.getContext;
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get: () => 800,
  });
  Object.defineProperty(HTMLElement.prototype, "clientHeight", {
    configurable: true,
    get: () => 400,
  });
  HTMLCanvasElement.prototype.getContext = vi.fn(() => null);
});

afterAll(() => {
  if (originalClientWidth) {
    Object.defineProperty(
      HTMLElement.prototype,
      "clientWidth",
      originalClientWidth
    );
  }
  if (originalClientHeight) {
    Object.defineProperty(
      HTMLElement.prototype,
      "clientHeight",
      originalClientHeight
    );
  }
  HTMLCanvasElement.prototype.getContext = originalGetContext;
});

function retEvent(node_id: string, ret: string, index: number): TraceEvent {
  return {
    event: "return",
    node_id,
    ts_ns: index,
    thread_id: 1,
    frame_depth: 0,
    values: { ret },
  };
}

function callEvent(node_id: string, index: number): TraceEvent {
  return { event: "call", node_id, ts_ns: index, thread_id: 1, frame_depth: 0 };
}

/**
 * A minimal single-Linear-layer "demo net" trace: one record_architecture
 * beacon, one train_step (forward -> loss -> loss-grad -> backward -> update
 * -> reset -> idle), one record_epoch return, one record_layer_stats return.
 * Indices below are absolute positions in this array — used directly by the
 * playhead tests rather than re-deriving them from layerActivity.test.ts's
 * golden-34 fixture (that module's own correctness is already pinned there;
 * this file only needs to prove NetworkViewPanel wires the data through).
 */
const NET_EVENTS: TraceEvent[] = [
  retEvent("grackle_nn/metrics.py:record_architecture", "'linear:4:2'", 0), // 0
  callEvent("grackle_nn/train.py:train_step", 1), // 1
  callEvent("grackle_nn/model.py:Sequential.forward", 2), // 2
  callEvent("grackle_nn/layers.py:Linear.forward", 3), // 3 <- forward-train segment fires here
  retEvent("grackle_nn/layers.py:Linear.forward", "<ndarray>", 4), // 4
  {
    event: "return",
    node_id: "grackle_nn/model.py:Sequential.forward",
    ts_ns: 5,
    thread_id: 1,
    frame_depth: 0,
  }, // 5
  callEvent("grackle_nn/losses.py:SoftmaxCrossEntropy.forward", 6), // 6 <- loss
  {
    event: "return",
    node_id: "grackle_nn/losses.py:SoftmaxCrossEntropy.forward",
    ts_ns: 7,
    thread_id: 1,
    frame_depth: 0,
  }, // 7
  callEvent("grackle_nn/losses.py:SoftmaxCrossEntropy.backward", 8), // 8 <- loss-grad
  {
    event: "return",
    node_id: "grackle_nn/losses.py:SoftmaxCrossEntropy.backward",
    ts_ns: 9,
    thread_id: 1,
    frame_depth: 0,
  }, // 9
  callEvent("grackle_nn/model.py:Sequential.backward", 10), // 10
  callEvent("grackle_nn/layers.py:Linear.backward", 11), // 11 <- backward segment fires here
  {
    event: "return",
    node_id: "grackle_nn/layers.py:Linear.backward",
    ts_ns: 12,
    thread_id: 1,
    frame_depth: 0,
  }, // 12
  {
    event: "return",
    node_id: "grackle_nn/model.py:Sequential.backward",
    ts_ns: 13,
    thread_id: 1,
    frame_depth: 0,
  }, // 13
  callEvent("grackle_nn/optim.py:SGD.step", 14), // 14 <- update
  {
    event: "return",
    node_id: "grackle_nn/optim.py:SGD.step",
    ts_ns: 15,
    thread_id: 1,
    frame_depth: 0,
  }, // 15
  callEvent("grackle_nn/model.py:Sequential.zero_grad", 16), // 16 <- reset
  {
    event: "return",
    node_id: "grackle_nn/model.py:Sequential.zero_grad",
    ts_ns: 17,
    thread_id: 1,
    frame_depth: 0,
  }, // 17
  {
    event: "return",
    node_id: "grackle_nn/train.py:train_step",
    ts_ns: 18,
    thread_id: 1,
    frame_depth: 0,
  }, // 18 <- idle
  retEvent("grackle_nn/metrics.py:record_epoch", "(0, 0.5, 0.75)", 19), // 19
  retEvent("grackle_nn/metrics.py:record_layer_stats", "(0, 0.2, 0.05)", 20), // 20
];

const NO_BEACON_EVENTS: TraceEvent[] = [
  callEvent("some/module.py:foo", 0),
  {
    event: "return",
    node_id: "some/module.py:foo",
    ts_ns: 1,
    thread_id: 1,
    frame_depth: 0,
  },
];

const INITIAL_STORE_STATE = useGraphStore.getState();

afterEach(cleanup);

beforeEach(() => {
  mockUseFullTrace.mockReturnValue(fullTrace());
  useGraphStore.setState(INITIAL_STORE_STATE, true);
  useGraphStore.setState({
    traceSessionId: null,
    traceSeekable: false,
    traceTotal: 0,
    tracePlayhead: 0,
  });
});

function expandPanel(): void {
  fireEvent.click(screen.getByRole("button", { name: "Open network view" }));
}

describe("NetworkViewPanel — chip visibility", () => {
  it("renders nothing without a trace session", () => {
    const { container } = render(<NetworkViewPanel />);
    expect(container.firstChild).toBeNull();
  });

  it("renders a collapsed chip when a session exists", () => {
    useGraphStore.setState({ traceSessionId: "s1" });
    render(<NetworkViewPanel />);
    expect(
      screen.getByRole("button", { name: "Open network view" })
    ).toBeInTheDocument();
    // The card is not shown until expanded.
    expect(screen.queryByLabelText("Network view canvas")).toBeNull();
  });
});

describe("NetworkViewPanel — expand / collapse", () => {
  it("clicking the chip opens the card; clicking close collapses it again", () => {
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: NET_EVENTS, loaded: true })
    );
    useGraphStore.setState({ traceSessionId: "s1" });
    render(<NetworkViewPanel />);

    expandPanel();
    expect(screen.getByLabelText("Network view canvas")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close network view" }));
    expect(
      screen.getByRole("button", { name: "Open network view" })
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Network view canvas")).toBeNull();
  });

  it("a new session collapses an already-expanded panel", () => {
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: NET_EVENTS, loaded: true })
    );
    useGraphStore.setState({ traceSessionId: "s1" });
    const { rerender } = render(<NetworkViewPanel />);
    expandPanel();
    expect(screen.getByLabelText("Network view canvas")).toBeInTheDocument();

    useGraphStore.setState({ traceSessionId: "s2" });
    rerender(<NetworkViewPanel />);
    expect(
      screen.getByRole("button", { name: "Open network view" })
    ).toBeInTheDocument();
  });
});

describe("NetworkViewPanel — seekable loading states", () => {
  it("shows a Load button with the event count when seekable and unloaded", () => {
    mockUseFullTrace.mockReturnValue(fullTrace({ loaded: false }));
    useGraphStore.setState({
      traceSessionId: "s1",
      traceSeekable: true,
      traceTotal: 500,
    });
    render(<NetworkViewPanel />);
    expandPanel();
    expect(
      screen.getByRole("button", { name: "Load network view (500 events)" })
    ).toBeInTheDocument();
  });

  it("clicking Load calls full.load", () => {
    const load = vi.fn();
    mockUseFullTrace.mockReturnValue(fullTrace({ loaded: false, load }));
    useGraphStore.setState({ traceSessionId: "s1", traceSeekable: true });
    render(<NetworkViewPanel />);
    expandPanel();
    fireEvent.click(screen.getByRole("button", { name: /Load network view/ }));
    expect(load).toHaveBeenCalled();
  });

  it("shows the truncated banner only when seekable and truncated", () => {
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: NET_EVENTS, loaded: true, truncated: true })
    );
    useGraphStore.setState({ traceSessionId: "s1", traceSeekable: true });
    render(<NetworkViewPanel />);
    expandPanel();
    expect(
      screen.getByText(/Network view covers the first .* events only\./)
    ).toBeInTheDocument();
  });

  it("does not show the truncated banner in buffered mode", () => {
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: NET_EVENTS, loaded: true, truncated: true })
    );
    useGraphStore.setState({ traceSessionId: "s1", traceSeekable: false });
    render(<NetworkViewPanel />);
    expandPanel();
    expect(screen.queryByText(/events only\./)).toBeNull();
  });
});

describe("NetworkViewPanel — no-beacon degrade", () => {
  it("shows the quiet degrade message when no record_architecture beacon is present", () => {
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: NO_BEACON_EVENTS, loaded: true })
    );
    useGraphStore.setState({ traceSessionId: "s1" });
    render(<NetworkViewPanel />);
    expandPanel();
    expect(
      screen.getByText(
        "No network beacons (record_architecture) in this trace."
      )
    ).toBeInTheDocument();
    expect(screen.queryByLabelText("Network view canvas")).toBeNull();
  });
});

describe("NetworkViewPanel — header readout and phase label", () => {
  it("shows the model shape and epoch/loss/acc readout at the golden fixture's playhead", () => {
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: NET_EVENTS, loaded: true })
    );
    useGraphStore.setState({ traceSessionId: "s1", tracePlayhead: 20 });
    render(<NetworkViewPanel />);
    expandPanel();
    expect(
      screen.getByText("model: 4-2 · epoch 0 · loss 0.5000 · acc 0.750")
    ).toBeInTheDocument();
  });

  it("shows just the model shape before any epoch has completed", () => {
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: NET_EVENTS, loaded: true })
    );
    useGraphStore.setState({ traceSessionId: "s1", tracePlayhead: 2 });
    render(<NetworkViewPanel />);
    expandPanel();
    expect(screen.getByText("model: 4-2")).toBeInTheDocument();
  });

  it("the phase chip tracks setPlayhead across the sweep", () => {
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: NET_EVENTS, loaded: true })
    );
    useGraphStore.setState({ traceSessionId: "s1", tracePlayhead: 0 });
    const { rerender } = render(<NetworkViewPanel />);
    expandPanel();
    expect(screen.getByText("idle")).toBeInTheDocument();

    useGraphStore.setState({ tracePlayhead: 4 });
    rerender(<NetworkViewPanel />);
    expect(screen.getByText("forward · train")).toBeInTheDocument();

    useGraphStore.setState({ tracePlayhead: 12 });
    rerender(<NetworkViewPanel />);
    expect(screen.getByText("backward")).toBeInTheDocument();

    useGraphStore.setState({ tracePlayhead: 15 });
    rerender(<NetworkViewPanel />);
    expect(screen.getByText("update")).toBeInTheDocument();
  });
});
