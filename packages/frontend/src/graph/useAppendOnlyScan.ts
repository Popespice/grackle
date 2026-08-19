import type { TraceEvent } from "@grackle/shared-types";
import { useMemo, useRef } from "react";

/**
 * Incremental, append-only scanning of the trace-event array (Phase 12.4).
 *
 * Buffered (live-streaming) sessions grow `full.events` via an append-only
 * `.concat()` (useGraphStore's `addTraceEvents`), so re-scanning the WHOLE
 * array from scratch on every rAF batch is O(events) work repeated every
 * ~150ms for the entire session — and wasted for the overwhelming majority of
 * traces, which contain no beacons at all. Only the newly-appended tail is
 * scanned here; anything derived from the accumulated items (run splitting,
 * maxima) is re-derived cheaply by the caller from the returned list.
 *
 * Extracted from `LossCurvePanel`'s inline ref-cache (Phase 12.3), which had
 * the identical logic; `NetworkViewPanel` needs three of these, at which point
 * a fourth copy of the validity check was the wrong answer.
 *
 * `carry` threads a scanner's own left-to-right state across calls (a
 * dropped-event counter, or `layerActivity`'s open-frame state machine), so
 * even a stateful scan is resumable. `scan(events, 0, undefined)` over the
 * whole array and any sequence of incremental calls must produce the same
 * result — that equivalence is what makes this safe, and it is unit-tested.
 *
 * ## Cache validity
 *
 * `lastEvent` guards against anything OTHER than pure append — a new trace
 * session (the array swaps to a different object), a seekable re-page, or a
 * changed `scan` closure (e.g. a different layer count) — by checking that the
 * event this cache last scanned up to is still the same object reference at
 * the same index. A mismatch forces a full rescan from index 0.
 */

export interface ScanStep<T, C> {
  /** Items found in `events[startIndex..]`, with ABSOLUTE indices. */
  items: T[];
  /** Scanner state to hand back on the next incremental call. */
  carry: C;
}

export type Scanner<T, C> = (
  events: readonly TraceEvent[],
  startIndex: number,
  carry: C | undefined
) => ScanStep<T, C>;

interface ScanCache<T, C> {
  scanned: number;
  items: T[];
  carry: C | undefined;
  lastEvent: TraceEvent | undefined;
  scan: Scanner<T, C> | null;
}

const EMPTY: never[] = [];

export interface AppendOnlyScanResult<T, C> {
  /** Every item found so far. Identity is preserved across batches that added
   *  nothing, so downstream `useMemo`s keyed on it do not re-run. */
  items: T[];
  /** The scanner's accumulated state — e.g. a dropped-event counter. */
  carry: C | undefined;
}

/**
 * Accumulate `scan`'s findings across appends to `events`.
 *
 * `scan` MUST be referentially stable across renders (a module-level function
 * or a `useCallback`) — a new identity is treated as a different scan and
 * forces a full rescan. Pass `enabled: false` to do no work at all and reset
 * the cache; a collapsed panel should never pay for a scan it isn't showing.
 */
export function useAppendOnlyScan<T, C>(
  events: readonly TraceEvent[],
  scan: Scanner<T, C>,
  enabled = true
): AppendOnlyScanResult<T, C> {
  const cacheRef = useRef<ScanCache<T, C>>({
    scanned: 0,
    items: [],
    carry: undefined,
    lastEvent: undefined,
    scan: null,
  });

  return useMemo(() => {
    if (!enabled) {
      cacheRef.current = {
        scanned: 0,
        items: [],
        carry: undefined,
        lastEvent: undefined,
        scan: null,
      };
      return { items: EMPTY as T[], carry: undefined };
    }

    const cache = cacheRef.current;
    const stillValid =
      cache.scan === scan &&
      (cache.scanned === 0 ||
        (cache.scanned <= events.length &&
          events[cache.scanned - 1] === cache.lastEvent));

    const startIndex = stillValid ? cache.scanned : 0;
    const step = scan(events, startIndex, stillValid ? cache.carry : undefined);

    // Preserve the previous array's IDENTITY when the tail held nothing new —
    // a fresh `[].concat()` every batch would invalidate every downstream
    // useMemo (layout, maxima) and defeat the point of scanning incrementally.
    const items =
      stillValid && step.items.length === 0
        ? cache.items
        : stillValid
          ? cache.items.concat(step.items)
          : step.items;

    cacheRef.current = {
      scanned: events.length,
      items,
      carry: step.carry,
      lastEvent: events[events.length - 1],
      scan,
    };
    return { items, carry: step.carry };
  }, [events, scan, enabled]);
}
