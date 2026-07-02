import type { TraceEvent, TraceWindowMessage } from "@grackle/shared-types";
import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import { useGrackleClient } from "../ws/client";
import { _resetFullTraceCache, useFullTrace } from "./useFullTrace";
import { useGraphStore } from "./useGraphStore";

const ev = (i: number): TraceEvent => ({
  event: "call",
  node_id: `a.py:f${i}`,
  ts_ns: i,
  thread_id: 1,
  frame_depth: 0,
});

/** A minimal `trace_window` reply carrying `events` at `start`. */
function win(events: TraceEvent[], start: number): TraceWindowMessage {
  return {
    type: "trace_window",
    payload: {
      session_id: "s",
      start_index: start,
      events,
      total: events.length,
    },
  } as unknown as TraceWindowMessage;
}

const flush = () => new Promise((r) => setTimeout(r, 0));

beforeEach(() => {
  _resetFullTraceCache();
  useGraphStore.setState({
    traceSessionId: null,
    traceSeekable: false,
    traceTotal: 0,
    traceEvents: [],
  });
});

afterEach(() => vi.restoreAllMocks());

describe("useFullTrace", () => {
  it("returns the store trace directly in buffered mode and never fetches", () => {
    const request = vi.fn();
    useGrackleClient.setState({ requestTraceWindow: request });
    useGraphStore.setState({
      traceSessionId: "b1",
      traceSeekable: false,
      traceEvents: [ev(0), ev(1)],
    });

    const { result } = renderHook(() => useFullTrace());
    expect(result.current.events).toHaveLength(2);
    expect(result.current.truncated).toBe(false);
    expect(result.current.loaded).toBe(true);

    act(() => result.current.load()); // no-op in buffered mode
    expect(request).not.toHaveBeenCalled();
  });

  it("pages a seekable session once, even across two consumers", async () => {
    const request = vi.fn(async (_sid: string, start: number) =>
      win([ev(0), ev(1), ev(2)], start)
    );
    useGrackleClient.setState({ requestTraceWindow: request });
    useGraphStore.setState({
      traceSessionId: "s2",
      traceSeekable: true,
      traceTotal: 3,
    });

    const { result: r1 } = renderHook(() => useFullTrace());
    const { result: r2 } = renderHook(() => useFullTrace());
    await act(async () => {
      r1.current.load();
      r2.current.load();
      await flush();
    });

    expect(request).toHaveBeenCalledTimes(1); // shared promise cache
    expect(r1.current.events).toHaveLength(3);
    expect(r1.current.loaded).toBe(true);
    expect(r2.current.events).toHaveLength(3);
  });

  it("propagates truncated when only a prefix is paged", async () => {
    const request = vi.fn(async (_sid: string, start: number) =>
      win([ev(0), ev(1), ev(2)], start)
    );
    useGrackleClient.setState({ requestTraceWindow: request });
    useGraphStore.setState({
      traceSessionId: "s5",
      traceSeekable: true,
      traceTotal: 5, // 3 paged < 5 total → truncated
    });

    const { result } = renderHook(() => useFullTrace());
    await act(async () => {
      result.current.load();
      await flush();
    });
    expect(result.current.truncated).toBe(true);
  });

  it("does not fetch before the total is known", () => {
    const request = vi.fn();
    useGrackleClient.setState({ requestTraceWindow: request });
    useGraphStore.setState({
      traceSessionId: "s0",
      traceSeekable: true,
      traceTotal: 0, // seek handshake not complete yet
    });

    const { result } = renderHook(() => useFullTrace());
    act(() => result.current.load());
    expect(request).not.toHaveBeenCalled();
    expect(result.current.loaded).toBe(false);
  });

  it("drops a stale result when the session changes mid-fetch", async () => {
    let resolve: (m: TraceWindowMessage) => void = () => {};
    const request = vi.fn(
      () =>
        new Promise<TraceWindowMessage>((res) => {
          resolve = res;
        })
    );
    useGrackleClient.setState({ requestTraceWindow: request });
    useGraphStore.setState({
      traceSessionId: "s3",
      traceSeekable: true,
      traceTotal: 3,
    });

    const { result } = renderHook(() => useFullTrace());
    act(() => result.current.load());
    // Session changes before the fetch resolves.
    act(() => useGraphStore.setState({ traceSessionId: "s4" }));
    await act(async () => {
      resolve(win([ev(0)], 0));
      await flush();
    });

    expect(result.current.loaded).toBe(false); // stale result was not written
  });
});
