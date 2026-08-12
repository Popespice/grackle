import type { TraceEvent } from "@grackle/shared-types";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import {
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
import { LossCurvePanel } from "./LossCurvePanel";

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

// jsdom reports clientWidth 0; give the container a fixed width so
// layoutLossCurve yields a real, clickable layout. getContext is stubbed to
// null (FlameGraphPanel precedent) so the paint effect no-ops — we test the
// data/controls/interaction layer, not canvas pixels.
beforeAll(() => {
  Object.defineProperty(HTMLElement.prototype, "clientWidth", {
    configurable: true,
    get: () => 800,
  });
  HTMLCanvasElement.prototype.getContext = vi.fn(() => null);
});

function unrelated(id: string, tsNs: number): TraceEvent {
  return {
    event: "call",
    node_id: id,
    ts_ns: tsNs,
    thread_id: 1,
    frame_depth: 0,
  };
}

function epochRet(epoch: number, loss: number, accuracy: number): TraceEvent {
  return {
    event: "return",
    node_id: "grackle_nn/metrics.py:record_epoch",
    ts_ns: epoch,
    thread_id: 1,
    frame_depth: 0,
    values: { ret: `(${epoch}, ${loss}, ${accuracy})` },
  };
}

// Three epochs at absolute indices 1, 3, 5 (interleaved with unrelated
// events), width=800/PADDING_LEFT=40/PADDING_RIGHT=40 places epoch 0 at
// x=40, epoch 1 at x=400, epoch 2 at x=760 — used by the click-to-seek test.
const THREE_EPOCH_EVENTS: TraceEvent[] = [
  unrelated("a.py:f", 0),
  epochRet(0, 1.0, 0.5),
  unrelated("a.py:f", 2),
  epochRet(1, 0.6, 0.7),
  unrelated("a.py:f", 4),
  epochRet(2, 0.2, 0.9),
];

// Full reset to the pristine (pre-any-test-mutation) store snapshot —
// CausalPathPanel's precedent. Automatically covers every field including
// predictedOverlay, so no manually-enumerated field list can drift stale.
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

describe("LossCurvePanel", () => {
  it("renders nothing without a trace session", () => {
    const { container } = render(<LossCurvePanel />);
    expect(container.firstChild).toBeNull();
  });

  it("shows a placeholder when fewer than 2 epoch points are present (buffered)", () => {
    mockUseFullTrace.mockReturnValue(fullTrace({ events: [], loaded: true }));
    useGraphStore.setState({ traceSessionId: "s1" });
    render(<LossCurvePanel />);
    expect(
      screen.getByText("No learning-loop (record_epoch) events in this trace.")
    ).toBeInTheDocument();
  });

  it("shows a placeholder for a single record_epoch event (still < 2 points)", () => {
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: [epochRet(0, 1.0, 0.5)], loaded: true })
    );
    useGraphStore.setState({ traceSessionId: "s1" });
    render(<LossCurvePanel />);
    expect(
      screen.getByText("No learning-loop (record_epoch) events in this trace.")
    ).toBeInTheDocument();
  });

  it("renders the loss curve for a buffered session with >=2 epoch points", () => {
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: THREE_EPOCH_EVENTS, loaded: true })
    );
    useGraphStore.setState({ traceSessionId: "s1" });
    render(<LossCurvePanel />);
    expect(screen.getByText("3 epochs")).toBeInTheDocument();
    expect(screen.getByLabelText("Loss curve canvas")).toBeInTheDocument();
  });

  it("does not require a click in buffered mode (no Load button)", () => {
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: THREE_EPOCH_EVENTS, loaded: true })
    );
    useGraphStore.setState({ traceSessionId: "s1" });
    render(<LossCurvePanel />);
    expect(screen.queryByText("Load loss curve")).toBeNull();
  });

  it("shows a Load button when seekable and unloaded", () => {
    mockUseFullTrace.mockReturnValue(fullTrace({ loaded: false }));
    useGraphStore.setState({ traceSessionId: "s1", traceSeekable: true });
    render(<LossCurvePanel />);
    expect(
      screen.getByRole("button", { name: "Load loss curve" })
    ).toBeInTheDocument();
  });

  it("clicking Load calls full.load", () => {
    const load = vi.fn();
    mockUseFullTrace.mockReturnValue(fullTrace({ loaded: false, load }));
    useGraphStore.setState({ traceSessionId: "s1", traceSeekable: true });
    render(<LossCurvePanel />);
    fireEvent.click(screen.getByRole("button", { name: "Load loss curve" }));
    expect(load).toHaveBeenCalled();
  });

  it("shows a loading message while the prefix is loading", () => {
    mockUseFullTrace.mockReturnValue(fullTrace({ loading: true }));
    useGraphStore.setState({ traceSessionId: "s1", traceSeekable: true });
    render(<LossCurvePanel />);
    expect(screen.getByText("Loading loss curve…")).toBeInTheDocument();
  });

  it("shows a retry button on error", () => {
    const load = vi.fn();
    mockUseFullTrace.mockReturnValue(fullTrace({ error: true, load }));
    useGraphStore.setState({ traceSessionId: "s1", traceSeekable: true });
    render(<LossCurvePanel />);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(load).toHaveBeenCalled();
  });

  it("shows the truncated banner only when seekable and truncated", () => {
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: THREE_EPOCH_EVENTS, loaded: true, truncated: true })
    );
    useGraphStore.setState({ traceSessionId: "s1", traceSeekable: true });
    render(<LossCurvePanel />);
    expect(
      screen.getByText(/Curve covers the first .* events only\./)
    ).toBeInTheDocument();
  });

  it("does not show the truncated banner in buffered mode even if the flag were set", () => {
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: THREE_EPOCH_EVENTS, loaded: true, truncated: true })
    );
    useGraphStore.setState({ traceSessionId: "s1", traceSeekable: false });
    render(<LossCurvePanel />);
    expect(screen.queryByText(/events only\./)).toBeNull();
  });

  it("shows the multi-run banner when more than one run is detected", () => {
    const events = [
      epochRet(0, 1.0, 0.5),
      epochRet(1, 0.6, 0.7),
      // Restart: epoch 0 <= previous epoch (1) starts a new run.
      epochRet(0, 0.9, 0.4),
      epochRet(1, 0.5, 0.6),
    ];
    mockUseFullTrace.mockReturnValue(fullTrace({ events, loaded: true }));
    useGraphStore.setState({ traceSessionId: "s1" });
    render(<LossCurvePanel />);
    expect(screen.getByText("showing latest of 2 runs")).toBeInTheDocument();
  });

  it("does not show the multi-run banner for a single run", () => {
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: THREE_EPOCH_EVENTS, loaded: true })
    );
    useGraphStore.setState({ traceSessionId: "s1" });
    render(<LossCurvePanel />);
    expect(screen.queryByText(/showing latest of/)).toBeNull();
  });

  it("click-to-seek sets the playhead to the clicked point's own eventIndex (no +1)", () => {
    mockUseFullTrace.mockReturnValue(
      fullTrace({ events: THREE_EPOCH_EVENTS, loaded: true })
    );
    // setPlayhead clamps to the store's traceEvents.length in buffered mode
    // (useGraphStore.ts:236-248) — real useFullTrace returns this SAME array
    // by reference in buffered mode (useFullTrace.ts:129-137), so keep the
    // mock and the store in sync here rather than leaving traceEvents at its
    // default empty array, which would clamp the playhead to 0.
    useGraphStore.setState({
      traceSessionId: "s1",
      traceEvents: THREE_EPOCH_EVENTS,
    });
    render(<LossCurvePanel />);

    const canvas = screen.getByLabelText("Loss curve canvas");
    // Epoch 1 sits at x=400 (width=800, PADDING_LEFT/RIGHT=40, epochs 0..2).
    fireEvent.click(canvas, { clientX: 400, clientY: 50 });

    // The epoch-1 return event is THREE_EPOCH_EVENTS[3].
    expect(useGraphStore.getState().tracePlayhead).toBe(3);
  });
});
