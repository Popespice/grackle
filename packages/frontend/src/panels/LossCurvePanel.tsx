import type { JSX } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { extractEpochSeries } from "../graph/epochSeries";
import {
  type CurveLayout,
  layoutLossCurve,
  PADDING_BOTTOM,
  PADDING_LEFT,
  PADDING_RIGHT,
  PADDING_TOP,
} from "../graph/lossCurveLayout";
import { useFullTrace } from "../graph/useFullTrace";
import { useGraphStore } from "../graph/useGraphStore";
import { useSeekablePrefixState } from "../graph/useSeekablePrefixState";

const CANVAS_HEIGHT = 160;

const LOSS_COLOR = "#e86b20";
const ACC_COLOR = "#29a669";
const PLAYHEAD_COLOR = "#b794f6";
const GRID_COLOR = "#334155";
const LABEL_COLOR = "#94a3b8";

const PANEL_STYLE: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-2)",
  padding: "var(--space-3) var(--space-4)",
  borderTop: "1px solid var(--color-border)",
  background: "var(--color-surface-2)",
  fontFamily: "var(--font-sans)",
  fontSize: "var(--text-xs)",
  color: "var(--color-text-muted)",
};

const ROW_STYLE: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-3)",
  flexWrap: "wrap",
};

const BUTTON_STYLE: React.CSSProperties = {
  padding: "2px var(--space-3)",
  borderRadius: "var(--radius-sm)",
  border: "1px solid var(--color-border)",
  background: "var(--color-surface)",
  color: "var(--color-text)",
  cursor: "pointer",
  fontFamily: "var(--font-sans)",
  fontSize: "var(--text-xs)",
};

const MUTED: React.CSSProperties = { color: "var(--color-text-subtle)" };

interface HoverState {
  index: number;
  x: number;
  y: number;
}

function formatInt(n: number): string {
  return n.toLocaleString();
}

function drawPolyline(
  ctx: CanvasRenderingContext2D,
  points: Array<{ x: number; y: number }>,
  color: string
): void {
  if (points.length === 0) return;
  ctx.strokeStyle = color;
  ctx.lineWidth = 1.5;
  ctx.beginPath();
  points.forEach((p, i) => {
    if (i === 0) ctx.moveTo(p.x, p.y);
    else ctx.lineTo(p.x, p.y);
  });
  ctx.stroke();
}

/**
 * LossCurvePanel — renders the NN training loss/accuracy curve extracted
 * from `record_epoch` beacon events (Phase 12.3). Geometry lives in the pure
 * `extractEpochSeries` / `layoutLossCurve` helpers (ADR-0019 precedent, see
 * flameLayout.ts); this component is a thin canvas-painting + controls shell.
 *
 * ADR-0007: every hook runs before the `traceSessionId === null` early
 * return, mirroring TimelinePanel/FlameGraphPanel's bottom-dock convention
 * of hiding entirely (not a placeholder) when there is no active session.
 */
