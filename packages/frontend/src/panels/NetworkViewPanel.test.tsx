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

/**
 * A recording stand-in for a 2D context. jsdom has no canvas, and the rest of
 * this file stubs `getContext` to null so the paint effect no-ops — which left
 * `paintNetwork` (five draw passes, the active-bundle overdraw, the
 * highlight-column choice, every caption and label) with no coverage at all.
 * This records enough to assert what was drawn and in what colour.
 *
 * Neuron/glyph circles stroke an arc-only path, so `segments` stays 0 for them
 * and `strokes` isolates the edge-bundle passes.
 */
function makeRecorder() {
  const strokes: { style: string; alpha: number; segments: number }[] = [];
  const arcs: { x: number; y: number; fill: string }[] = [];
  const texts: string[] = [];
  const transforms: number[][] = [];
  let segments = 0;
  let lastArc: { x: number; y: number } | null = null;
  const ctx = {
    strokeStyle: "",
    fillStyle: "",
    globalAlpha: 1,
    lineWidth: 1,
    font: "",
    textAlign: "",
    textBaseline: "",
    clearRect: () => {},
    setTransform: (...args: number[]) => {
      transforms.push(args);
    },
    beginPath: () => {
      segments = 0;
      lastArc = null;
    },
    moveTo: () => {},
    lineTo: () => {
      segments += 1;
    },
    arc: (x: number, y: number) => {
      lastArc = { x, y };
    },
    fill: () => {
      if (lastArc) arcs.push({ ...lastArc, fill: String(ctx.fillStyle) });
    },
    stroke: () => {
      if (segments > 0) {
        strokes.push({
          style: String(ctx.strokeStyle),
          alpha: ctx.globalAlpha,
          segments,
        });
      }
    },
    fillText: (text: string) => {
      texts.push(text);
    },
  };
  return { ctx, strokes, arcs, texts, transforms };
}

describe("NetworkViewPanel — what actually gets painted", () => {
  const FORWARD_TRAIN = "#e86b20";
  const BACKWARD = "#8b5cf6";
  const NEURON_FILL = "#39404d";
  /** Column x positions for `linear:4:2` in the stubbed 800x400 container. */
  const SRC_X = 40;
  const DST_X = 760;

  let recorder: ReturnType<typeof makeRecorder>;

  beforeEach(() => {
    recorder = makeRecorder();
    // getContext is overloaded across every context id; the recorder only
    // implements the 2d one, so the assignment needs the cast.
    HTMLCanvasElement.prototype.getContext = vi.fn(
      () => recorder.ctx
    ) as unknown as typeof HTMLCanvasElement.prototype.getContext;
  });

  afterEach(() => {
    HTMLCanvasElement.prototype.getContext = vi.fn(() => null);
  });

  function paintAt(playhead: number, events = NET_EVENTS): void {
    mockUseFullTrace.mockReturnValue(fullTrace({ events, loaded: true }));
    useGraphStore.setState({ traceSessionId: "s1", tracePlayhead: playhead });
    render(<NetworkViewPanel />);
    expandPanel();
  }

  it("labels every column and annotates the bundle with the live weight RMS", () => {
    paintAt(20);
    expect(recorder.texts).toContain("input · 4");
    expect(recorder.texts).toContain("logits · 2");
    expect(recorder.texts).toContain("linear 4→2 · w 0.20");
  });

  it("omits the weight annotation before any stats have fired", () => {
    paintAt(5);
    expect(recorder.texts).toContain("linear 4→2");
    expect(recorder.texts.some((t) => t.includes("· w "))).toBe(false);
  });

  it("states the drawn count, not the total, on a capped column", () => {
    const wide = toEvents([
      [
        "return",
        "grackle_nn/metrics.py:record_architecture",
        3,
        "'linear:100:2'",
      ],
    ]);
    paintAt(0, wide);
    expect(recorder.texts).toContain("input · 100 (64 of 100 shown)");
  });

  it("forward highlights the DESTINATION column in the forward colour", () => {
    paintAt(4);
    expect(recorder.strokes.some((s) => s.style === FORWARD_TRAIN)).toBe(true);
    const dst = recorder.arcs.filter((a) => a.x === DST_X);
    const src = recorder.arcs.filter((a) => a.x === SRC_X);
    // Pin the counts first: `every` over an empty list is vacuously true.
    expect(dst).toHaveLength(2);
    expect(src).toHaveLength(4);
    expect(dst.every((a) => a.fill === FORWARD_TRAIN)).toBe(true);
    expect(src.every((a) => a.fill === NEURON_FILL)).toBe(true);
  });

  it("backward highlights the SOURCE column in violet — the opposite end", () => {
    // The direction is the panel's whole visual claim: gradients flow back
    // toward the input, so the sweep must light the column the bundle starts
    // from, not the one it ends at.
    paintAt(12);
    expect(recorder.strokes.some((s) => s.style === BACKWARD)).toBe(true);
    const src = recorder.arcs.filter((a) => a.x === SRC_X);
    const dst = recorder.arcs.filter((a) => a.x === DST_X);
    expect(src).toHaveLength(4);
    expect(dst).toHaveLength(2);
    expect(src.every((a) => a.fill === BACKWARD)).toBe(true);
    expect(dst.every((a) => a.fill === NEURON_FILL)).toBe(true);
  });

  it("highlights no column at all during update and reset", () => {
    paintAt(15); // optimizer step
    expect(recorder.arcs).toHaveLength(6); // 4 input + 2 logit neurons
    expect(recorder.arcs.every((a) => a.fill === NEURON_FILL)).toBe(true);
  });

  it("draws one path per bundle, not one per line", () => {
    // D-N3: a bundle is a single beginPath/stroke over all its pairs; per-line
    // strokes would be 4x2 separate calls here.
    paintAt(0);
    const bundleStrokes = recorder.strokes.filter((s) => s.segments === 8);
    expect(bundleStrokes).toHaveLength(1); // 4x2 pairs, ONE path
  });
});

