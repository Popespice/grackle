/**
 * The "what is true as of the playhead" lookup shared by every trace-derived
 * series panel (Phase 12.4).
 *
 * **Boundary convention: INCLUSIVE.** An item whose event index EQUALS the
 * playhead has happened. This is the app-wide meaning of `tracePlayhead`
 * already: `ValueInspectorPanel` displays `full.events[tracePlayhead]` (the
 * event AT the playhead), and `LossCurvePanel` seeks to `point.eventIndex`
 * precisely so that clicking epoch N shows epoch N. Three hand-written copies
 * of this scan had drifted to two different conventions (`<` in
 * `layerStats`/`layerActivity`, `<=` in `LossCurvePanel`/`NetworkViewPanel`),
 * so the phase chip and the header readout disagreed by one event at exactly
 * the index a LossCurvePanel click navigated to.
 *
 * `items` must be in ascending key order, which every extractor guarantees
 * (they report absolute indices from a single left-to-right pass).
 */
export function lastIndexAtOrBefore<T>(
  items: readonly T[],
  playhead: number,
  keyOf: (item: T) => number
): number {
  let lo = 0;
  let hi = items.length - 1;
  let result = -1;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const item = items[mid];
    if (item === undefined) break; // unreachable given the loop bounds
    if (keyOf(item) <= playhead) {
      result = mid;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return result;
}

/** `lastIndexAtOrBefore` as the item itself, or `null` when nothing has been
 *  reached yet. */
export function lastAtOrBefore<T>(
  items: readonly T[],
  playhead: number,
  keyOf: (item: T) => number
): T | null {
  const index = lastIndexAtOrBefore(items, playhead, keyOf);
  return index === -1 ? null : (items[index] ?? null);
}
