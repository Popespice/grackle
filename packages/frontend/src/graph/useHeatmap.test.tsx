/**
 * useHeatmap — the heat the graph canvas paints (campaign T11-6,
 * `docs/test-campaigns/phase-12.md`). It had no test of its own: `computeHeat`
 * is tested in `heatmap.test.ts`, but not which inputs this wrapper feeds it,
 * nor the ADR-0018 branch that substitutes the agent's cumulative heat.
 *
 * The last block probes that branch in context: it mounts `TimelinePanel`
 * (which owns both the playback loop and the debounced agent re-query) next
 * to a `useHeatmap` consumer, with the client's two request actions stubbed.
 */
import type {
  TraceEvent,
  TraceQueryResponse,
  TraceWindowMessage,
} from "@grackle/shared-types";
import { act, cleanup, render, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { TimelinePanel } from "../panels/TimelinePanel";
import { restoreInitialState } from "../test/storeReset";
import { useGrackleClient } from "../ws/client";
import { useGraphStore } from "./useGraphStore";
import { useHeatmap } from "./useHeatmap";

function ev(node: string, event = "call"): TraceEvent {
  return { event, node_id: node, ts_ns: 0, thread_id: 1, frame_depth: 0 };
}

// a a b a c — call-only unless stated.
const EVENTS: TraceEvent[] = [
  ev("m.py:a"),
  ev("m.py:a"),
  ev("m.py:b"),
  ev("m.py:a", "return"),
  ev("m.py:c"),
];

function heatOf(result: { heat: Map<string, number> }) {
  return Object.fromEntries([...result.heat].sort());
}

beforeEach(() => {
  restoreInitialState(useGraphStore);
  restoreInitialState(useGrackleClient);
});

afterEach(() => {
  cleanup();
  vi.useRealTimers();
});

describe("useHeatmap — local computation", () => {
  it("counts events before the playhead (cumulative)", () => {
    useGraphStore.setState({ traceEvents: EVENTS, tracePlayhead: 3 });
    const { result } = renderHook(() => useHeatmap());
    expect(heatOf(result.current)).toEqual({ "m.py:a": 2, "m.py:b": 1 });
    expect(result.current.maxHeat).toBe(2);
  });

  it("applies the event-type filter", () => {
    useGraphStore.setState({
      traceEvents: EVENTS,
      tracePlayhead: 5,
      traceEventTypeFilter: new Set(["return"]),
    });
    const { result } = renderHook(() => useHeatmap());
    expect(heatOf(result.current)).toEqual({ "m.py:a": 1 });
  });

  it("uses the sliding window when the heat mode is sliding", () => {
    useGraphStore.setState({
      traceEvents: EVENTS,
      tracePlayhead: 5,
      traceHeatMode: "sliding",
      traceWindowSize: 2,
    });
    const { result } = renderHook(() => useHeatmap());
    expect(heatOf(result.current)).toEqual({ "m.py:a": 1, "m.py:c": 1 });
  });

  it("in seekable mode reads the absolute playhead against the window start", () => {
    // The store holds the window [40, 45); playhead 43 is window offset 3.
    useGraphStore.setState({
      traceSeekable: true,
      traceHeatMode: "sliding", // keeps the agent branch out of it
      traceWindowSize: 100,
      traceEvents: EVENTS,
      traceWindowStart: 40,
      traceTotal: 100,
      tracePlayhead: 43,
    });
    const { result } = renderHook(() => useHeatmap());
    expect(heatOf(result.current)).toEqual({ "m.py:a": 2, "m.py:b": 1 });
  });

  it("recomputes when the playhead moves, and not on an unrelated store change", () => {
    useGraphStore.setState({ traceEvents: EVENTS, tracePlayhead: 1 });
    const { result } = renderHook(() => useHeatmap());
    const first = result.current;

    act(() => {
      useGraphStore.getState().selectNode("m.py:a");
    });
    expect(result.current).toBe(first);

    act(() => {
      useGraphStore.setState({ tracePlayhead: 2 });
    });
    expect(result.current).not.toBe(first);
    expect(heatOf(result.current)).toEqual({ "m.py:a": 2 });
  });
});

describe("useHeatmap — agent cumulative heat (ADR-0018)", () => {
  const AGENT = { "m.py:a": 40, "m.py:z": 90, "m.py:b": 7 };

  it("uses the agent's counts in seekable + cumulative mode", () => {
    useGraphStore.setState({
      traceSeekable: true,
      traceEvents: EVENTS,
      tracePlayhead: 3,
      agentHeat: AGENT,
    });
    const { result } = renderHook(() => useHeatmap());
    expect(heatOf(result.current)).toEqual(AGENT);
    expect(result.current.maxHeat).toBe(90);
  });

  it("reports zero max heat for an empty agent result", () => {
    useGraphStore.setState({
      traceSeekable: true,
      traceEvents: EVENTS,
      tracePlayhead: 3,
      agentHeat: {},
    });
    const { result } = renderHook(() => useHeatmap());
    expect(result.current.heat.size).toBe(0);
    expect(result.current.maxHeat).toBe(0);
  });

  it("ignores the agent's counts in sliding mode (they are cumulative)", () => {
    useGraphStore.setState({
      traceSeekable: true,
      traceHeatMode: "sliding",
      traceWindowSize: 100,
      traceEvents: EVENTS,
      traceTotal: 100,
      tracePlayhead: 3,
      agentHeat: AGENT,
    });
    const { result } = renderHook(() => useHeatmap());
    expect(heatOf(result.current)).toEqual({ "m.py:a": 2, "m.py:b": 1 });
  });

  it("ignores the agent's counts outside seekable mode", () => {
    useGraphStore.setState({
      traceEvents: EVENTS,
      tracePlayhead: 3,
      agentHeat: AGENT,
    });
    const { result } = renderHook(() => useHeatmap());
    expect(heatOf(result.current)).toEqual({ "m.py:a": 2, "m.py:b": 1 });
  });

  it("falls back to the window when no agent heat has arrived yet", () => {
    useGraphStore.setState({
      traceSeekable: true,
      traceEvents: EVENTS,
      traceTotal: 100,
      tracePlayhead: 3,
    });
    const { result } = renderHook(() => useHeatmap());
    expect(heatOf(result.current)).toEqual({ "m.py:a": 2, "m.py:b": 1 });
  });

  it("an event-type filter that admits nothing present gives zero heat — buffered mode (control)", () => {
    useGraphStore.setState({
      traceEvents: EVENTS,
      tracePlayhead: 5,
      traceEventTypeFilter: new Set(["exception"]),
      agentHeat: AGENT, // not consulted outside seekable mode
    });
    const { result } = renderHook(() => useHeatmap());
    expect(result.current.maxHeat).toBe(0);
  });

  // KNOWN DEFECT (T11-6, docs/test-campaigns/phase-12.md): the agent branch
  // never looks at `traceEventTypeFilter`, and the `cumulative_heat` query
  // carries no filter, so in seekable + cumulative mode the event-type chips
  // in the timeline change nothing on the graph. Here the filter admits only
  // `exception` events — none exist — yet the canvas is painted with every
  // call. Remove `.fails` in the PR that makes the filter apply here too.
  it.fails("an event-type filter that admits nothing present gives zero heat — seekable mode", () => {
    useGraphStore.setState({
      traceSeekable: true,
      traceEvents: EVENTS,
      traceTotal: 100,
      tracePlayhead: 5,
      traceEventTypeFilter: new Set(["exception"]),
      agentHeat: AGENT,
    });
    const { result } = renderHook(() => useHeatmap());
    expect(result.current.maxHeat).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// In context: TimelinePanel's playback + debounced agent re-query
// ---------------------------------------------------------------------------

const TOTAL = 1000;

function windowEvents(start: number, count: number): TraceEvent[] {
  return Array.from({ length: count }, (_, i) => ev(`m.py:w${start + i}`));
}

/**
 * Stub the client: a seek returns the requested window of a 1000-event trace;
 * a cumulative_heat query answers `{ "at:<index>": 1 }`, so the heat on screen
 * names the playhead it was computed for.
 */
function stubSeekableClient() {
  const requestTraceWindow = vi.fn(
    (sessionId: string, start: number, count: number) =>
      Promise.resolve<TraceWindowMessage>({
        id: "w",
        type: "trace_window",
        payload: {
          session_id: sessionId,
          start_index: start,
          events: windowEvents(start, count),
          total: TOTAL,
        },
      })
  );
  const requestTraceQuery = vi.fn(
    (sessionId: string, kind: string, atIndex: number) =>
      Promise.resolve<TraceQueryResponse>({
        id: "q",
        type: "trace_query_response",
        payload: {
          session_id: sessionId,
          kind,
          at_index: atIndex,
          data: { [`at:${atIndex}`]: 1 },
        },
      })
  );
  useGrackleClient.setState({ requestTraceWindow, requestTraceQuery });
  return { requestTraceQuery };
}

let painted: { heat: Map<string, number> } | null = null;

function HeatConsumer(): null {
  painted = useHeatmap();
  return null;
}

function paintedKeys(): string[] {
  return painted ? [...painted.heat.keys()] : [];
}

async function advance(ms: number): Promise<void> {
  await act(() => vi.advanceTimersByTimeAsync(ms));
}

/** A seekable session, loaded and settled: agent heat computed at playhead 0. */
async function settledSeekableSession() {
  vi.useFakeTimers();
  painted = null;
  const client = stubSeekableClient();
  act(() => {
    useGraphStore.getState().startTraceSession("s1", true);
  });
  render(
    <>
      <TimelinePanel />
      <HeatConsumer />
    </>
  );
  await advance(200); // initial window + the 150 ms debounced heat query
  return client;
}

describe("useHeatmap in context — seekable playback", () => {
  it("shows the agent heat for the settled playhead (control)", async () => {
    await settledSeekableSession();
    expect(useGraphStore.getState().traceTotal).toBe(TOTAL);
    expect(paintedKeys()).toEqual(["at:0"]);
  });

  it("catches up to the playhead once playback pauses (control)", async () => {
    await settledSeekableSession();
    act(() => {
      useGraphStore.getState().play();
    });
    await advance(1000);
    act(() => {
      useGraphStore.getState().pause();
    });
    await advance(200);

    const at = useGraphStore.getState().tracePlayhead;
    expect(at).toBeGreaterThan(50);
    expect(paintedKeys()).toEqual([`at:${at}`]);
  });

  // KNOWN DEFECT (T11-6, docs/test-campaigns/phase-12.md): in seekable +
  // cumulative mode the canvas paints `agentHeat` whenever it is non-null,
  // whatever playhead it was computed for, and TimelinePanel re-queries it
  // only after the playhead has been still for 150 ms. Playback moves the
  // playhead every frame, so the debounce never fires while playing: the heat
  // map freezes at the pre-play position for the whole run and jumps when it
  // stops. (PredictedHeatPanel's header assumes the re-query happens "on every
  // playhead move".) Remove `.fails` in the PR that keeps playback heat live.
  it.fails("the painted heat moves on from the pre-play position during playback", async () => {
    await settledSeekableSession();
    act(() => {
      useGraphStore.getState().play();
    });
    await advance(1000);

    expect(useGraphStore.getState().tracePlaying).toBe(true);
    expect(useGraphStore.getState().tracePlayhead).toBeGreaterThan(50);
    expect(paintedKeys()).not.toEqual(["at:0"]);
  });
});
