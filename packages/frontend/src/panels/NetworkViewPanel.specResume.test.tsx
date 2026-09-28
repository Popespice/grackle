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
import { restoreInitialState } from "../test/storeReset";
import { NetworkViewPanel } from "./NetworkViewPanel";

/**
 * Campaign T8-6 (`docs/test-campaigns/phase-12.md`): the architecture latch in
 * `NetworkViewPanel` resumes `extractNetworkSpec` over each appended tail, and
 * that resume does not agree with a one-shot scan of the same events.
 *
 * `extractNetworkSpec` deliberately does NOT look past a `record_architecture`
 * beacon that parsed but is incoherent (no param-carrying layer, or a linear
 * chain that does not connect): it returns `null` for the whole trace
 * (networkSpec.test.ts pins this). The panel cannot tell that `null` from
 * "no beacon yet", so when the incoherent beacon and a later coherent one land
 * in different batches, the live panel resumes past the incoherent one and
 * renders the later net, while a replay of the very same trace (one array)
 * renders "No network beacons". Found by the resume-consistency sweep in
 * `graph/beaconGrammar.sweep.test.ts`; minimized here to two events.
 */

vi.mock("../graph/useFullTrace");
const mockUseFullTrace = vi.mocked(useFullTrace);

function fullTrace(events: TraceEvent[]): UseFullTraceResult {
  return {
    events,
    truncated: false,
    loading: false,
    error: false,
    loaded: true,
    load: vi.fn(),
  };
}

function archReturn(ret: string, ts_ns: number): TraceEvent {
  return {
    event: "return",
    node_id: "grackle_nn/metrics.py:record_architecture",
    ts_ns,
    thread_id: 1,
    frame_depth: 3,
    values: { ret },
  };
}

// Parses (two linear tokens) but does not chain: 3 !== 4.
const INCOHERENT = archReturn("'linear:2:3 linear:4:5'", 0);
const COHERENT = archReturn("'linear:1:1'", 1);
const DEGRADE = "No network beacons (record_architecture) in this trace.";

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

afterEach(cleanup);

beforeEach(() => {
  restoreInitialState(useGraphStore);
  useGraphStore.setState({
    traceSessionId: "s1",
    traceSeekable: false,
    traceTotal: 0,
    tracePlayhead: 0,
  });
});

describe("NetworkViewPanel — architecture latch vs a one-shot scan (T8-6)", () => {
  it("a replay of [incoherent, coherent] renders the degrade message", () => {
    // The reference behavior: one array, one scan, the incoherent beacon is
    // fatal. This half holds today and is what the live path must match.
    mockUseFullTrace.mockReturnValue(fullTrace([INCOHERENT, COHERENT]));
    render(<NetworkViewPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Open network view" }));
    expect(screen.getByText(DEGRADE)).toBeInTheDocument();
    expect(screen.queryByText(/model: 1-1/)).toBeNull();
  });

  it("a lone incoherent beacon renders the degrade message (control for the ledgered test below)", () => {
    // The ledgered test starts from exactly this render, so pinning it here
    // means that test can only fail at its final assertions, never because
    // the first half stopped rendering the degrade message.
    mockUseFullTrace.mockReturnValue(fullTrace([INCOHERENT]));
    render(<NetworkViewPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Open network view" }));
    expect(screen.getByText(DEGRADE)).toBeInTheDocument();
    expect(screen.queryByText(/model: /)).toBeNull();
  });

  // KNOWN DEFECT (T8-6, docs/test-campaigns/phase-12.md): the live path
  // resumes past the fatal beacon — `cache.spec ?? extractNetworkSpec(events,
  // cache.scanned)` — and renders "model: 1-1" for a trace whose replay
  // renders nothing. Remove `.fails` in the PR that makes the two agree.
  it.fails("the same trace streamed in two batches renders what the replay renders", () => {
    mockUseFullTrace.mockReturnValue(fullTrace([INCOHERENT]));
    const { rerender } = render(<NetworkViewPanel />);
    fireEvent.click(screen.getByRole("button", { name: "Open network view" }));
    expect(screen.getByText(DEGRADE)).toBeInTheDocument();

    // A pure append: same first event object, one more event after it.
    mockUseFullTrace.mockReturnValue(fullTrace([INCOHERENT, COHERENT]));
    rerender(<NetworkViewPanel />);
    expect(screen.queryByText(/model: 1-1/)).toBeNull();
    expect(screen.getByText(DEGRADE)).toBeInTheDocument();
  });
});
