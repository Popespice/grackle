import type { TraceEvent } from "@grackle/shared-types";
import { FLOAT } from "./epochSeries";

/**
 * Per-epoch layer weight-statistics extraction from a raw trace-event array
 * (Phase 12.4).
 *
 * `packages/nn`'s `record_layer_stats` beacon
 * (`grackle_nn/metrics.py:record_layer_stats`) fires once per epoch, sibling
 * to `record_epoch`, and returns a flat `(epoch, w0_rms, dw0_rms, w1_rms,
 * dw1_rms, ...)` tuple — one `(wRms, dwRms)` pair per param-carrying
 * ("linear") layer, in model order, pre-rounded to 3 significant figures by
 * the caller (`train.fit`). Under `--capture-values` that shows up as a
 * `"return"` `TraceEvent` whose `values.ret` is the Python `repr()` of that
 * tuple.
 *
 * The tuple's arity is `1 + 2 * linearCount` — the caller must already know
 * `linearCount` (from `extractNetworkSpec`'s `NetworkSpec.tokens`) to build
 * an arity-exact regex; a mismatched arity is a sign of a different net
 * (or a corrupt/foreign beacon) and is silently ignored, same as any other
 * shape mismatch.
 *
 * Pure function over an already-received event array — no wire messages,
 * mirroring `epochSeries.ts`.
 */

export interface LayerStatsPoint {
  epoch: number;
  eventIndex: number;
  /** One `{wRms, dwRms}` per param-carrying layer, in model order. */
  perLinear: { wRms: number; dwRms: number }[];
}

export interface LayerStatsMaxima {
  /** Per-linear-index max wRms across the whole series — the opacity
   *  normalizer for the forward-sweep (weight-magnitude) encoding. */
  wRms: number[];
  /** Per-linear-index max dwRms across the whole series — the opacity
   *  normalizer for the backward-sweep (weight-change) encoding. */
  dwRms: number[];
}

/** See epochSeries.ts's identical helper: `Number("inf")` is `NaN` in JS, not
 *  `Infinity`, so Python repr()'s non-finite literals need special-casing. */
function parseFloatToken(token: string): number {
  if (token === "inf") return Number.POSITIVE_INFINITY;
  if (token === "-inf") return Number.NEGATIVE_INFINITY;
  if (token === "nan") return Number.NaN;
  return Number(token);
}

/** Build a regex matching a Python repr'd `(int, float, float, ..., float)`
 *  tuple with EXACTLY `1 + 2 * linearCount` elements — the arity is baked
 *  into the pattern so a tuple of the wrong shape (a different net) simply
 *  doesn't match, rather than requiring a separate arity check. */
function buildStatsRegex(linearCount: number): RegExp {
  const floatGroups = Array.from(
    { length: 2 * linearCount },
    () => `, (${FLOAT})`
  ).join("");
  return new RegExp(`^\\((\\d+)${floatGroups}\\)$`);
}

/**
 * Extract the per-epoch layer-stats series from a raw trace-event array
 * (must be a from-index-0 prefix, same contract as `extractEpochSeries`).
 *
 * Multi-run rule mirrors `epochSeries.ts`: whenever an epoch does not
 * strictly increase over the current run's previous point, the run restarts;
 * only the LAST run's points are returned, in ascending `eventIndex` order.
 */
export function extractLayerStats(
  events: readonly TraceEvent[],
  linearCount: number
): LayerStatsPoint[] {
  const re = buildStatsRegex(linearCount);
  const candidates: LayerStatsPoint[] = [];

  for (let i = 0; i < events.length; i++) {
    const ev = events[i];
    if (!ev) continue; // noUncheckedIndexedAccess guard

    if (ev.event !== "return") continue;

    // Exact-or-slash-preceded match — same false-positive guard as
    // epochSeries.ts / networkSpec.ts.
    const nodeId = ev.node_id;
    const isRecordLayerStats =
      nodeId === "metrics.py:record_layer_stats" ||
      nodeId.endsWith("/metrics.py:record_layer_stats");
    if (!isRecordLayerStats) continue;

    const ret = ev.values?.ret;
    if (typeof ret !== "string") continue;
    if (ev.values?.ret_truncated === true) continue;

    const match = re.exec(ret);
    if (!match) continue; // wrong arity (a different net) or malformed — ignored

    const epochStr = match[1];
    if (epochStr === undefined) continue; // unreachable given the regex's first group

    const nums = match.slice(2).map((g) => parseFloatToken(g as string));
    if (nums.some((n) => !Number.isFinite(n))) continue; // dropped: non-finite

    const epoch = Number.parseInt(epochStr, 10);
    const perLinear: { wRms: number; dwRms: number }[] = [];
    for (let k = 0; k < nums.length; k += 2) {
      const wRms = nums[k];
      const dwRms = nums[k + 1];
      if (wRms === undefined || dwRms === undefined) continue; // unreachable: nums.length is even by construction
      perLinear.push({ wRms, dwRms });
    }

    candidates.push({ epoch, eventIndex: i, perLinear });
  }

  return keepLastRun(candidates);
}

function keepLastRun(
  candidates: readonly LayerStatsPoint[]
): LayerStatsPoint[] {
  let currentRun: LayerStatsPoint[] = [];
  for (const point of candidates) {
    const prev = currentRun[currentRun.length - 1];
    if (prev && point.epoch <= prev.epoch) {
      currentRun = [point];
    } else {
      currentRun.push(point);
    }
  }
  return currentRun;
}

/**
 * The last point whose `eventIndex` is strictly before `playhead` — the
 * "as of right now" epoch snapshot for a scrubbing playhead. Returns `null`
 * before the first epoch's stats have fired (nothing to show yet). `series`
 * must be in ascending `eventIndex` order (as returned by
 * `extractLayerStats`).
 */
export function statsAtPlayhead(
  series: readonly LayerStatsPoint[],
  playhead: number
): LayerStatsPoint | null {
  // Binary search for the last point with eventIndex < playhead.
  let lo = 0;
  let hi = series.length - 1;
  let result: LayerStatsPoint | null = null;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const point = series[mid];
    if (!point) break; // unreachable given the loop bounds
    if (point.eventIndex < playhead) {
      result = point;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return result;
}

/**
 * Per-linear-index maxima across the whole series — the opacity/width
 * normalizers `NetworkViewPanel` uses so each bundle's brightness reflects
 * its OWN historical range (an early-training bundle reads as faint even
 * though it may already be near another bundle's absolute scale).
 */
export function maxima(series: readonly LayerStatsPoint[]): LayerStatsMaxima {
  const wRms: number[] = [];
  const dwRms: number[] = [];
  for (const point of series) {
    point.perLinear.forEach((stat, i) => {
      wRms[i] = Math.max(wRms[i] ?? 0, stat.wRms);
      dwRms[i] = Math.max(dwRms[i] ?? 0, stat.dwRms);
    });
  }
  return { wRms, dwRms };
}
