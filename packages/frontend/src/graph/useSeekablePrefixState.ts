import { useMemo, useRef } from "react";
import type { UseFullTraceResult } from "./useFullTrace";
import { useGraphStore } from "./useGraphStore";

/**
 * The states a `useFullTrace()` consumer walks through when paging a seekable
 * (file-replay) session, plus the buffered (live) case that needs none of it.
 *
 * - `"buffered"` — non-seekable session; `full.events` is already the live
 *   trace, no load step exists.
 * - `"unloaded"` — seekable, not yet paged; the caller should offer a
 *   "Load call stack" affordance.
 * - `"loading"` / `"error"` — the in-flight / failed page fetch.
 * - `"ready"` — a usable prefix is loaded (it may still be `truncated`; that
 *   gate is caller-specific — see ValueInspectorPanel's hard gate vs.
 *   CausalPathPanel's completeness banner, ADR-0026 §8).
 */
export type SeekablePrefixStatus =
  | "buffered"
  | "unloaded"
  | "loading"
  | "error"
  | "ready";

export interface UseSeekablePrefixStateResult {
  status: SeekablePrefixStatus;
  /** Triggers the page-in (seekable, unloaded) or a retry (seekable, error).
   *  No-op in buffered mode and once already loaded — same handler `useFullTrace`
   *  already guards this way. */
  load: () => void;
  /** Whether captured values (`--capture-values`) have been seen anywhere in
   *  the loaded/live prefix — a bounded prefix scan (values appear on the
   *  first sampled calls), so it never false-fires on a lone value-less event
   *  in a capture-ON run. Drives the "enable --capture-values" hint. */
  captureSeen: boolean;
}

/**
 * Derive the seekable-prefix load state machine shared by every panel that
 * consumes `useFullTrace()` (ValueInspectorPanel's time-travel inspector,
 * CausalPathPanel's causal path). Extracted rather than mirrored: three of
 * the four states are behaviorally identical between consumers, and a silent
 * divergence here (e.g. a forgotten guard) would be a real bug, not a cosmetic
 * one — see the Phase 10.5 plan's "mirror vs. extract" judgment call.
 */
export function useSeekablePrefixState(
  full: UseFullTraceResult
): UseSeekablePrefixStateResult {
  const traceSeekable = useGraphStore((s) => s.traceSeekable);
  const traceEvents = useGraphStore((s) => s.traceEvents);
  const traceSessionId = useGraphStore((s) => s.traceSessionId);

  // Once a captured value has been observed, the fact can never become false
  // again for the rest of THIS session — latch it so a live-streaming session
  // (whose traceEvents/full.events array identity changes on every rAF batch)
  // doesn't re-scan up to 2000 events on every tick after the answer is
  // already known. Reset the latch on a session change (a new session may not
  // have captured anything, even if a prior one did).
  const latchedRef = useRef(false);
  const sessionRef = useRef(traceSessionId);
  if (sessionRef.current !== traceSessionId) {
    sessionRef.current = traceSessionId;
    latchedRef.current = false;
  }

  const scanned = useMemo(() => {
    if (latchedRef.current) return true;
    const events = full.loaded ? full.events : traceEvents;
    const limit = Math.min(events.length, 2000);
    for (let i = 0; i < limit; i++) {
      if (events[i]?.values !== undefined) return true;
    }
    return false;
  }, [full.loaded, full.events, traceEvents]);
  if (scanned) latchedRef.current = true;
  const captureSeen = latchedRef.current;

  let status: SeekablePrefixStatus;
  if (!traceSeekable) {
    status = "buffered";
  } else if (full.loading) {
    status = "loading";
  } else if (full.error) {
    status = "error";
  } else if (!full.loaded) {
    status = "unloaded";
  } else {
    status = "ready";
  }

  return { status, load: full.load, captureSeen };
}
