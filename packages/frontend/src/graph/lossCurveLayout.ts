/**
 * Pure loss/accuracy dual-axis curve layout, extracted from the canvas so it
 * is testable under jsdom (where `canvas.getContext` returns null) — mirrors
 * `flameLayout.ts`'s split: this module computes screen-space geometry and a
 * `hitTest`, a separate (not-this-module) panel component does the painting
 * and routes pointer events through `hitTest` (Phase 12.3, ADR-0019 in
 * spirit).
 *
 * Two independent y-scales share one canvas: loss on the left axis (data-
 * dependent range `[0, maxLoss]`), accuracy on the right axis (fixed
 * `[0, 1]`, since accuracy is already a proportion). Both scales share the
 * same x-axis (epoch, linear) and the same plotted pixel band.
 */

import type { EpochPoint } from "./epochSeries";

export type { EpochPoint } from "./epochSeries";

export interface AxisTick {
  /** pixel coordinate (x for xTicks, y for lossTicks/accTicks) */
  pos: number;
  label: string;
}

export interface CurveLayout {
  /** Screen-space points for the loss line, parallel array to the input points. */
  lossPoints: Array<{ x: number; y: number }>;
  /** Screen-space points for the accuracy line (right axis scale), parallel array to the input points. */
  accPoints: Array<{ x: number; y: number }>;
  /** At most 6 x-axis ticks, integer epoch labels, left-to-right. */
  xTicks: AxisTick[];
  /** At most 4 left-axis (loss) ticks, from 0 to the layout's max loss. */
  lossTicks: AxisTick[];
  /** Fixed right-axis (accuracy) ticks covering 0..1 (e.g. 0, 0.25, 0.5, 0.75, 1). */
  accTicks: AxisTick[];
  /**
   * Nearest-by-x lookup via binary search over lossPoints/accPoints' x
   * coordinates (they are index-parallel to the input points array, which is
   * guaranteed non-decreasing in epoch by the caller/epochSeries contract).
   * Returns the index into the ORIGINAL points array, or null if there are no
   * points.
   */
  hitTest: (px: number) => number | null;
  /** The x pixel coordinate for the point at array index i (bounds-checked;
   *  callers pass a valid index, e.g. from hitTest's result). */
  xForEpochIndex: (i: number) => number;
}

/** Pixel padding around the plotted area — left/right leave room for the two
 *  y-axes' tick labels (right axis is the accuracy axis), top is a small
 *  margin, bottom leaves room for x-axis epoch labels. */
export const PADDING_LEFT = 40;
export const PADDING_RIGHT = 40;
export const PADDING_TOP = 12;
export const PADDING_BOTTOM = 24;

/** A canvas smaller than this (in either dimension) is too small to plot
 *  anything legible — mirrors `flameLayout`'s `width <= 0` guard in spirit,
 *  but a 1px-wide chart is meaningless even though it's not literally <= 0. */
const MIN_WIDTH = 40;
const MIN_HEIGHT = 40;

const MAX_X_TICKS = 6;
const MAX_LOSS_TICKS = 4;

/** Fixed right-axis (accuracy) tick values — never data-dependent. */
const ACC_TICK_VALUES = [0, 0.25, 0.5, 0.75, 1];

/**
 * Lay out a loss/accuracy curve for a canvas of the given pixel size.
 * Returns null when there are fewer than 2 points (a curve needs at least two
 * points to draw a line) or when width/height are too small to be useful.
 */
