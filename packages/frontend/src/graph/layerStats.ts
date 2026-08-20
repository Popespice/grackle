import type { TraceEvent } from "@grackle/shared-types";
import { matchesBeaconNode } from "./beaconNode";
import { FLOAT, parseFloatToken } from "./epochSeries";
import { lastAtOrBefore } from "./playheadLookup";
import type { ScanStep } from "./useAppendOnlyScan";

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
 * mirroring `epochSeries.ts`, whose float grammar (`FLOAT`), token decoder
 * (`parseFloatToken`) and multi-run rule (`computeRunsFromCandidates`) this
 * module shares rather than duplicates: both series are emitted by the same
 * `train.fit` loop and must agree about which run is current — including
 * about how many points a diverged run lost, which is what the scan's
 * `carry` counts.
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
 * Scan `events[startIndex..]` for `record_layer_stats` points, reported with
 * ABSOLUTE indices into `events` regardless of where the scan started. Callers
 * that want an incremental scan own the accumulation, see `useAppendOnlyScan`.
 * Run splitting is NOT applied here; it is re-derived from the full
 * accumulated candidate list.
 *
 * `carry` is the running count of points whose tuple matched the expected
 * shape but held a non-finite value (`inf`/`nan`) and were therefore dropped —
 * the same signal `scanEpochCandidates` reports as `dropped`. A diverged run
 * produces these, and without the count the panel would freeze its bundle
 * widths at the last finite epoch with nothing to say about why.
 */
export function scanLayerStatsCandidates(
  events: readonly TraceEvent[],
  linearCount: number,
  startIndex = 0,
  carry?: number
): ScanStep<LayerStatsPoint, number> {
  const re = buildStatsRegex(linearCount);
  const items: LayerStatsPoint[] = [];
  let dropped = carry ?? 0;

  for (let i = startIndex; i < events.length; i++) {
    const ev = events[i];
    if (!ev) continue; // noUncheckedIndexedAccess guard

    if (ev.event !== "return") continue;
    if (!matchesBeaconNode(ev.node_id, "metrics.py:record_layer_stats")) {
      continue;
    }

    const ret = ev.values?.ret;
    if (typeof ret !== "string") continue;
    if (ev.values?.ret_truncated === true) continue;

    const match = re.exec(ret);
    if (!match) continue; // wrong arity (a different net) or malformed — ignored

    const epochStr = match[1];
    if (epochStr === undefined) continue; // unreachable given the regex's first group

    const nums = match.slice(2).map((g) => parseFloatToken(g as string));
    if (nums.some((n) => !Number.isFinite(n))) {
      dropped += 1;
      continue;
    }

    const epoch = Number.parseInt(epochStr, 10);
    const perLinear: { wRms: number; dwRms: number }[] = [];
    for (let k = 0; k < nums.length; k += 2) {
      const wRms = nums[k];
      const dwRms = nums[k + 1];
      if (wRms === undefined || dwRms === undefined) continue; // unreachable: nums.length is even by construction
      perLinear.push({ wRms, dwRms });
    }

    items.push({ epoch, eventIndex: i, perLinear });
  }

  return { items, carry: dropped };
}

/**
 * The layer stats in effect as of `playhead` — `playheadLookup.ts`'s inclusive
 * convention (the event AT the playhead has happened). Returns `null` before
 * the first epoch's stats have fired (nothing to show yet). `series` must be
 * in ascending `eventIndex` order — i.e. `scanLayerStatsCandidates`' output
 * run through `computeRunsFromCandidates`.
 */
export function statsAtPlayhead(
  series: readonly LayerStatsPoint[],
  playhead: number
): LayerStatsPoint | null {
  return lastAtOrBefore(series, playhead, (p) => p.eventIndex);
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
