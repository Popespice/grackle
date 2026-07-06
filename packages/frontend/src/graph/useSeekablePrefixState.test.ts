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

  it("recomputes captureSeen from the current session's events, not a stale latch", () => {
    // A captured session followed by an uncaptured one must report false for
    // the second — the scan is deliberately unlatched so a prior session's
    // capture can never leak its "--capture-values is on" hint-suppression
    // into a session that captured nothing (a fixed-session-id re-import or a
    // seekable→seekable switch would otherwise strand a stale latch).
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

    // New session, no captures — must recompute false even though a prior
    // session captured. Also covers a fixed session id reused across imports
    // (the id staying "s1" would strand a session-id-keyed latch).
    useGraphStore.setState({
      traceSessionId: "s2",
      traceEvents: [call("a.py:h")],
    });
    rerender(full({ events: [call("a.py:h")] }));
    expect(result.current.captureSeen).toBe(false);

    // Same-id reuse (e.g. two back-to-back FlameGraph imports under the fixed
    // "imported" session id): swapping in an uncaptured prefix without changing
    // the id must still recompute false — no latch to strand.
    useGraphStore.setState({ traceEvents: [call("a.py:i")] });
    rerender(full({ events: [call("a.py:i")] }));
    expect(result.current.captureSeen).toBe(false);
  });
});