export function layoutLossCurve(
  points: readonly EpochPoint[],
  width: number,
  height: number
): CurveLayout | null {
  if (points.length < 2) return null;
  if (width < MIN_WIDTH || height < MIN_HEIGHT) return null;

  const first = points[0];
  const last = points[points.length - 1];
  if (!first || !last) return null; // noUncheckedIndexedAccess guard, unreachable given length check

  const plotLeft = PADDING_LEFT;
  const plotRight = width - PADDING_RIGHT;
  const plotTop = PADDING_TOP;
  const plotBottom = height - PADDING_BOTTOM;

  const firstEpoch = first.epoch;
  const lastEpoch = last.epoch;
  const epochSpan = lastEpoch - firstEpoch;

  const xForEpoch = (epoch: number): number => {
    if (epochSpan === 0) {
      // Single distinct epoch value across the run: no meaningful x spread,
      // so park every point at the plot area's horizontal center.
      return (plotLeft + plotRight) / 2;
    }
    return (
      plotLeft + ((epoch - firstEpoch) / epochSpan) * (plotRight - plotLeft)
    );
  };

  let maxLoss = Number.NEGATIVE_INFINITY;
  for (const p of points) {
    if (p.loss > maxLoss) maxLoss = p.loss;
  }
  const flatBaseline = maxLoss <= 0;

  const yForLoss = (loss: number): number => {
    if (flatBaseline) return plotBottom;
    return plotBottom - (loss / maxLoss) * (plotBottom - plotTop);
  };

  // Accuracy's range is the fixed [0, 1] proportion scale, independent of
  // the loss axis — no flat-baseline special case needed here. Clamped:
  // record_epoch's third field is "accuracy" (in [0,1]) for train.py's
  // classification loop, but a raw, unbounded validation loss for
  // heat_model.py's regression loop (packages/nn/src/grackle_nn/ml/
  // heat_model.py:231) — both share this beacon and thus this axis. An
  // out-of-range value must draw pinned to the boundary, not vanish off
  // the canvas.
  const yForAcc = (acc: number): number => {
    const clamped = Math.max(0, Math.min(1, acc));
    return plotBottom - clamped * (plotBottom - plotTop);
  };

  const lossPoints = points.map((p) => ({
    x: xForEpoch(p.epoch),
    y: yForLoss(p.loss),
  }));
  const accPoints = points.map((p) => ({
    x: xForEpoch(p.epoch),
    y: yForAcc(p.accuracy),
  }));

  const xTicks = buildXTicks(firstEpoch, lastEpoch, xForEpoch);

  const lossTicks: AxisTick[] = flatBaseline
    ? [{ pos: plotBottom, label: "0" }]
    : Array.from({ length: MAX_LOSS_TICKS }, (_, i) => {
        const value = (i / (MAX_LOSS_TICKS - 1)) * maxLoss;
        return { pos: yForLoss(value), label: value.toFixed(2) };
      });

  const accTicks: AxisTick[] = ACC_TICK_VALUES.map((v) => ({
    pos: yForAcc(v),
    label: `${Math.round(v * 100)}%`,
  }));

  const xs = lossPoints.map((p) => p.x);

  const hitTest = (px: number): number | null => nearestIndexByX(xs, px);

  const xForEpochIndex = (i: number): number => {
    const clamped = Math.max(0, Math.min(lossPoints.length - 1, i));
    const p = lossPoints[clamped];
    // lossPoints is non-empty here (>=2 points guaranteed above), so p is
    // always defined; the fallback is unreachable but keeps the return type
    // a plain number under noUncheckedIndexedAccess.
    return p ? p.x : plotLeft;
  };

  return {
    lossPoints,
    accPoints,
    xTicks,
    lossTicks,
    accTicks,
    hitTest,
    xForEpochIndex,
  };
}

/**
 * At most `MAX_X_TICKS` integer-epoch ticks, evenly spread across
 * `[firstEpoch, lastEpoch]` and deduped when the integer range spans fewer
 * than `MAX_X_TICKS` distinct values.
 */
function buildXTicks(
  firstEpoch: number,
  lastEpoch: number,
  xForEpoch: (epoch: number) => number
): AxisTick[] {
  const loEpoch = Math.ceil(firstEpoch);
  const hiEpoch = Math.floor(lastEpoch);

  if (loEpoch > hiEpoch) {
    // No integer epoch value falls within range (epochs are ints by the
    // epochSeries contract, so this shouldn't happen in practice) — fall
    // back to the two rounded endpoints so the axis is never empty.
    const ticks: AxisTick[] = [
      { pos: xForEpoch(firstEpoch), label: String(Math.round(firstEpoch)) },
    ];
    if (lastEpoch !== firstEpoch) {
      ticks.push({
        pos: xForEpoch(lastEpoch),
        label: String(Math.round(lastEpoch)),
      });
    }
    return ticks;
  }

  const distinctCount = hiEpoch - loEpoch + 1;
  const tickCount = Math.min(MAX_X_TICKS, distinctCount);
  const seen = new Set<number>();
  const ticks: AxisTick[] = [];
  for (let i = 0; i < tickCount; i++) {
    const t = tickCount === 1 ? 0 : i / (tickCount - 1);
    const epoch = Math.round(loEpoch + t * (hiEpoch - loEpoch));
    if (seen.has(epoch)) continue;
    seen.add(epoch);
    ticks.push({ pos: xForEpoch(epoch), label: String(epoch) });
  }
  return ticks;
}

/**
 * Binary search `xs` (assumed non-decreasing) for the index nearest `px`.
 * Ties (equidistant on both sides) resolve to the earlier index. Returns
 * null for an empty array.
 */
function nearestIndexByX(xs: readonly number[], px: number): number | null {
  if (xs.length === 0) return null;

  const firstX = xs[0];
  const lastX = xs[xs.length - 1];
  if (firstX === undefined || lastX === undefined) return null; // unreachable

  if (px <= firstX) return 0;
  if (px >= lastX) return xs.length - 1;

  let lo = 0;
  let hi = xs.length - 1;
  while (lo < hi) {
    const mid = (lo + hi) >> 1;
    const midX = xs[mid];
    if (midX === undefined) break; // unreachable, mid is always in range
    if (midX === px) return mid;
    if (midX < px) lo = mid + 1;
    else hi = mid;
  }
  // lo is now the first index whose x is >= px; compare it against its
  // predecessor to find the nearer of the two straddling points.
  const before = lo - 1;
  if (before < 0) return lo;
  const beforeX = xs[before];
  const loX = xs[lo];
  if (beforeX === undefined || loX === undefined) return lo; // unreachable
  return px - beforeX <= loX - px ? before : lo;
}
