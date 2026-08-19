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

/**
 * A minimal single-Linear-layer "demo net" trace: one record_architecture
 * beacon, one train_step (forward -> loss -> loss-grad -> backward -> update
 * -> reset -> idle), one record_epoch return, one record_layer_stats return.
 *
 * `frame_depth` mirrors the real shape (`packages/nn/run-a.jsonl`): train_step
 * at 3, Sequential.forward/backward and the loss/optimizer calls at 4, the
 * per-layer calls at 5. It is load-bearing — depth is what tells
 * `layerActivity` a frame has exited, including the exception case where no
 * `return` event is ever emitted.
 *
 * Indices in the comments are absolute positions in this array, used directly
 * by the playhead tests rather than re-derived from layerActivity.test.ts's
 * golden-34 fixture (that module's own correctness is pinned there; this file
 * only needs to prove NetworkViewPanel wires the data through).
 */
type Row = [event: string, node_id: string, frame_depth: number, ret?: string];

const NET_ROWS: Row[] = [
  ["return", "grackle_nn/metrics.py:record_architecture", 3, "'linear:4:2'"], // 0
  ["call", "grackle_nn/train.py:train_step", 3], // 1
  ["call", "grackle_nn/model.py:Sequential.forward", 4], // 2
  ["call", "grackle_nn/layers.py:Linear.forward", 5], // 3 <- forward-train
  ["return", "grackle_nn/layers.py:Linear.forward", 5], // 4
  ["return", "grackle_nn/model.py:Sequential.forward", 4], // 5
  ["call", "grackle_nn/losses.py:SoftmaxCrossEntropy.forward", 4], // 6 <- loss
  ["return", "grackle_nn/losses.py:SoftmaxCrossEntropy.forward", 4], // 7
  ["call", "grackle_nn/losses.py:SoftmaxCrossEntropy.backward", 4], // 8 <- loss-grad
  ["return", "grackle_nn/losses.py:SoftmaxCrossEntropy.backward", 4], // 9
  ["call", "grackle_nn/model.py:Sequential.backward", 4], // 10
  ["call", "grackle_nn/layers.py:Linear.backward", 5], // 11 <- backward
  ["return", "grackle_nn/layers.py:Linear.backward", 5], // 12
  ["return", "grackle_nn/model.py:Sequential.backward", 4], // 13
  ["call", "grackle_nn/optim.py:SGD.step", 4], // 14 <- update
  ["return", "grackle_nn/optim.py:SGD.step", 4], // 15
  ["call", "grackle_nn/model.py:Sequential.zero_grad", 4], // 16 <- reset
  ["return", "grackle_nn/model.py:Sequential.zero_grad", 4], // 17
  ["return", "grackle_nn/train.py:train_step", 3], // 18 <- idle
  ["return", "grackle_nn/metrics.py:record_epoch", 3, "(0, 0.5, 0.75)"], // 19
  ["return", "grackle_nn/metrics.py:record_layer_stats", 3, "(0, 0.2, 0.05)"], // 20
];

function toEvents(rows: readonly Row[]): TraceEvent[] {
  return rows.map(([event, node_id, frame_depth, ret], i) => ({
    event,
    node_id,
    ts_ns: i,
    thread_id: 1,
    frame_depth,
    ...(ret === undefined ? {} : { values: { ret } }),
  }));
}

const NET_EVENTS: TraceEvent[] = toEvents(NET_ROWS);

/** Captured values present, but no `record_architecture` anywhere. */
const NO_BEACON_EVENTS: TraceEvent[] = toEvents([
  ["call", "some/module.py:foo", 0],
  ["return", "some/module.py:foo", 0, "1"],
]);

/** No `values` on any event at all — i.e. traced without --capture-values. */
const NO_CAPTURE_EVENTS: TraceEvent[] = toEvents([
  ["call", "grackle_nn/metrics.py:record_architecture", 3],
  ["return", "grackle_nn/metrics.py:record_architecture", 3],
]);

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
  it("shows the quiet degrade message when values were captured but no beacon fired", () => {
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

  it("blames --capture-values, not the beacons, when nothing was captured", () => {
    // The beacons DID fire here — they just carry no payload, so "no network
    // beacons in this trace" would be the wrong diagnosis and would leave the
    // user with no route to the actual fix.
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: NO_CAPTURE_EVENTS, loaded: true })
    );
    useGraphStore.setState({ traceSessionId: "s1" });
    render(<NetworkViewPanel />);
    expandPanel();
    expect(screen.getByText(/--capture-values/)).toBeInTheDocument();
    expect(
      screen.queryByText(
        "No network beacons (record_architecture) in this trace."
      )
    ).toBeNull();
  });
});

describe("NetworkViewPanel — hover tooltip", () => {
  /** Layout for `linear:4:2` in the stubbed 800x400 container: columns at
   *  x=40 (4 neurons, y 167/189/211/233) and x=760 (2 neurons, y 189/211).
   *  jsdom's getBoundingClientRect is all-zero, so client coords ARE canvas
   *  coords. */
  function setupHover(playhead = 20): HTMLElement {
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: NET_EVENTS, loaded: true })
    );
    useGraphStore.setState({ traceSessionId: "s1", tracePlayhead: playhead });
    render(<NetworkViewPanel />);
    expandPanel();
    return screen.getByLabelText("Network view canvas");
  }

  it("names the column under a neuron", () => {
    const canvas = setupHover();
    fireEvent.mouseMove(canvas, { clientX: 40, clientY: 167 });
    expect(screen.getByText("input · 4")).toBeInTheDocument();

    fireEvent.mouseMove(canvas, { clientX: 760, clientY: 189 });
    expect(screen.getByText("logits · 2")).toBeInTheDocument();
  });

  it("reads the live w/dw off a bundle at the playhead", () => {
    const canvas = setupHover();
    // Midpoint of the (40,167) -> (760,189) weight line.
    fireEvent.mouseMove(canvas, { clientX: 400, clientY: 178 });
    expect(
      screen.getByText("linear 4→2 · w 0.200 · dw 0.050")
    ).toBeInTheDocument();
  });

  it("falls back to the bare label before any stats have fired", () => {
    const canvas = setupHover(5); // before record_layer_stats at index 20
    fireEvent.mouseMove(canvas, { clientX: 400, clientY: 178 });
    expect(screen.getByText("linear 4→2")).toBeInTheDocument();
  });

  it("clears on mouseleave and on a miss", () => {
    const canvas = setupHover();
    fireEvent.mouseMove(canvas, { clientX: 40, clientY: 167 });
    expect(screen.getByText("input · 4")).toBeInTheDocument();

    fireEvent.mouseMove(canvas, { clientX: 400, clientY: 5 }); // empty space
    expect(screen.queryByText("input · 4")).toBeNull();

    fireEvent.mouseMove(canvas, { clientX: 40, clientY: 167 });
    fireEvent.mouseLeave(canvas);
    expect(screen.queryByText("input · 4")).toBeNull();
  });

  it("does not resurrect a stale tooltip when the card is reopened", () => {
    // Closing from the keyboard leaves the pointer over the canvas, so the
    // canvas's own mouseleave never fires.
    const canvas = setupHover();
    fireEvent.mouseMove(canvas, { clientX: 40, clientY: 167 });
    expect(screen.getByText("input · 4")).toBeInTheDocument();

    fireEvent.click(screen.getByRole("button", { name: "Close network view" }));
    expandPanel();
    expect(screen.queryByText("input · 4")).toBeNull();
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
