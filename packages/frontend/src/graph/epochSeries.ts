import type { TraceEvent } from "@grackle/shared-types";
import { matchesBeaconNode } from "./beaconNode";

/**
 * Training-loss curve extraction from a raw trace-event array (Phase 12.3).
 *
 * `packages/nn`'s `record_epoch` beacon (`grackle_nn/metrics.py:record_epoch`)
 * fires once per epoch and returns `(epoch, loss, accuracy)`. Under
 * `--capture-values` (ADR-0025) that shows up as a `"return"` `TraceEvent`
 * whose `values.ret` is the Python `repr()` of the tuple — full
 * double-precision text, e.g.
 * `"(0, 0.7107649687606569, 0.6041666666666666)"`, never rounded.
 *
 * This module is a pure function over an already-received event array — no
 * wire messages, mirroring `callTree.ts` / `diff.ts`. Non-matching events are
 * silently skipped rather than throwing: a raw trace can contain any mix of
 * `call`/`return`/`exception`/`line` events for any node, and only a small
 * slice of `return` events for this one beacon node are relevant here.
 */

export interface EpochPoint {
  epoch: number;
  loss: number;
  accuracy: number;
  /** Index into the events array this point was extracted from — callers MUST
   *  pass a from-index-0 full-trace prefix (e.g. useFullTrace's result), never
   *  the store's seekable window (traceEvents), which starts at traceWindowStart
   *  != 0 — passing a windowed array here silently produces wrong indices. */
  eventIndex: number;
}

/** The assembled series a panel renders: `scanEpochCandidates`' findings with
 *  the multi-run rule applied. Built by the consumer (see LossCurvePanel),
 *  which owns the incremental accumulation. */
export interface EpochSeries {
  points: EpochPoint[];
  /** Number of distinct training runs detected (see multi-run rule below). */
  runs: number;
  /** Count of record_epoch return events whose ret matched the tuple shape but
   *  contained a non-finite value (inf/-inf/nan) and were therefore dropped. */
  dropped: number;
}

/** A single scan's raw findings, before the multi-run rule is applied —
 *  `useAppendOnlyScan` accumulates these across calls (threading `dropped`
 *  through its `carry`); run-splitting is then re-derived cheaply from the
 *  full accumulated list via `computeRunsFromCandidates`. */
export interface EpochScanResult {
  candidates: EpochPoint[];
  dropped: number;
}

// Matches a Python repr() float/int token: ordinary decimal (optionally with
// a fractional part and/or exponent) or the literal `inf` / `-inf` / `nan`.
// Exported (Phase 12.4): layerStats.ts builds its own arity-parameterized
// regex from this same fragment for the record_layer_stats beacon's flat
// (1 + 2L)-tuple repr, so the float-token grammar has exactly one definition.
export const FLOAT = String.raw`-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|-?inf|nan`;
const EPOCH_RET_RE = new RegExp(`^\\((\\d+), (${FLOAT}), (${FLOAT})\\)$`);

/** Parse a single matched float/int token from a repr'd tuple. `Number("inf")`
 *  is `NaN` in JS, not `Infinity`, so the non-finite literals Python's repr()
 *  can produce must be special-cased before falling back to `Number()`.
 *  Exported alongside `FLOAT` (Phase 12.4) so the grammar and the decoder that
 *  consumes it stay in one place — `layerStats.ts` parses the same tokens. */
export function parseFloatToken(token: string): number {
  if (token === "inf") return Number.POSITIVE_INFINITY;
  if (token === "-inf") return Number.NEGATIVE_INFINITY;
  if (token === "nan") return Number.NaN;
  return Number(token);
}

/**
 * Scan `events` for record_epoch candidates starting at `startIndex`
 * (inclusive), reported with ABSOLUTE indices into `events` regardless of
 * where the scan started. Pure and stateless — callers that want an
 * incremental scan own the accumulation (see LossCurvePanel's scan cache).
 */
export function scanEpochCandidates(
  events: readonly TraceEvent[],
  startIndex = 0
): EpochScanResult {
  let dropped = 0;
  const candidates: EpochPoint[] = [];

  for (let i = startIndex; i < events.length; i++) {
    const ev = events[i];
    if (!ev) continue; // noUncheckedIndexedAccess guard

    if (ev.event !== "return") continue;

    if (!matchesBeaconNode(ev.node_id, "metrics.py:record_epoch")) continue;

    const ret = ev.values?.ret;
    if (typeof ret !== "string") continue;
    if (ev.values?.ret_truncated === true) continue;

    const match = EPOCH_RET_RE.exec(ret);
    if (!match) continue; // e.g. a 2-tuple or an ndarray repr — not our shape

    const epochStr = match[1];
    const lossStr = match[2];
    const accStr = match[3];
    if (
      epochStr === undefined ||
      lossStr === undefined ||
      accStr === undefined
    ) {
      continue; // unreachable given the regex has exactly 3 capture groups
    }

    const epoch = Number.parseInt(epochStr, 10);
    const loss = parseFloatToken(lossStr);
    const accuracy = parseFloatToken(accStr);

    if (!Number.isFinite(loss) || !Number.isFinite(accuracy)) {
      dropped += 1;
      continue;
    }

    candidates.push({ epoch, loss, accuracy, eventIndex: i });
  }

  return { candidates, dropped };
}

/**
 * Multi-run rule: walk candidates in order, splitting into a new run
 * whenever an epoch does not strictly increase over the current run's
 * previous point. Generic over any `{epoch}`-bearing point (Phase 12.4:
 * `layerStats.ts`'s per-epoch series is emitted by the same `train.fit` loop
 * and must split runs identically, so it shares this function rather than
 * carrying a second copy that could drift). Only the last run's points are returned, alongside the
 * total number of runs detected. Cheap (O(candidates), not O(events)) —
 * safe to re-run on every incremental scan update.
 */
export function computeRunsFromCandidates<T extends { epoch: number }>(
  candidates: readonly T[]
): {
  points: T[];
  runs: number;
} {
  let runs = 0;
  let currentRun: T[] = [];
  for (const point of candidates) {
    const prev = currentRun[currentRun.length - 1];
    if (prev && point.epoch <= prev.epoch) {
      runs += 1;
      currentRun = [point];
    } else {
      currentRun.push(point);
    }
  }
  if (currentRun.length > 0) {
    runs += 1;
  }
  return { points: currentRun, runs };
}
