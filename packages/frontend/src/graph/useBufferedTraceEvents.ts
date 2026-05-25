import type { TraceEvent } from "@grackle/shared-types";
import { useEffect, useRef } from "react";
import { useGrackleClient } from "../ws/client";
import { useGraphStore } from "./useGraphStore";

/**
 * Coalescing buffer for live trace events.
 *
 * Instead of adding each TraceEvent to the store individually (which via
 * repeated concat is O(n²) across a full session), incoming events are
 * accumulated in a ref and flushed in a single `addTraceEvents(batch)` call
 * once per animation frame.  This lowers the total ingest cost from O(n²) to
 * O(n²/B) where B is the typical batch size per frame.
 *
 * **Force-flush on `trace_session_end`** — every event that arrived before
 * session end lands in the store before `endTraceSession` marks the session
 * complete, so no tail events are lost.
 *
 * **Testing** — jsdom provides `requestAnimationFrame` (backed by setTimeout).
 * Tests that need to control when the rAF callback fires should stub it with
 * `vi.stubGlobal("requestAnimationFrame", cb => { captured = cb; return 1; })`
 * and invoke the captured callback manually; or use `vi.useFakeTimers()` with
 * `vi.runAllTimers()` to drain the scheduled call.
 *
 * **Mounting:** call this once in `App`.  It owns the `onTraceEvent` and
 * `onTraceSessionEnd` subscriptions for the lifetime of the component.
 */
export function useBufferedTraceEvents(): void {
  const onTraceEvent = useGrackleClient((s) => s.onTraceEvent);
  const onTraceSessionEnd = useGrackleClient((s) => s.onTraceSessionEnd);

  const pendingRef = useRef<TraceEvent[]>([]);
  const rafRef = useRef<number | null>(null);

  // Subscribe to incoming trace events; coalesce into one rAF flush.
  useEffect(() => {
    const flush = () => {
      const batch = pendingRef.current;
      pendingRef.current = [];
      rafRef.current = null;
      if (batch.length > 0) {
        useGraphStore.getState().addTraceEvents(batch);
      }
    };

    return onTraceEvent((ev) => {
      pendingRef.current.push(ev);
      if (rafRef.current === null) {
        rafRef.current = requestAnimationFrame(flush);
      }
    });
  }, [onTraceEvent]);

  // Force-flush pending events and cancel any scheduled rAF before
  // endTraceSession so no tail events are silently dropped.
  useEffect(() => {
    return onTraceSessionEnd((msg) => {
      // Cancel the rAF flush if one is pending, then drain immediately.
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      const batch = pendingRef.current;
      pendingRef.current = [];
      if (batch.length > 0) {
        useGraphStore.getState().addTraceEvents(batch);
      }
      useGraphStore.getState().endTraceSession(msg);
    });
  }, [onTraceSessionEnd]);
}