describe("NetworkViewPanel — diverged run", () => {
  it("says how many epochs of layer stats were dropped as non-finite", () => {
    const diverged = toEvents([
      ...NET_ROWS,
      [
        "return",
        "grackle_nn/metrics.py:record_layer_stats",
        3,
        "(1, nan, 0.05)",
      ],
    ]);
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: diverged, loaded: true })
    );
    useGraphStore.setState({ traceSessionId: "s1", tracePlayhead: 22 });
    render(<NetworkViewPanel />);
    expandPanel();
    expect(
      screen.getByText(/1 epoch of layer stats dropped/)
    ).toBeInTheDocument();
  });

  it("says nothing when every epoch is finite", () => {
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: NET_EVENTS, loaded: true })
    );
    useGraphStore.setState({ traceSessionId: "s1", tracePlayhead: 20 });
    render(<NetworkViewPanel />);
    expandPanel();
    expect(screen.queryByText(/dropped \(non-finite\)/)).toBeNull();
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

/** The real per-epoch shape of `train.fit` (packages/nn/src/grackle_nn/train.py):
 *  train_step(s), then evaluate(), then the two beacons. LossCurvePanel seeks
 *  to the record_epoch index, so index 8 here is where a click lands. */
const EPOCH_BOUNDARY_ROWS: Row[] = [
  ["return", "grackle_nn/metrics.py:record_architecture", 3, "'linear:4:2'"], // 0
  ["call", "grackle_nn/train.py:train_step", 3], // 1
  ["call", "grackle_nn/model.py:Sequential.forward", 4], // 2
  ["call", "grackle_nn/layers.py:Linear.forward", 5], // 3
  ["return", "grackle_nn/layers.py:Linear.forward", 5], // 4
  ["return", "grackle_nn/model.py:Sequential.forward", 4], // 5
  ["return", "grackle_nn/train.py:train_step", 3], // 6
  ["call", "grackle_nn/train.py:evaluate", 3], // 7
  ["call", "grackle_nn/losses.py:SoftmaxCrossEntropy.forward", 4], // 8
  ["return", "grackle_nn/losses.py:SoftmaxCrossEntropy.forward", 4], // 9
  ["return", "grackle_nn/train.py:evaluate", 3], // 10
  ["return", "grackle_nn/metrics.py:record_epoch", 3, "(0, 0.5, 0.75)"], // 11
  ["return", "grackle_nn/metrics.py:record_layer_stats", 3, "(0, 0.2, 0.05)"], // 12
];

