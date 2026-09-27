import { vi } from "vitest";

/** One `stroke()` of a path that contained at least one `lineTo` segment. */
export interface RecordedStroke {
  style: string;
  alpha: number;
  lineWidth: number;
  /** `lineTo` calls in the path — `moveTo` is not a segment. */
  segments: number;
  /** Every `moveTo`/`lineTo` vertex of the path, in call order. */
  points: { x: number; y: number }[];
}

/** One filled arc (the last `arc()` of the path when `fill()` ran). */
export interface RecordedArc {
  x: number;
  y: number;
  fill: string;
}

/** One `fillRect`/`strokeRect` call, with the style live at call time. */
export interface RecordedRect {
  x: number;
  y: number;
  w: number;
  h: number;
  style: string;
  lineWidth: number;
}

/** One `fillText` call, with the text state live at call time. */
export interface RecordedText {
  text: string;
  x: number;
  y: number;
  maxWidth: number | undefined;
  fill: string;
  align: string;
  baseline: string;
  font: string;
}

/**
 * A recording stand-in for a 2D context. jsdom has no canvas, and a suite that
 * stubs `getContext` to null makes the paint effect no-op — which leaves the
 * panel's whole paint path (every fill, stroke, caption and label) with no
 * coverage at all. This records enough to assert what was drawn, where, and
 * in what colour. Ported out of `NetworkViewPanel.test.tsx` (Phase 12.4) so
 * every canvas panel can execute its real paint path (campaign T11-2).
 *
 * Arc-only paths (neuron/glyph circles) never call `lineTo`, so `segments`
 * stays 0 for them and `strokes` isolates line work (edge bundles, polylines,
 * gridlines). `texts` is the bare string list; `labels` carries the position
 * and text state of each call. Only the methods the panels actually call are
 * implemented — a new one is a loud `TypeError`, not a silent no-op.
 */
export function makeRecorder() {
  const strokes: RecordedStroke[] = [];
  const arcs: RecordedArc[] = [];
  const texts: string[] = [];
  const labels: RecordedText[] = [];
  const transforms: number[][] = [];
  const clears: number[][] = [];
  const fillRects: RecordedRect[] = [];
  const strokeRects: RecordedRect[] = [];
  let segments = 0;
  let points: { x: number; y: number }[] = [];
  let lastArc: { x: number; y: number } | null = null;
  const ctx = {
    strokeStyle: "",
    fillStyle: "",
    globalAlpha: 1,
    lineWidth: 1,
    font: "",
    textAlign: "",
    textBaseline: "",
    clearRect: (...args: number[]) => {
      clears.push(args);
    },
    setTransform: (...args: number[]) => {
      transforms.push(args);
    },
    beginPath: () => {
      segments = 0;
      points = [];
      lastArc = null;
    },
    moveTo: (x: number, y: number) => {
      points.push({ x, y });
    },
    lineTo: (x: number, y: number) => {
      segments += 1;
      points.push({ x, y });
    },
    arc: (x: number, y: number) => {
      lastArc = { x, y };
    },
    fill: () => {
      if (lastArc) arcs.push({ ...lastArc, fill: String(ctx.fillStyle) });
    },
    stroke: () => {
      if (segments > 0) {
        strokes.push({
          style: String(ctx.strokeStyle),
          alpha: ctx.globalAlpha,
          lineWidth: ctx.lineWidth,
          segments,
          points: [...points],
        });
      }
    },
    fillRect: (x: number, y: number, w: number, h: number) => {
      fillRects.push({
        x,
        y,
        w,
        h,
        style: String(ctx.fillStyle),
        lineWidth: ctx.lineWidth,
      });
    },
    strokeRect: (x: number, y: number, w: number, h: number) => {
      strokeRects.push({
        x,
        y,
        w,
        h,
        style: String(ctx.strokeStyle),
        lineWidth: ctx.lineWidth,
      });
    },
    fillText: (text: string, x = 0, y = 0, maxWidth?: number) => {
      texts.push(text);
      labels.push({
        text,
        x,
        y,
        maxWidth,
        fill: String(ctx.fillStyle),
        align: ctx.textAlign,
        baseline: ctx.textBaseline,
        font: ctx.font,
      });
    },
  };
  return {
    ctx,
    strokes,
    arcs,
    texts,
    labels,
    transforms,
    clears,
    fillRects,
    strokeRects,
  };
}

export type CanvasRecorder = ReturnType<typeof makeRecorder>;

/**
 * A `getContext` replacement that hands every canvas the recorder's context.
 * `getContext` is overloaded across every context id and the recorder only
 * implements the 2d one, so the assignment needs the cast.
 */
export function recordingGetContext(
  recorder: CanvasRecorder
): typeof HTMLCanvasElement.prototype.getContext {
  return vi.fn(
    () => recorder.ctx
  ) as unknown as typeof HTMLCanvasElement.prototype.getContext;
}
