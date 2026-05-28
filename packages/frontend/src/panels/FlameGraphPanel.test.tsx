import type { TraceEvent } from "@grackle/shared-types";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import {
  afterEach,
  beforeAll,
  beforeEach,
  describe,
  expect,
  it,
  vi,
} from "vitest";
import { useGraphStore } from "../graph/useGraphStore";
import { FlameGraphPanel } from "./FlameGraphPanel";

// jsdom reports clientWidth 0; give the container a width so layoutFlame yields
// clickable rectangles. getContext is stubbed to null (jsdom otherwise logs a
// noisy "not implemented" error) so the draw effect no-ops — we test the
// data/controls layer, not canvas pixels (same as GraphCanvas, untested).
beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get: () => 800,
  });
  HTMLCanvasElement.prototype.getContext = vi.fn(() => null);
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

function resetStore(overrides: Record<string, unknown> = {}): void {
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
    useGraphStore.setState({ highlightedNodeIds: new Set(["x"]) });
    resetStore({
      traceSessionId: "s1",
      traceEvents: EVENTS,
      highlightedNodeIds: new Set(["x"]),
    });
    render(<FlameGraphPanel />);
    const canvas = screen.getByLabelText("Flame graph canvas");
    // Row 0 spans the full width → the root frame f.
    fireEvent.click(canvas, { clientX: 10, clientY: 4 });
    expect(useGraphStore.getState().selectedNodeId).toBe("a.py:f");
    expect(useGraphStore.getState().highlightedNodeIds).toBeNull();
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
      useGraphStore.setState({ traceSessionId: "s1", traceEvents: EVENTS });
    });
    const canvas = screen.getByLabelText("Flame graph canvas");
    fireEvent.click(canvas, { clientX: 10, clientY: 4 });
    expect(useGraphStore.getState().selectedNodeId).toBe("a.py:f");
  });

  it("exposes export and import controls", () => {
    resetStore({ traceSessionId: "s1", traceEvents: EVENTS });
    render(<FlameGraphPanel />);
    expect(
      screen.getByRole("button", { name: /speedscope/ })
    ).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: /Chrome trace/ })
    ).toBeInTheDocument();
    expect(screen.getByLabelText("Import trace file")).toBeInTheDocument();
  });
});
