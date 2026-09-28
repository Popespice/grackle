import type { Graph, TraceEvent } from "@grackle/shared-types";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
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
import { frameColor } from "../graph/flameLayout";
import { useGraphStore } from "../graph/useGraphStore";
import {
  type CanvasRecorder,
  makeRecorder,
  recordingGetContext,
} from "../test/canvasRecorder";
import { restoreInitialState } from "../test/storeReset";
import { FlameGraphPanel } from "./FlameGraphPanel";

// jsdom reports clientWidth 0; give the container a width so layoutFlame yields
// clickable rectangles. getContext is stubbed to null (jsdom otherwise logs a
// noisy "not implemented" error) so the draw effect no-ops for the
// data/controls tests; the "what actually gets painted" block below swaps in
// a recording context to execute the real paint path (campaign T11-2).
// Restored in afterAll — these patch shared jsdom prototypes (LossCurvePanel
// precedent).
let originalClientWidth: PropertyDescriptor | undefined;
let originalGetContext: typeof HTMLCanvasElement.prototype.getContext;

beforeAll(() => {
  originalClientWidth = Object.getOwnPropertyDescriptor(
    HTMLElement.prototype,
    "clientWidth"
  );
  originalGetContext = HTMLCanvasElement.prototype.getContext;
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get: () => 800,
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
  HTMLCanvasElement.prototype.getContext = originalGetContext;
});

afterEach(cleanup);

function ev(
  event: string,
  node_id: string,
  frame_depth: number,
  ts_ns: number
): TraceEvent {
  return { event, node_id, ts_ns, thread_id: 1, frame_depth };
}

// f [0..100] { g [20..60] }
const EVENTS: TraceEvent[] = [
  ev("call", "a.py:f", 0, 0),
  ev("call", "a.py:g", 1, 20),
  ev("return", "a.py:g", 1, 60),
  ev("return", "a.py:f", 0, 100),
];

// Static graph containing the traced nodes (click-to-focus only selects nodes
// that exist in the graph).
const MOCK_GRAPH = {
  version: "1",
  language: "python",
  nodes: [
    { id: "a.py:f", kind: "function", name: "f", path: "a.py" },
    { id: "a.py:g", kind: "function", name: "g", path: "a.py" },
  ],
  edges: [],
} as unknown as Graph;

// Full-replace first (campaign T11-4): a partial merge only resets the fields
// it names, so anything else a previous test merged in — an action stub
// included — would leak into every later test.
function resetStore(overrides: Record<string, unknown> = {}): void {
  restoreInitialState(useGraphStore);
  useGraphStore.setState({
    graph: null,
    selectedNodeId: null,
    highlightedNodeIds: null,
    traceEvents: [],
    traceSessionId: null,
    traceSessionComplete: false,
    tracePlayhead: 0,
    tracePlaying: false,
    traceSeekable: false,
    traceTotal: 0,
    traceWindowStart: 0,
    ...overrides,
  });
}

beforeEach(() => resetStore());

