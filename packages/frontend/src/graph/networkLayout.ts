import type { NetworkSpec } from "./networkSpec";

/**
 * Pure neuron-column / edge-bundle layout for `NetworkViewPanel` (Phase
 * 12.4), extracted from the canvas so it is testable under jsdom — the
 * `flameLayout.ts` precedent (ADR-0019). `NetworkViewPanel` calls
 * `layoutNetwork` to get positions, paints them, and routes hover/click
 * through `hitTest` — no geometry lives in the component.
 *
 * Layout is data-only: no color, no live per-epoch stats (wRms/dwRms). The
 * panel combines a `NetworkLayout` with `layerStats.ts`'s `statsAtPlayhead`
 * at PAINT time to decide bundle opacity and tooltip text — keeping this
 * module a pure function of `(spec, width, height)` alone.
 */

export const MAX_NEURONS_PER_COLUMN = 64;

const COLUMN_PADDING_X = 40;
const ROW_PADDING_Y = 24;
const NEURON_SPACING_MAX = 22;
const NEURON_RADIUS_MAX = 7;
const NEURON_RADIUS_MIN = 2;
/** Fixed y for activation glyphs — near the top, clear of the neuron rows
 *  (which start no higher than `ROW_PADDING_Y`) regardless of column
 *  density. Mockup: "relu pill glyphs centered above the gaps between
 *  linear bundles". */
const GLYPH_Y = 16;

export interface NeuronPosition {
  x: number;
  y: number;
}

export interface LayoutColumn {
  /** Index into `NetworkSpec.columns`. */
  columnIndex: number;
  x: number;
  /** Full neuron count — may exceed `MAX_NEURONS_PER_COLUMN`. */
  count: number;
  /** Rendered positions, capped at `MAX_NEURONS_PER_COLUMN`. */
  neurons: NeuronPosition[];
  radius: number;
  /** `"64 of 100 shown"` when `count > MAX_NEURONS_PER_COLUMN`, else `null`.
   *  States the DRAWN count, not the total: a label of `"×100"` read as
   *  "×100 not drawn" in the tooltip, claiming the whole column was omitted
   *  when in fact 64 of it is on screen. */
  overflowLabel: string | null;
  /** `"input · 2"` / `"hidden · 32"` / `"logits · 3"` (mockup wording). */
  caption: string;
}

export interface EdgeBundlePair {
  src: NeuronPosition;
  dst: NeuronPosition;
}

export interface LayoutBundle {
  /** Index into `NetworkSpec.tokens` of the `"linear"` token this bundle
   *  represents. */
  tokenIndex: number;
  srcColumnIndex: number;
  dstColumnIndex: number;
  inDim: number;
  outDim: number;
  /** `"linear 32→32"` — the static half of the mockup's bundle caption; the
   *  panel appends the live `"· w 0.48"` reading at paint time. */
  label: string;
  /** Cartesian product of the src/dst columns' RENDERED (capped) neurons —
   *  e.g. the demo net's three bundles have 2×32=64, 32×32=1024, 32×3=96
   *  pairs (D-N3: "~1184 individual lines"). */
  pairs: EdgeBundlePair[];
  labelX: number;
  labelY: number;
}

export interface LayoutGlyph {
  /** Index into `NetworkSpec.tokens` of the `"activation"` token. */
  tokenIndex: number;
  name: string;
  /** The (shared) column this activation sits between — the column index
   *  the preceding linear token's OUTPUT lands on. */
  columnIndex: number;
  x: number;
  y: number;
}

export interface NetworkLayout {
  width: number;
  height: number;
  columns: LayoutColumn[];
  bundles: LayoutBundle[];
  glyphs: LayoutGlyph[];
}

export type HitResult =
  | { kind: "column"; columnIndex: number }
  | { kind: "bundle"; tokenIndex: number }
  | { kind: "glyph"; tokenIndex: number };

function columnX(index: number, columnCount: number, width: number): number {
  if (columnCount <= 1) return width / 2;
  const usable = width - 2 * COLUMN_PADDING_X;
  return COLUMN_PADDING_X + (usable * index) / (columnCount - 1);
}

function spacingFor(renderedCount: number, height: number): number {
  if (renderedCount <= 1) return 0;
  const usable = Math.max(0, height - 2 * ROW_PADDING_Y);
  return Math.min(NEURON_SPACING_MAX, usable / (renderedCount - 1));
}

function neuronYPositions(
  renderedCount: number,
  height: number,
  spacing: number
): number[] {
  if (renderedCount <= 0) return [];
  if (renderedCount === 1) return [height / 2];
  const totalSpan = spacing * (renderedCount - 1);
  const startY = height / 2 - totalSpan / 2;
  return Array.from({ length: renderedCount }, (_, i) => startY + i * spacing);
}

/** Neuron radius from the row spacing. The two `spacing === 0` causes need
 *  distinguishing: a lone neuron has no spacing to speak of and should be
 *  drawn at full size, whereas a column whose rows collapsed for want of
 *  vertical room is maximally crowded and must shrink, not swell into one
 *  solid blob. */
function radiusFor(spacing: number, renderedCount: number): number {
  if (renderedCount <= 1) return NEURON_RADIUS_MAX;
  if (spacing <= 0) return NEURON_RADIUS_MIN;
  return Math.max(
    NEURON_RADIUS_MIN,
    Math.min(NEURON_RADIUS_MAX, spacing * 0.32)
  );
}

function columnCaption(
  count: number,
  columnIndex: number,
  columnCount: number
): string {
  if (columnIndex === 0) return `input · ${count}`;
  if (columnIndex === columnCount - 1) return `logits · ${count}`;
  return `hidden · ${count}`;
}

