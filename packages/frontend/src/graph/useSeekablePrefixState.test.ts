import type { TraceEvent } from "@grackle/shared-types";
import { renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";
import type { UseFullTraceResult } from "./useFullTrace";
import { useGraphStore } from "./useGraphStore";
import { useSeekablePrefixState } from "./useSeekablePrefixState";

function full(over: Partial<UseFullTraceResult> = {}): UseFullTraceResult {
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

const call = (id: string): TraceEvent => ({
  event: "call",
  node_id: id,
  ts_ns: 1,
  thread_id: 1,
  frame_depth: 0,
});

describe("useSeekablePrefixState", () => {
  it('is "buffered" for a non-seekable session', () => {
    useGraphStore.setState({ traceSeekable: false, traceEvents: [] });
    const { result } = renderHook(() => useSeekablePrefixState(full()));
    expect(result.current.status).toBe("buffered");
  });

  it('is "unloaded" for a seekable session before load()', () => {
    useGraphStore.setState({ traceSeekable: true, traceEvents: [] });
    const { result } = renderHook(() =>
      useSeekablePrefixState(full({ loaded: false }))
    );
    expect(result.current.status).toBe("unloaded");
  });

  it('is "loading" while a page fetch is in flight', () => {
    useGraphStore.setState({ traceSeekable: true, traceEvents: [] });
    const { result } = renderHook(() =>
      useSeekablePrefixState(full({ loading: true }))
    );
    expect(result.current.status).toBe("loading");
  });

  it('is "error" when the page fetch failed', () => {
    useGraphStore.setState({ traceSeekable: true, traceEvents: [] });
    const { result } = renderHook(() =>
      useSeekablePrefixState(full({ error: true }))
    );
    expect(result.current.status).toBe("error");
  });

  it('is "ready" once a seekable prefix is loaded', () => {
    useGraphStore.setState({ traceSeekable: true, traceEvents: [] });
    const { result } = renderHook(() =>
      useSeekablePrefixState(full({ loaded: true, events: [call("a.py:f")] }))
    );
    expect(result.current.status).toBe("ready");
  });

  it("exposes full.load as the load handler", () => {
    const load = vi.fn();
    useGraphStore.setState({ traceSeekable: true, traceEvents: [] });
    const { result } = renderHook(() => useSeekablePrefixState(full({ load })));
    result.current.load();
    expect(load).toHaveBeenCalled();
  });

  it("reports captureSeen from the loaded prefix", () => {
    useGraphStore.setState({ traceSeekable: true, traceEvents: [] });
    const events: TraceEvent[] = [
      call("a.py:f"),
      { ...call("a.py:g"), values: { args: [{ name: "x", repr: "1" }] } },
    ];
    const { result } = renderHook(() =>
      useSeekablePrefixState(full({ loaded: true, events }))
    );
    expect(result.current.captureSeen).toBe(true);
  });

  it("reports captureSeen false when nothing captured", () => {
    useGraphStore.setState({ traceSeekable: true, traceEvents: [] });
    const events: TraceEvent[] = [call("a.py:f")];
    const { result } = renderHook(() =>
      useSeekablePrefixState(full({ loaded: true, events }))
    );
    expect(result.current.captureSeen).toBe(false);
  });

  it("falls back to the live store window for captureSeen before load", () => {
    const events: TraceEvent[] = [
      { ...call("a.py:g"), values: { args: [{ name: "x", repr: "1" }] } },
    ];
    useGraphStore.setState({ traceSeekable: true, traceEvents: events });
    const { result } = renderHook(() =>
      useSeekablePrefixState(full({ loaded: false }))
    );
    expect(result.current.captureSeen).toBe(true);
  });

  it("latches captureSeen true and never rescans once observed", () => {
    useGraphStore.setState({
      traceSeekable: false,
      traceSessionId: "s1",
      traceEvents: [
        { ...call("a.py:g"), values: { args: [{ name: "x", repr: "1" }] } },
      ],
    });
    const { result, rerender } = renderHook(
      (f: UseFullTraceResult) => useSeekablePrefixState(f),
      { initialProps: full() }
    );
    expect(result.current.captureSeen).toBe(true);

    // A fresh events array with NO captured values would scan false on its
    // own — the latch must keep reporting true regardless (append-only trace
    // events never actually un-capture, but this proves the ref-latch itself,
    // not just "the data happens to still contain a capture").
    useGraphStore.setState({
      traceEvents: [call("a.py:h")],
    });
    rerender(full({ events: [call("a.py:h")] }));
    expect(result.current.captureSeen).toBe(true);
  });

  it("resets the captureSeen latch when the trace session changes", () => {
    useGraphStore.setState({
      traceSeekable: false,
      traceSessionId: "s1",
      traceEvents: [
        { ...call("a.py:g"), values: { args: [{ name: "x", repr: "1" }] } },
      ],
    });
    const { result, rerender } = renderHook(
      (f: UseFullTraceResult) => useSeekablePrefixState(f),
      { initialProps: full() }
    );
    expect(result.current.captureSeen).toBe(true);

    // A new session starts with no captured values yet — the stale latch
    // from the previous session must not leak the "--capture-values is on"
    // hint-suppression into a session that hasn't captured anything.
    useGraphStore.setState({
      traceSessionId: "s2",
      traceEvents: [call("a.py:h")],
    });
    rerender(full({ events: [call("a.py:h")] }));
    expect(result.current.captureSeen).toBe(false);
  });
});
