import type { TraceEvent } from "@grackle/shared-types";

/**
 * Compute per-node call counts over the trace events visible in the current
 * playback window.
 *
 * @param events     The current event buffer (the full trace in buffered mode,
 *                   or the seek window in seekable mode).
 * @param playhead   **Absolute** index into the trace — the exclusive upper
 *                   bound of the visible window.  In non-seekable mode this
 *                   equals the window-relative position; in seekable mode it
 *                   must be translated via ``windowStart``.
 * @param filter     Set of event `event` strings to count; empty = count all.
 * @param mode       `"cumulative"` counts from the start; `"sliding"` uses a
 *                   fixed-size look-back window.
 * @param windowSize Number of events in the sliding window (ignored in cumulative mode).
 * @param windowStart Absolute index of the first event in ``events`` (default 0).
 *                   In seekable mode this is ``traceWindowStart`` from the store.
 *                   The function subtracts it from ``playhead`` to get the
 *                   window-relative upper bound before indexing into ``events``.
 * @returns `{ heat, maxHeat }` — a Map from node_id to count, plus the maximum
 *          count (0 when the window is empty or all events are filtered out).
 */
export function computeHeat(
  events: TraceEvent[],
  playhead: number,
  filter: Set<string>,
  mode: "cumulative" | "sliding",
  windowSize: number,
  windowStart = 0
): { heat: Map<string, number>; maxHeat: number } {
  // Translate absolute playhead to window-relative position so heat is
  // computed over events[0..windowRelativePlayhead] rather than the full
  // window regardless of where in the trace the user has seeked.
  const windowRelativePlayhead = playhead - windowStart;
  const end = Math.min(windowRelativePlayhead, events.length);
  const start = mode === "sliding" ? Math.max(0, end - windowSize) : 0;

  const heat = new Map<string, number>();
  for (let i = start; i < end; i++) {
    const ev = events[i];
    if (!ev) continue; // noUncheckedIndexedAccess guard
    // Skip filtered-out event types (empty filter = all pass)
    if (filter.size > 0 && !filter.has(ev.event)) continue;
    heat.set(ev.node_id, (heat.get(ev.node_id) ?? 0) + 1);
  }

  let maxHeat = 0;
  for (const v of heat.values()) {
    if (v > maxHeat) maxHeat = v;
  }

  return { heat, maxHeat };
}
