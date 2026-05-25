/**
 * Tests for useBufferedTraceEvents — the rAF coalescing buffer for live trace
 * events (Phase 7.1).
 *
 * jsdom provides requestAnimationFrame (backed by setTimeout).  To observe
 * the batching behaviour we must control when callbacks fire.  Every test that
 * needs to inspect the store after an rAF flush does one of:
 *   - Stubs rAF with vi.stubGlobal to capture the callback, then fires it manually.
 *   - Uses vi.useFakeTimers() + vi.runAllTimers() to advance past the scheduled frame.
 *
 * The session_end handler flushes the queue synchronously (it calls
 * cancelAnimationFrame then reads pendingRef directly), so those tests
 * observe the result immediately without rAF control.
 */

import type { TraceEvent, TraceSessionEndMessage } from "@grackle/shared-types";
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useGrackleClient } from "../ws/client";
import { useBufferedTraceEvents } from "./useBufferedTraceEvents";
import { useGraphStore } from "./useGraphStore";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

function mkEv(i: number): TraceEvent {
  return {
    event: "call",
    node_id: `fn_${i}`,
    ts_ns: i,
    thread_id: 1,
    frame_depth: i,
  };
}

function mkSessionEnd(count = 0): TraceSessionEndMessage {
  return {
    id: "end-1",
    type: "trace_session_end",
    payload: { session_id: "s1", ended_ns: 9_000_000, event_count: count },
  };
}

/** Push a trace event directly through all registered handlers. */
function pushTraceEvent(ev: TraceEvent): void {
  for (const h of useGrackleClient.getState()._traceEventHandlers) h(ev);
}

/** Fire session_end directly through all registered handlers. */
function pushSessionEnd(count = 0): void {
  const msg = mkSessionEnd(count);
  for (const h of useGrackleClient.getState()._traceSessionEndHandlers) h(msg);
}

// ---------------------------------------------------------------------------
// Test fixtures
// ---------------------------------------------------------------------------

beforeEach(() => {
  useGrackleClient.setState({
    status: "disconnected",
    lastPong: null,
    _ws: null,
    _staticGraphHandlers: new Set(),
    _pendingReadSource: new Map(),
    _traceSessionStartHandlers: new Set(),
    _traceEventHandlers: new Set(),
    _traceSessionEndHandlers: new Set(),
  });
  useGraphStore.setState({
    graph: null,
    selectedNodeId: null,
    highlightedNodeIds: null,
    hiddenKinds: new Set(),
    searchTerm: "",
    excludeGlobs: [],
    traceEvents: [],
    traceSessionId: "s1",
    traceSessionComplete: false,
    tracePlayhead: 0,
    tracePlaying: false,
    tracePlaybackSpeed: 1,
    traceEventTypeFilter: new Set(),
    traceHeatMode: "cumulative",
    traceWindowSize: 200,
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.useRealTimers();
});

// ---------------------------------------------------------------------------
// Subscription wiring
// ---------------------------------------------------------------------------

describe("useBufferedTraceEvents — subscription wiring", () => {
  it("registers exactly one onTraceEvent handler", () => {
    renderHook(() => useBufferedTraceEvents());
    expect(useGrackleClient.getState()._traceEventHandlers.size).toBe(1);
  });

  it("removes the onTraceEvent handler on unmount", () => {
    const { unmount } = renderHook(() => useBufferedTraceEvents());
    expect(useGrackleClient.getState()._traceEventHandlers.size).toBe(1);
    unmount();
    expect(useGrackleClient.getState()._traceEventHandlers.size).toBe(0);
  });

  it("registers exactly one onTraceSessionEnd handler", () => {
    renderHook(() => useBufferedTraceEvents());
    expect(useGrackleClient.getState()._traceSessionEndHandlers.size).toBe(1);
  });

  it("removes the onTraceSessionEnd handler on unmount", () => {
    const { unmount } = renderHook(() => useBufferedTraceEvents());
    unmount();
    expect(useGrackleClient.getState()._traceSessionEndHandlers.size).toBe(0);
  });
});

// ---------------------------------------------------------------------------
// rAF batching — controlled via vi.stubGlobal
// ---------------------------------------------------------------------------

describe("useBufferedTraceEvents — rAF batching", () => {
  it("N synchronous events schedule exactly one rAF", () => {
    const rafMock = vi.fn(() => 1);
    vi.stubGlobal("requestAnimationFrame", rafMock);
    vi.stubGlobal("cancelAnimationFrame", vi.fn());

    renderHook(() => useBufferedTraceEvents());

    act(() => {
      for (let i = 0; i < 5; i++) pushTraceEvent(mkEv(i));
    });

    expect(rafMock).toHaveBeenCalledOnce();
  });

  it("addTraceEvents is not called until the rAF callback fires", () => {
    let capturedCb: FrameRequestCallback | null = null;
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
      capturedCb = cb;
      return 1;
    });
    vi.stubGlobal("cancelAnimationFrame", vi.fn());

    const spy = vi.spyOn(useGraphStore.getState(), "addTraceEvents");
    renderHook(() => useBufferedTraceEvents());

    act(() => {
      for (let i = 0; i < 5; i++) pushTraceEvent(mkEv(i));
    });

    expect(spy).not.toHaveBeenCalled();

    act(() => {
      capturedCb!(0);
    });

    expect(spy).toHaveBeenCalledOnce();
  });

  it("rAF callback delivers all pending events as one batch in order", () => {
    let capturedCb: FrameRequestCallback | null = null;
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
      capturedCb = cb;
      return 1;
    });
    vi.stubGlobal("cancelAnimationFrame", vi.fn());

    renderHook(() => useBufferedTraceEvents());

    act(() => {
      for (let i = 0; i < 5; i++) pushTraceEvent(mkEv(i));
    });

    act(() => {
      capturedCb!(0);
    });

    const events = useGraphStore.getState().traceEvents;
    expect(events).toHaveLength(5);
    expect(events.map((e) => e.node_id)).toEqual([
      "fn_0",
      "fn_1",
      "fn_2",
      "fn_3",
      "fn_4",
    ]);
  });

  it("pending queue is cleared after rAF flush — second rAF call delivers nothing", () => {
    let capturedCb: FrameRequestCallback | null = null;
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
      capturedCb = cb;
      return 1;
    });
    vi.stubGlobal("cancelAnimationFrame", vi.fn());

    const spy = vi.spyOn(useGraphStore.getState(), "addTraceEvents");
    renderHook(() => useBufferedTraceEvents());

    act(() => {
      pushTraceEvent(mkEv(0));
    });
    act(() => {
      capturedCb!(0);
    });

    // Fire again with nothing pending — addTraceEvents must not be called
    spy.mockClear();
    act(() => {
      capturedCb!(1);
    });

    expect(spy).not.toHaveBeenCalled();
  });

  it("second batch of events schedules a new rAF after the first flush", () => {
    const rafCbs: FrameRequestCallback[] = [];
    vi.stubGlobal("requestAnimationFrame", (cb: FrameRequestCallback) => {
      rafCbs.push(cb);
      return rafCbs.length;
    });
    vi.stubGlobal("cancelAnimationFrame", vi.fn());

    renderHook(() => useBufferedTraceEvents());

    // First batch
    act(() => {
      pushTraceEvent(mkEv(0));
    });
    act(() => {
      rafCbs[0]!(0); // flush first batch
    });

    // Second batch — a new rAF should be scheduled
    act(() => {
      pushTraceEvent(mkEv(1));
    });
    act(() => {
      rafCbs[1]!(1); // flush second batch
    });

    expect(useGraphStore.getState().traceEvents).toHaveLength(2);
    expect(useGraphStore.getState().traceEvents[1]?.node_id).toBe("fn_1");
  });
});

