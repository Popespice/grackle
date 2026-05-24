import type { TraceEvent } from "@grackle/shared-types";

/**
 * Compute per-node call counts over the trace events visible in the current
 * playback window.
 *
 * @param events   Full ordered trace-event list.
 * @param playhead Index into `events` — the exclusive upper bound of the window.
 * @param filter   Set of event `event` strings to count; empty = count all.
 * @param mode     `"cumulative"` counts from the start; `"sliding"` uses a
 *                 fixed-size look-back window.
 * @param windowSize  Number of events in the sliding window (ignored in cumulative mode).
 * @returns `{ heat, maxHeat }` — a Map from node_id to count, plus the maximum
 *          count (0 when the window is empty or all events are filtered out).
 */
export function computeHeat(
  events: TraceEvent[],
  playhead: number,
  filter: Set<string>,
  mode: "cumulative" | "sliding",
  windowSize: number
): { heat: Map<string, number>; maxHeat: number } {
  const end = Math.min(playhead, events.length);
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