describe("FlameGraphPanel", () => {
  it("renders null when no trace session is active", () => {
    const { container } = render(<FlameGraphPanel />);
    expect(container.firstChild).toBeNull();
  });

  it("renders the flame graph region and frame count for an active session", () => {
    resetStore({ traceSessionId: "s1", traceEvents: EVENTS });
    render(<FlameGraphPanel />);
    expect(
      screen.getByRole("region", { name: "Flame graph" })
    ).toBeInTheDocument();
    expect(screen.getByText(/2 frames/)).toBeInTheDocument();
    expect(screen.getByLabelText("Flame graph canvas")).toBeInTheDocument();
  });

  it("shows an empty state when there are no call events", () => {
    resetStore({ traceSessionId: "s1", traceEvents: [] });
    render(<FlameGraphPanel />);
    expect(
      screen.getByText("No call events in this session yet.")
    ).toBeInTheDocument();
  });

  it("selects the clicked frame's node (click-to-focus) and clears highlights", () => {
    resetStore({
      traceSessionId: "s1",
      traceEvents: EVENTS,
      graph: MOCK_GRAPH,
      highlightedNodeIds: new Set(["x"]),
    });
    render(<FlameGraphPanel />);
    const canvas = screen.getByLabelText("Flame graph canvas");
    // Row 0 spans the full width → the root frame f.
    fireEvent.click(canvas, { clientX: 10, clientY: 4 });
    expect(useGraphStore.getState().selectedNodeId).toBe("a.py:f");
    expect(useGraphStore.getState().highlightedNodeIds).toBeNull();
  });

  it("does NOT select when the clicked frame is not a static-graph node", () => {
    // Graph lacks "a.py:f" → clicking it must not dim the whole Sigma view.
    const partialGraph = {
      ...MOCK_GRAPH,
      nodes: [
        { id: "a.py:other", kind: "function", name: "other", path: "a.py" },
      ],
    } as unknown as Graph;
    resetStore({
      traceSessionId: "s1",
      traceEvents: EVENTS,
      graph: partialGraph,
    });
    render(<FlameGraphPanel />);
    fireEvent.click(screen.getByLabelText("Flame graph canvas"), {
      clientX: 10,
      clientY: 4,
    });
    expect(useGraphStore.getState().selectedNodeId).toBeNull();
  });

  it("flags an approximate reconstruction when frames close implicitly", () => {
    // A call with no matching return → synthetic close at stream end.
    resetStore({
      traceSessionId: "s1",
      traceEvents: [ev("call", "a.py:f", 0, 0)],
    });
    render(<FlameGraphPanel />);
    expect(screen.getByText("~approx")).toBeInTheDocument();
  });

  it("offers a 'Load full trace' control only for a windowed seekable session", () => {
    resetStore({
      traceSessionId: "s1",
      traceEvents: EVENTS, // 4 loaded
      traceSeekable: true,
      traceTotal: 1000, // far more on the server
    });
    render(<FlameGraphPanel />);
    expect(
      screen.getByRole("button", { name: /Load full trace \(1000\)/ })
    ).toBeInTheDocument();
  });

  it("measures width and becomes clickable when a session starts AFTER mount", () => {
    // Regression: the panel first mounts with no session (returns null, the
    // container is never in the DOM). A useRef + []-deps measure effect would
    // latch a null ref and leave width 0 forever; the callback ref re-measures
    // when the container finally mounts.
    const { container } = render(<FlameGraphPanel />);
    expect(container.firstChild).toBeNull();
    act(() => {
      useGraphStore.setState({
        traceSessionId: "s1",
        traceEvents: EVENTS,
        graph: MOCK_GRAPH,
      });
    });
    const canvas = screen.getByLabelText("Flame graph canvas");
    fireEvent.click(canvas, { clientX: 10, clientY: 4 });
    expect(useGraphStore.getState().selectedNodeId).toBe("a.py:f");
  });

  it("exposes export and import controls", () => {
    resetStore({
      traceSessionId: "s1",
      traceEvents: EVENTS,
      traceSessionComplete: true,
    });
    render(<FlameGraphPanel />);
    expect(
      screen.getByRole("button", { name: /speedscope/ })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Chrome trace/ })
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Import trace file")).toBeInTheDocument();
  });

  it("disables export while a seekable session is still windowed", () => {
    resetStore({
      traceSessionId: "s1",
      traceEvents: EVENTS,
      traceSeekable: true,
      traceTotal: 1000, // window << total → partial
    });
    render(<FlameGraphPanel />);
    expect(screen.getByRole("button", { name: /speedscope/ })).toBeDisabled();
  });

  it("disables Import while a live session is still streaming", () => {
    resetStore({
      traceSessionId: "s1",
      traceEvents: EVENTS,
      traceSessionComplete: false, // live, not finished
      traceSeekable: false,
    });
    render(<FlameGraphPanel />);
    expect(screen.getByRole("button", { name: /Import/ })).toBeDisabled();
  });
});

