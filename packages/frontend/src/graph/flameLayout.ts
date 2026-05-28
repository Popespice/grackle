import type { CallFrame } from "./callTree";

/**
 * Pure flame-graph (icicle) layout, extracted from the canvas so it is testable
 * under jsdom (where `canvas.getContext` returns null). `FlameGraphPanel` calls
 * `layoutFlame` to get rectangles, paints them, and routes clicks through
 * `hitTest` — no geometry lives in the component (ADR-0019).
 *
 * Layout is width-proportional to `totalNs`: roots fill the full width in
 * proportion to their share of the summed root time, and each child fills a
 * slice of its parent proportional to `child.totalNs / parentDenominator`.
 * Because a frame's children are sequential and never exceed the parent's span,
 * children occupy <= the parent's width and the unused remainder on the right
 * is the parent's self-time — the classic flame shape.
 *
 * Display row is the tree-traversal depth (root = 0), NOT `frame.depth`, so a
 * seekable-window forest whose roots have non-zero `frame_depth` still renders
 * from row 0 down.
 */

export interface FlameRect {
  x: number;
  y: number;
  w: number;
  h: number;
  /** Tree-traversal depth (0 = a root row). */
  depth: number;
  frame: CallFrame;
}

export interface FlameLayoutOptions {
  /** Total pixel width to lay the forest across. */
  width: number;
  /** Pixel height of one row. */
  rowHeight: number;
  /** Rects narrower than this (px) are dropped — they are sub-pixel and
   *  unclickable anyway. Default 0 (keep all). */
  minWidth?: number;
}

/**
 * Lay a call forest out into a flat list of rectangles, depth-first so parents
 * precede children (paint order: parent then its children on the row below).
 */
export function layoutFlame(
  roots: CallFrame[],
  options: FlameLayoutOptions
): FlameRect[] {
  const { width, rowHeight, minWidth = 0 } = options;
  const rects: FlameRect[] = [];
  if (width <= 0 || roots.length === 0) return rects;

  let denom = 0;
  for (const r of roots) denom += r.totalNs;
  // Degenerate run with zero measured time (e.g. an all-same-`ts_ns` trace on a
  // coarse clock, or a single instantaneous call): fall back to equal-width
  // slices at EVERY level — each frame gets an equal share of its parent's
  // span — so the full tree structure stays visible and clickable, not just
  // the root row.
  const equalFallback = denom <= 0;
  const scale = equalFallback ? 0 : width / denom;

  const place = (
    frames: CallFrame[],
    x0: number,
    availWidth: number,
    depth: number
  ): void => {
    let cursor = x0;
    const equalShare =
      equalFallback && frames.length > 0 ? availWidth / frames.length : 0;
    for (const frame of frames) {
      const w = equalFallback ? equalShare : frame.totalNs * scale;
      if (w >= minWidth && w > 0) {
        rects.push({
          x: cursor,
          y: depth * rowHeight,
          w,
          h: rowHeight,
          depth,
          frame,
        });
        place(frame.children, cursor, w, depth + 1);
      }
      cursor += w;
    }
  };

  place(roots, 0, width, 0);
  return rects;
}

/** Maximum tree-traversal depth present in a forest (for canvas sizing).
 *  Returns -1 for an empty forest, 0 for a single flat row. */
export function maxDepth(roots: CallFrame[]): number {
  let max = -1;
  const walk = (frames: CallFrame[], depth: number): void => {
    if (frames.length === 0) return;
    if (depth > max) max = depth;
    for (const f of frames) walk(f.children, depth + 1);
  };
  walk(roots, 0);
  return max;
}

/**
 * Return the rectangle containing point `(x, y)`, or null. Rectangles never
 * overlap (each `(row, x-slice)` band holds at most one), so the first
 * containment match is unambiguous.
 */
export function hitTest(
  rects: FlameRect[],
  x: number,
  y: number
): FlameRect | null {
  for (const r of rects) {
    if (x >= r.x && x < r.x + r.w && y >= r.y && y < r.y + r.h) return r;
  }
  return null;
}

/**
 * Deterministic warm "flame" fill for a node id, returned as an `hsl()` string
 * (canvas-safe in every browser, unlike the project's `oklch` CSS tokens which
 * Sigma/`parseColor` rejects — see ADR-0015). Hue stays in the 18°–54°
 * red-orange-yellow band; the hash spreads node ids across it. `dimmed` desat-
 * urates non-hot-path frames when a hot path is highlighted.
 */
export function frameColor(nodeId: string, dimmed = false): string {
  let hash = 0;
  for (let i = 0; i < nodeId.length; i++) {
    hash = (hash * 31 + nodeId.charCodeAt(i)) | 0;
  }
  const hue = 18 + (Math.abs(hash) % 37); // 18..54
  const sat = dimmed ? 28 : 78;
  const light = dimmed ? 42 : 58;
  return `hsl(${hue}, ${sat}%, ${light}%)`;
}
