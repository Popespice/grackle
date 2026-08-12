import type { TraceEvent } from "@grackle/shared-types";

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

export interface EpochSeries {
  points: EpochPoint[];
  /** Number of distinct training runs detected (see multi-run rule below). */
  runs: number;
  /** Count of record_epoch return events whose ret matched the tuple shape but
   *  contained a non-finite value (inf/-inf/nan) and were therefore dropped. */
  dropped: number;
}

// Matches a Python repr() float/int token: ordinary decimal (optionally with
// a fractional part and/or exponent) or the literal `inf` / `-inf` / `nan`.
const FLOAT = String.raw`-?(?:\d+(?:\.\d*)?|\.\d+)(?:[eE][+-]?\d+)?|-?inf|nan`;
const EPOCH_RET_RE = new RegExp(`^\\((\\d+), (${FLOAT}), (${FLOAT})\\)$`);

/** Parse a single matched float/int token from `EPOCH_RET_RE`. `Number("inf")`
 *  is `NaN` in JS, not `Infinity`, so the non-finite literals Python's repr()
 *  can produce must be special-cased before falling back to `Number()`. */
function parseFloatToken(token: string): number {
  if (token === "inf") return Number.POSITIVE_INFINITY;
  if (token === "-inf") return Number.NEGATIVE_INFINITY;
  if (token === "nan") return Number.NaN;
  return Number(token);
}

/**
 * Extract the training-loss curve from a raw trace-event array (must be a
 * from-index-0 prefix — see EpochPoint.eventIndex doc).
 */
export function extractEpochSeries(events: readonly TraceEvent[]): EpochSeries {
  let dropped = 0;
  const candidates: EpochPoint[] = [];

  for (let i = 0; i < events.length; i++) {
    const ev = events[i];
    if (!ev) continue; // noUncheckedIndexedAccess guard

    if (ev.event !== "return") continue;

    // Exact-or-slash-preceded match: a bare endsWith would also match a
    // node_id like "pkg/mymetrics.py:record_epoch", which is NOT this beacon.
    const nodeId = ev.node_id;
    const isRecordEpoch =
      nodeId === "metrics.py:record_epoch" ||
      nodeId.endsWith("/metrics.py:record_epoch");
    if (!isRecordEpoch) continue;

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

  // Multi-run rule: walk survivors in order, splitting into a new run
  // whenever an epoch does not strictly increase over the current run's
  // previous point. Only the last run's points are returned.
  let runs = 0;
  let currentRun: EpochPoint[] = [];
  let lastRun: EpochPoint[] = [];
  for (const point of candidates) {
    const prev = currentRun[currentRun.length - 1];
    if (prev && point.epoch <= prev.epoch) {
      runs += 1;
      lastRun = currentRun;
      currentRun = [point];
    } else {
      currentRun.push(point);
    }
  }
  if (currentRun.length > 0) {
    runs += 1;
    lastRun = currentRun;
  }

  return { points: lastRun, runs, dropped };
}
