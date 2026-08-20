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
 *
 * ## Re-entrancy
 *
 * The cache is written during render, which React may invoke more than once
 * for the same inputs — `<StrictMode>` (main.tsx) double-invokes every render
 * in development, and a concurrent render can be discarded and replayed. The
 * hook is therefore built so a repeat invocation with an already-scanned array
 * returns the cached result WITHOUT calling `scan` again, rather than relying
 * on each scanner to be a no-op over an empty tail. That keeps the re-entrancy
 * guarantee a property of this module instead of an unwritten obligation on
 * every future scanner.
 *
 * ## Disabling
 *
 * `enabled: false` returns nothing and does no work, but deliberately does NOT
 * clear the cache: a collapsed panel that reopens on the same session resumes
 * from where it left off (usually with nothing to do at all) instead of paying
 * a full rescan. Correctness across a session change is already handled by the
 * validity check, which sees a different array.
 */

export interface ScanStep<T, C> {
  /** Items found in `events[startIndex..]`, with ABSOLUTE indices. */
  items: readonly T[];
  /** Scanner state to hand back on the next incremental call. */
  carry: C;
}

/** What the hook returns: the accumulated items, plus the scanner's carry
 *  (`undefined` before the first scan, or while disabled). `items` identity is
 *  preserved across batches that added nothing, so downstream `useMemo`s keyed
 *  on it do not re-run. */
export type AppendOnlyScanResult<T, C> = ScanStep<T, C | undefined>;

export type Scanner<T, C> = (
  events: readonly TraceEvent[],
  startIndex: number,
  carry: C | undefined
) => ScanStep<T, C>;

interface ScanCache<T, C> {
  scanned: number;
  items: readonly T[];
  carry: C | undefined;
  lastEvent: TraceEvent | undefined;
  scan: Scanner<T, C> | null;
}

/** Shared across every disabled consumer, hence `readonly` on the public
 *  `items` — a caller that sorted or pushed in place would otherwise corrupt
 *  every other consumer's "disabled means empty" result. */
const EMPTY: readonly never[] = [];

/**
 * Accumulate `scan`'s findings across appends to `events`.
 *
 * `scan` MUST be referentially stable across renders (a module-level function
 * or a `useCallback`) — a new identity is treated as a different scan and
 * forces a full rescan.
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
    if (!enabled) return { items: EMPTY as readonly T[], carry: undefined };

    const cache = cacheRef.current;
    const stillValid =
      cache.scan === scan &&
      (cache.scanned === 0 ||
        (cache.scanned <= events.length &&
          events[cache.scanned - 1] === cache.lastEvent));

    // Nothing new to look at: hand back the cache untouched, without calling
    // `scan`. This is what makes a repeat render (StrictMode, a replayed
    // concurrent render) and a collapse/reopen genuinely free.
    if (stillValid && cache.scanned >= events.length) {
      return { items: cache.items, carry: cache.carry };
    }

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