// ---------------------------------------------------------------------------
// session_end force-flush (synchronous — no rAF required)
// ---------------------------------------------------------------------------

describe("useBufferedTraceEvents — session_end force-flush", () => {
  it("session_end flushes pending events before marking session complete", () => {
    vi.stubGlobal(
      "requestAnimationFrame",
      vi.fn(() => 42)
    );
    vi.stubGlobal("cancelAnimationFrame", vi.fn());

    renderHook(() => useBufferedTraceEvents());

    act(() => {
      pushTraceEvent(mkEv(0));
      pushTraceEvent(mkEv(1));
    });

    // At this point events are in pendingRef, not yet in the store.
    expect(useGraphStore.getState().traceEvents).toHaveLength(0);

    act(() => {
      pushSessionEnd(2);
    });

    // After session_end, flush is synchronous — events must be in the store.
    expect(useGraphStore.getState().traceEvents).toHaveLength(2);
    expect(useGraphStore.getState().traceSessionComplete).toBe(true);
  });

  it("session_end cancels the pending rAF to prevent double-flush", () => {
    const cancelMock = vi.fn();
    vi.stubGlobal(
      "requestAnimationFrame",
      vi.fn(() => 42)
    );
    vi.stubGlobal("cancelAnimationFrame", cancelMock);

    renderHook(() => useBufferedTraceEvents());

    act(() => {
      pushTraceEvent(mkEv(0));
    });

    act(() => {
      pushSessionEnd(1);
    });

    expect(cancelMock).toHaveBeenCalledWith(42);
  });

  it("session_end with no pending events still calls endTraceSession", () => {
    vi.stubGlobal(
      "requestAnimationFrame",
      vi.fn(() => 1)
    );
    vi.stubGlobal("cancelAnimationFrame", vi.fn());

    renderHook(() => useBufferedTraceEvents());

    act(() => {
      pushSessionEnd(0);
    });

    expect(useGraphStore.getState().traceSessionComplete).toBe(true);
  });

  it("events in store after session_end are correct (order preserved)", () => {
    vi.stubGlobal(
      "requestAnimationFrame",
      vi.fn(() => 1)
    );
    vi.stubGlobal("cancelAnimationFrame", vi.fn());

    renderHook(() => useBufferedTraceEvents());

    act(() => {
      for (let i = 0; i < 4; i++) pushTraceEvent(mkEv(i));
    });

    act(() => {
      pushSessionEnd(4);
    });

    const events = useGraphStore.getState().traceEvents;
    expect(events).toHaveLength(4);
    expect(events.map((e) => e.node_id)).toEqual([
      "fn_0",
      "fn_1",
      "fn_2",
      "fn_3",
    ]);
  });
});

// ---------------------------------------------------------------------------
// Integration via fake timers (alternative approach: let jsdom's rAF fire)
// ---------------------------------------------------------------------------

describe("useBufferedTraceEvents — fake timer integration", () => {
  it("events land in store after fake timers drain the rAF queue", () => {
    vi.useFakeTimers();

    renderHook(() => useBufferedTraceEvents());

    act(() => {
      for (let i = 0; i < 3; i++) pushTraceEvent(mkEv(i));
    });

    // Events are pending; not yet flushed.
    expect(useGraphStore.getState().traceEvents).toHaveLength(0);

    act(() => {
      vi.runAllTimers();
    });

    expect(useGraphStore.getState().traceEvents).toHaveLength(3);
  });
});