export function LossCurvePanel(): JSX.Element | null {
  // ── ALL HOOKS FIRST (ADR-0007) ──────────────────────────────────────────
  const traceSessionId = useGraphStore((s) => s.traceSessionId);
  const traceSeekable = useGraphStore((s) => s.traceSeekable);
  const tracePlayhead = useGraphStore((s) => s.tracePlayhead);
  const setPlayhead = useGraphStore((s) => s.setPlayhead);

  const full = useFullTrace();
  const prefixState = useSeekablePrefixState(full);

  const canvasRef = useRef<HTMLCanvasElement>(null);

  // Callback ref (not useRef) — the panel first mounts with no session and
  // hits the early `return null`, so a useRef-based measure effect with `[]`
  // deps would latch a null ref and never measure once a session later
  // arrives (FlameGraphPanel precedent, GraphCanvas.tsx:130-134 rationale).
  const [containerEl, setContainerEl] = useState<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  const [hover, setHover] = useState<HoverState | null>(null);

  const series = useMemo(() => extractEpochSeries(full.events), [full.events]);

  const layout: CurveLayout | null = useMemo(
    () => layoutLossCurve(series.points, width, CANVAS_HEIGHT),
    [series.points, width]
  );

  // Measure container width and track resizes (ResizeObserver is absent in jsdom).
  useEffect(() => {
    if (!containerEl) return;
    const measure = () => setWidth(containerEl.clientWidth);
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(containerEl);
    return () => ro.disconnect();
  }, [containerEl]);

  // Reset transient hover state whenever the session changes.
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset keyed on session id only.
  useEffect(() => {
    setHover(null);
  }, [traceSessionId]);

  // Paint the canvas (no-op under jsdom where getContext returns null, or
  // while layout is null — e.g. before the container's first width measure).
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !layout) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const dpr =
      typeof window !== "undefined" && window.devicePixelRatio
        ? window.devicePixelRatio
        : 1;
    canvas.width = Math.max(1, Math.floor(width * dpr));
    canvas.height = Math.max(1, Math.floor(CANVAS_HEIGHT * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, CANVAS_HEIGHT);

    // Gridlines at the loss/accuracy tick rows.
    ctx.strokeStyle = GRID_COLOR;
    ctx.lineWidth = 1;
    for (const tick of layout.lossTicks) {
      ctx.beginPath();
      ctx.moveTo(PADDING_LEFT, tick.pos + 0.5);
      ctx.lineTo(width - PADDING_RIGHT, tick.pos + 0.5);
      ctx.stroke();
    }

    drawPolyline(ctx, layout.lossPoints, LOSS_COLOR);
    drawPolyline(ctx, layout.accPoints, ACC_COLOR);

    // Literal font stack — the 2D context can't resolve CSS var() (colors
    // below are separately literal hex; FlameGraphPanel:203-205 precedent).
    ctx.font = '10px ui-monospace, "SF Mono", Menlo, Consolas, monospace';
    ctx.fillStyle = LABEL_COLOR;
    ctx.textBaseline = "middle";
    for (const tick of layout.xTicks) {
      ctx.textAlign = "center";
      ctx.fillText(tick.label, tick.pos, CANVAS_HEIGHT - PADDING_BOTTOM + 12);
    }
    ctx.textAlign = "right";
    for (const tick of layout.lossTicks) {
      ctx.fillText(tick.label, PADDING_LEFT - 6, tick.pos);
    }
    ctx.textAlign = "left";
    for (const tick of layout.accTicks) {
      ctx.fillText(tick.label, width - PADDING_RIGHT + 6, tick.pos);
    }

    // Playhead marker: a vertical line at the last point whose eventIndex has
    // been reached (points are index-parallel and eventIndex is strictly
    // increasing with array position, so a linear scan is cheap and exact).
    let markerIndex = -1;
    for (let i = 0; i < series.points.length; i++) {
      const p = series.points[i];
      if (p && p.eventIndex <= tracePlayhead) markerIndex = i;
      else break;
    }
    if (markerIndex >= 0) {
      const x = layout.xForEpochIndex(markerIndex);
      ctx.strokeStyle = PLAYHEAD_COLOR;
      ctx.lineWidth = 1.5;
      ctx.beginPath();
      ctx.moveTo(x, PADDING_TOP);
      ctx.lineTo(x, CANVAS_HEIGHT - PADDING_BOTTOM);
      ctx.stroke();
    }
  }, [layout, width, series.points, tracePlayhead]);

  const onCanvasClick = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      if (!layout) return;
      const canvas = canvasRef.current;
      if (!canvas) return;
      const box = canvas.getBoundingClientRect();
      const idx = layout.hitTest(e.clientX - box.left);
      if (idx === null) return;
      const point = series.points[idx];
      if (!point) return;
      // The return event's OWN index — ValueInspectorPanel displays the
      // event AT the playhead (full.events[tracePlayhead]), so seeking to
      // eventIndex (not +1) shows the clicked epoch's tuple.
      setPlayhead(point.eventIndex);
    },
    [layout, series.points, setPlayhead]
  );

  const onCanvasMove = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      if (!layout) {
        setHover(null);
        return;
      }
      const canvas = canvasRef.current;
      if (!canvas) return;
      const box = canvas.getBoundingClientRect();
      const x = e.clientX - box.left;
      const idx = layout.hitTest(x);
      if (idx === null) {
        setHover(null);
        return;
      }
      setHover({ index: idx, x, y: e.clientY - box.top });
    },
    [layout]
  );

  const onCanvasLeave = useCallback(() => setHover(null), []);

  // ── EARLY RETURN (after all hooks) ──────────────────────────────────────
  if (traceSessionId === null) return null;

  const hoverPoint =
    hover !== null ? (series.points[hover.index] ?? null) : null;

  const renderBody = (): JSX.Element => {
    if (prefixState.status === "loading") {
      return <div style={MUTED}>Loading loss curve…</div>;
    }
    if (prefixState.status === "error") {
      return (
        <div role="alert" style={{ color: "var(--color-error)" }}>
          Failed to load the full trace.{" "}
          <button type="button" onClick={prefixState.load} style={BUTTON_STYLE}>
            Retry
          </button>
        </div>
      );
    }
    if (prefixState.status === "unloaded") {
      return (
        <button type="button" onClick={prefixState.load} style={BUTTON_STYLE}>
          Load loss curve
        </button>
      );
    }

    if (series.points.length < 2) {
      return (
        <div style={MUTED}>
          No learning-loop (record_epoch) events in this trace.
        </div>
      );
    }

    return (
      <>
        <div style={ROW_STYLE}>
          <span style={{ color: "var(--color-text-subtle)", flexShrink: 0 }}>
            Loss curve
          </span>
          <span style={{ fontFamily: "var(--font-mono)" }}>
            {series.points.length} epoch{series.points.length === 1 ? "" : "s"}
          </span>
          {series.runs > 1 && (
            <span style={MUTED}>showing latest of {series.runs} runs</span>
          )}
        </div>
        {traceSeekable && full.truncated && (
          <div style={{ color: "var(--color-warning)" }}>
            Curve covers the first {formatInt(full.events.length)} events only.
          </div>
        )}
        <div
          ref={setContainerEl}
          style={{ position: "relative", width: "100%" }}
        >
          <canvas
            ref={canvasRef}
            aria-label="Loss curve canvas"
            onClick={onCanvasClick}
            onMouseMove={onCanvasMove}
            onMouseLeave={onCanvasLeave}
            style={{
              display: "block",
              width: "100%",
              height: CANVAS_HEIGHT,
              cursor: "pointer",
            }}
          />
          {hover && hoverPoint && (
            <div
              style={{
                position: "absolute",
                left: Math.min(hover.x + 12, Math.max(0, width - 180)),
                top: Math.max(0, hover.y - 12),
                pointerEvents: "none",
                padding: "var(--space-1) var(--space-2)",
                borderRadius: "var(--radius-sm)",
                border: "1px solid var(--color-border-strong)",
                background: "var(--color-surface)",
                color: "var(--color-text)",
                fontFamily: "var(--font-mono)",
                fontSize: "var(--text-xs)",
                zIndex: 100, // --z-overlay
                boxShadow: "0 2px 8px rgba(0,0,0,0.4)",
                whiteSpace: "nowrap",
              }}
            >
              epoch {hoverPoint.epoch} · loss {hoverPoint.loss.toFixed(4)} · acc{" "}
              {hoverPoint.accuracy.toFixed(4)}
            </div>
          )}
        </div>
      </>
    );
  };

  return (
    <section aria-label="Loss curve" style={PANEL_STYLE}>
      {renderBody()}
    </section>
  );
}