describe("NetworkViewPanel — session isolation", () => {
  it("does not carry a latched spec into the next session", () => {
    // The architecture latch is keyed on the session id and survives a
    // collapse; a new session must not inherit the old net's columns.
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: NET_EVENTS, loaded: true })
    );
    useGraphStore.setState({ traceSessionId: "s1", tracePlayhead: 20 });
    const { rerender } = render(<NetworkViewPanel />);
    expandPanel();
    expect(screen.getByText(/model: 4-2/)).toBeInTheDocument();

    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: NO_BEACON_EVENTS, loaded: true })
    );
    useGraphStore.setState({ traceSessionId: "s2" });
    rerender(<NetworkViewPanel />);
    expandPanel();
    expect(screen.queryByText(/model: 4-2/)).toBeNull();
    expect(
      screen.getByText(
        "No network beacons (record_architecture) in this trace."
      )
    ).toBeInTheDocument();
  });

  it("shows the truncated banner and the diverged-run warning together", () => {
    const diverged = toEvents([
      ...NET_ROWS,
      [
        "return",
        "grackle_nn/metrics.py:record_layer_stats",
        3,
        "(1, nan, 0.05)",
      ],
    ]);
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: diverged, loaded: true, truncated: true })
    );
    useGraphStore.setState({
      traceSessionId: "s1",
      traceSeekable: true,
      tracePlayhead: 22,
    });
    render(<NetworkViewPanel />);
    expandPanel();
    expect(screen.getByText(/events only\./)).toBeInTheDocument();
    expect(
      screen.getByText(/1 epoch of layer stats dropped/)
    ).toBeInTheDocument();
  });
});

describe("NetworkViewPanel — canvas backing store", () => {
  let recorder: ReturnType<typeof makeRecorder>;
  let originalDpr: number;

  beforeEach(() => {
    recorder = makeRecorder();
    HTMLCanvasElement.prototype.getContext = vi.fn(
      () => recorder.ctx
    ) as unknown as typeof HTMLCanvasElement.prototype.getContext;
    originalDpr = window.devicePixelRatio;
  });

  afterEach(() => {
    HTMLCanvasElement.prototype.getContext = vi.fn(() => null);
    Object.defineProperty(window, "devicePixelRatio", {
      configurable: true,
      value: originalDpr,
    });
  });

  it("scales the backing store by devicePixelRatio and matches the transform", () => {
    // A backing store at CSS size on a retina display is the classic blurry
    // canvas; a transform that disagrees with it draws at the wrong scale.
    Object.defineProperty(window, "devicePixelRatio", {
      configurable: true,
      value: 2,
    });
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: NET_EVENTS, loaded: true })
    );
    useGraphStore.setState({ traceSessionId: "s1", tracePlayhead: 20 });
    render(<NetworkViewPanel />);
    expandPanel();
    const canvas = screen.getByLabelText(
      "Network view canvas"
    ) as HTMLCanvasElement;
    expect(canvas.width).toBe(1600); // 800 css px * 2
    expect(canvas.height).toBe(800); // 400 css px * 2
    expect(recorder.transforms.at(-1)).toEqual([2, 0, 0, 2, 0, 0]);
  });

  it("paints an all-zero-weight layer at base alpha rather than NaN", () => {
    // maxima() is 0 for a layer whose wRms never moves off zero, and
    // `wRms / max` would be NaN — which canvas silently IGNORES, leaving the
    // previous bundle's alpha in place and making one layer masquerade as
    // another. The `max > 0` guard in paintNetwork is what prevents it.
    const zeroed = toEvents([
      [
        "return",
        "grackle_nn/metrics.py:record_architecture",
        3,
        "'linear:4:2'",
      ],
      [
        "return",
        "grackle_nn/metrics.py:record_layer_stats",
        3,
        "(0, 0.0, 0.0)",
      ],
    ]);
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: zeroed, loaded: true })
    );
    useGraphStore.setState({ traceSessionId: "s1", tracePlayhead: 1 });
    render(<NetworkViewPanel />);
    expandPanel();
    const bundleStrokes = recorder.strokes.filter((s) => s.segments === 8);
    expect(bundleStrokes).toHaveLength(1);
    expect(bundleStrokes[0]?.alpha).toBe(0.15); // BASE_ALPHA_MIN, not NaN
    expect(Number.isNaN(bundleStrokes[0]?.alpha ?? Number.NaN)).toBe(false);
  });
});

/**
 * ─── KNOWN DEFECT ─────────────────────────────────────────────────────────
 * The panel-level face of layerActivity.ts's "idle is keyed on a return
 * event" defect — see the KNOWN DEFECTS block in layerActivity.test.ts.
 */
describe("KNOWN DEFECT — the phase chip at an epoch boundary", () => {
  it.fails("reads idle where a loss-curve click lands", () => {
    // Measured on the real packages/nn/run-a.jsonl: 60 of 60 epoch markers
    // report "loss" — the loss call inside evaluate(), which returned three
    // events earlier. The chip is wrong at every point a user can click to.
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: toEvents(EPOCH_BOUNDARY_ROWS), loaded: true })
    );
    useGraphStore.setState({ traceSessionId: "s1", tracePlayhead: 11 });
    render(<NetworkViewPanel />);
    expandPanel();
    expect(screen.getByText("idle")).toBeInTheDocument(); // reads "loss"
  });
});