describe("FlameGraphPanel — what actually gets painted", () => {
  // Literal colours from FlameGraphPanel's paint effect.
  const HOT_OUTLINE = "#fff3c4";
  const SELECTED_OUTLINE = "#b794f6";
  const LABEL_INK = "#1a1208";

  let recorder: CanvasRecorder;
  let originalDpr: number;

  beforeEach(() => {
    recorder = makeRecorder();
    HTMLCanvasElement.prototype.getContext = recordingGetContext(recorder);
    originalDpr = window.devicePixelRatio;
  });

  afterEach(() => {
    HTMLCanvasElement.prototype.getContext = vi.fn(() => null);
    Object.defineProperty(window, "devicePixelRatio", {
      configurable: true,
      value: originalDpr,
    });
  });

  function paint(
    events: TraceEvent[],
    overrides: Record<string, unknown> = {}
  ): void {
    resetStore({ traceSessionId: "s1", traceEvents: events, ...overrides });
    render(<FlameGraphPanel />);
  }

  const bars = () =>
    recorder.fillRects.map(({ x, y, w, h, style }) => ({ x, y, w, h, style }));

  it("paints one bar per frame, width-proportional to total time, with a 1px gutter", () => {
    // f spans 100ns across the 800px container (8 px/ns); g's 40ns is 320px
    // on the row below, starting at its parent's left edge.
    paint(EVENTS);
    expect(bars()).toEqual([
      { x: 0, y: 0, w: 799, h: 19, style: frameColor("a.py:f") },
      { x: 0, y: 20, w: 319, h: 19, style: frameColor("a.py:g") },
    ]);
  });

  it("labels each bar at its left inset, vertically centred, clipped to the bar", () => {
    paint(EVENTS);
    expect(
      recorder.labels.map(({ text, x, y, maxWidth, fill, baseline }) => ({
        text,
        x,
        y,
        maxWidth,
        fill,
        baseline,
      }))
    ).toEqual([
      {
        text: "f",
        x: 3,
        y: 10,
        maxWidth: 794,
        fill: LABEL_INK,
        baseline: "middle",
      },
      {
        text: "g",
        x: 3,
        y: 30,
        maxWidth: 314,
        fill: LABEL_INK,
        baseline: "middle",
      },
    ]);
    // A literal font stack: the 2D context cannot resolve a CSS var().
    expect(recorder.labels[0]?.font).toMatch(/^11px ui-monospace/);
  });

  it("paints a too-narrow frame's bar but not its label", () => {
    // g lasts 3ns of f's 100 → 24px, under the 32px label threshold.
    paint([
      ev("call", "a.py:f", 0, 0),
      ev("call", "a.py:g", 1, 10),
      ev("return", "a.py:g", 1, 13),
      ev("return", "a.py:f", 0, 100),
    ]);
    expect(bars().map((b) => [b.y, b.w])).toEqual([
      [0, 799],
      [20, 23],
    ]);
    expect(recorder.texts).toEqual(["f"]);
  });

  it("outlines the hot path, and the selected frame in the selection colour instead", () => {
    // f -> g is the only chain, so both are on the hot path; selecting g must
    // replace its hot outline, not add a second one.
    paint(EVENTS, { selectedNodeId: "a.py:g" });
    expect(
      recorder.strokeRects.map(({ x, y, w, h, style, lineWidth }) => ({
        x,
        y,
        w,
        h,
        style,
        lineWidth,
      }))
    ).toEqual([
      { x: 0.5, y: 0.5, w: 798.5, h: 18.5, style: HOT_OUTLINE, lineWidth: 1.5 },
      {
        x: 0.5,
        y: 20.5,
        w: 318.5,
        h: 18.5,
        style: SELECTED_OUTLINE,
        lineWidth: 2,
      },
    ]);
  });

  it("dims, and leaves un-outlined, every frame off the hot path", () => {
    // f { g 40ns, h 10ns }: the hot path is f -> g, so h alone is dimmed.
    paint([
      ev("call", "a.py:f", 0, 0),
      ev("call", "a.py:g", 1, 10),
      ev("return", "a.py:g", 1, 50),
      ev("call", "a.py:h", 1, 60),
      ev("return", "a.py:h", 1, 70),
      ev("return", "a.py:f", 0, 100),
    ]);
    // Pin that the dimmed fill really differs from the lit one, or the
    // assertion below could not tell a dimmed bar from a lit one.
    expect(frameColor("a.py:h", true)).not.toBe(frameColor("a.py:h"));
    expect(bars().map((b) => [b.x, b.y, b.style])).toEqual([
      [0, 0, frameColor("a.py:f")],
      [0, 20, frameColor("a.py:g")],
      [320, 20, frameColor("a.py:h", true)],
    ]);
    expect(recorder.strokeRects.map((r) => [r.x, r.y])).toEqual([
      [0.5, 0.5],
      [0.5, 20.5],
    ]);
  });

  it("scales the backing store by devicePixelRatio and matches the transform", () => {
    // A backing store at CSS size on a retina display is the classic blurry
    // canvas; a transform that disagrees with it draws at the wrong scale.
    Object.defineProperty(window, "devicePixelRatio", {
      configurable: true,
      value: 2,
    });
    paint(EVENTS); // two rows (depth 0..1) → a 40 CSS-px tall canvas
    const canvas = screen.getByLabelText(
      "Flame graph canvas"
    ) as HTMLCanvasElement;
    expect(canvas.width).toBe(1600); // 800 css px * 2
    expect(canvas.height).toBe(80); // 40 css px * 2
    expect(recorder.transforms.at(-1)).toEqual([2, 0, 0, 2, 0, 0]);
    // Cleared in CSS pixels — the transform already scales them.
    expect(recorder.clears.at(-1)).toEqual([0, 0, 800, 40]);
  });

  it("never asks for a context when there is nothing to paint", () => {
    paint([]);
    expect(
      screen.getByText("No call events in this session yet.")
    ).toBeInTheDocument();
    expect(HTMLCanvasElement.prototype.getContext).not.toHaveBeenCalled();
    expect(recorder.fillRects).toEqual([]);
  });
});