/**
 * Lay a parsed network architecture out into neuron columns, edge bundles,
 * and activation glyphs. Returns `null` for a degenerate canvas (non-positive
 * width/height) or a spec with no columns (e.g. an all-activation token
 * list, which `extractNetworkSpec` never actually produces but this stays
 * defensive about).
 */
export function layoutNetwork(
  spec: NetworkSpec,
  width: number,
  height: number
): NetworkLayout | null {
  if (width <= 0 || height <= 0) return null;
  if (spec.columns.length === 0) return null;

  const columnCount = spec.columns.length;
  const columns: LayoutColumn[] = spec.columns.map((count, columnIndex) => {
    const x = columnX(columnIndex, columnCount, width);
    const rendered = Math.min(count, MAX_NEURONS_PER_COLUMN);
    const spacing = spacingFor(rendered, height);
    const neurons = neuronYPositions(rendered, height, spacing).map((y) => ({
      x,
      y,
    }));
    return {
      columnIndex,
      x,
      count,
      neurons,
      radius: radiusFor(spacing, rendered),
      overflowLabel:
        count > MAX_NEURONS_PER_COLUMN
          ? `${MAX_NEURONS_PER_COLUMN} of ${count} shown`
          : null,
      caption: columnCaption(count, columnIndex, columnCount),
    };
  });

  const bundles: LayoutBundle[] = [];
  const glyphs: LayoutGlyph[] = [];
  let linearsSoFar = 0;

  for (let tokenIndex = 0; tokenIndex < spec.tokens.length; tokenIndex++) {
    const token = spec.tokens[tokenIndex];
    if (!token) continue; // noUncheckedIndexedAccess guard

    if (token.kind === "linear") {
      const srcColumnIndex = linearsSoFar;
      const dstColumnIndex = linearsSoFar + 1;
      linearsSoFar += 1;
      const srcColumn = columns[srcColumnIndex];
      const dstColumn = columns[dstColumnIndex];
      // Unreachable for a spec extractNetworkSpec produced (columns.length is
      // always linears.length + 1 by construction) — stays defensive for a
      // hand-built spec in a test.
      if (!srcColumn || !dstColumn) continue;

      const pairs: EdgeBundlePair[] = [];
      for (const src of srcColumn.neurons) {
        for (const dst of dstColumn.neurons) {
          pairs.push({ src, dst });
        }
      }
      bundles.push({
        tokenIndex,
        srcColumnIndex,
        dstColumnIndex,
        inDim: token.inDim,
        outDim: token.outDim,
        label: `linear ${token.inDim}→${token.outDim}`,
        pairs,
        labelX: (srcColumn.x + dstColumn.x) / 2,
        labelY: height / 2,
      });
    } else {
      // An activation token sits at the column shared between the linear
      // bundle before it and the one after it — the number of linear tokens
      // already seen IS that shared column's index.
      const columnIndex = linearsSoFar;
      const column = columns[columnIndex];
      if (!column) continue; // unreachable for a well-formed spec
      glyphs.push({
        tokenIndex,
        name: token.name,
        columnIndex,
        x: column.x,
        y: GLYPH_Y,
      });
    }
  }

  return { width, height, columns, bundles, glyphs };
}

const HIT_RADIUS = 10;
const BUNDLE_HIT_TOLERANCE = 3;

function distance(x1: number, y1: number, x2: number, y2: number): number {
  return Math.hypot(x1 - x2, y1 - y2);
}

/** Perpendicular distance from `(x, y)` to the segment `a`-`b` (clamped to
 *  the segment's endpoints, not the infinite line). */
function distanceToSegment(
  x: number,
  y: number,
  a: NeuronPosition,
  b: NeuronPosition
): number {
  const dx = b.x - a.x;
  const dy = b.y - a.y;
  const lengthSq = dx * dx + dy * dy;
  if (lengthSq === 0) return distance(x, y, a.x, a.y);
  let t = ((x - a.x) * dx + (y - a.y) * dy) / lengthSq;
  t = Math.max(0, Math.min(1, t));
  return distance(x, y, a.x + t * dx, a.y + t * dy);
}

/**
 * Identify what a point hits, in priority order: an activation glyph, a
 * neuron (returned as its column, for a column-level tooltip), then a bundle
 * edge line. Glyphs/columns are checked first (small, precise targets);
 * bundles last (large area, effectively the "everything else on a line"
 * catch-all) — mirrors `flameLayout.ts`'s linear-scan `hitTest`, at a scale
 * (up to ~1184 pairs) where that's still cheap for pointer-event handlers.
 */
export function hitTest(
  layout: NetworkLayout,
  x: number,
  y: number
): HitResult | null {
  for (const glyph of layout.glyphs) {
    if (distance(x, y, glyph.x, glyph.y) <= HIT_RADIUS) {
      return { kind: "glyph", tokenIndex: glyph.tokenIndex };
    }
  }
  for (const column of layout.columns) {
    for (const neuron of column.neurons) {
      if (distance(x, y, neuron.x, neuron.y) <= HIT_RADIUS) {
        return { kind: "column", columnIndex: column.columnIndex };
      }
    }
  }
  for (const bundle of layout.bundles) {
    for (const pair of bundle.pairs) {
      if (distanceToSegment(x, y, pair.src, pair.dst) <= BUNDLE_HIT_TOLERANCE) {
        return { kind: "bundle", tokenIndex: bundle.tokenIndex };
      }
    }
  }
  return null;
}
