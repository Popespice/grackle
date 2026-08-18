import type { TraceEvent } from "@grackle/shared-types";
import type { JSX } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  computeRunsFromCandidates,
  type EpochPoint,
  scanEpochCandidates,
} from "../graph/epochSeries";
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

  // Incremental scan cache: buffered (live-streaming) sessions grow
  // `full.events` via append-only `.concat()` (useGraphStore.ts's
  // addTraceEvents), so re-scanning the WHOLE array from scratch on every
  // batch is O(events) work repeated every ~150ms for the entire session
  // duration — wasted for the overwhelming majority of traces, which
  // contain zero record_epoch events at all. Only the newly-appended tail
  // is scanned; `computeRunsFromCandidates` is then cheaply re-derived
  // (O(candidates), not O(events)) from the full accumulated candidate
  // list every time.
  //
  // `scanCacheRef.lastEvent` guards against anything OTHER than pure
  // append — a new trace session (full.events swaps to a different array)
  // or a seekable reload — by checking that the event this cache last
  // scanned up to is still the same object reference at the same index; a
  // mismatch (or the array having shrunk below what was scanned) forces a
  // full rescan from index 0.
  const scanCacheRef = useRef<{
    scanned: number;
    candidates: EpochPoint[];
    dropped: number;
    lastEvent: TraceEvent | undefined;
  }>({ scanned: 0, candidates: [], dropped: 0, lastEvent: undefined });

  const series = useMemo(() => {
    const events = full.events;
    const cache = scanCacheRef.current;
    const stillValid =
      cache.scanned === 0 ||
      (cache.scanned <= events.length &&
        events[cache.scanned - 1] === cache.lastEvent);

    const startIndex = stillValid ? cache.scanned : 0;
    const scanned = scanEpochCandidates(events, startIndex);
    const candidates = stillValid
      ? cache.candidates.concat(scanned.candidates)
      : scanned.candidates;
    const dropped = (stillValid ? cache.dropped : 0) + scanned.dropped;

    scanCacheRef.current = {
      scanned: events.length,
      candidates,
      dropped,
      lastEvent: events[events.length - 1],
    };

    const { points, runs } = computeRunsFromCandidates(candidates);
    return { points, runs, dropped };
  }, [full.events]);

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

  // Hide entirely, rather than showing a permanent "No learning-loop
  // events" row, once we KNOW there are none — buffered mode always knows
  // (full.events is the live trace), and seekable mode knows once its
  // prefix has loaded. While a seekable prefix is still unloaded we
  // genuinely don't know yet without paging it in, so the "Load loss
  // curve" affordance below still shows (same load-to-find-out precedent
  // as ValueInspector/CausalPath's own seekable panels).
  const knowsThereAreNoEpochs =
    (prefixState.status === "buffered" || prefixState.status === "ready") &&
    series.points.length === 0;
  if (knowsThereAreNoEpochs) return null;

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

    if (series.points.length === 1) {
      return (
        <div style={MUTED}>
          Only 1 epoch recorded so far — need at least 2 to draw a curve.
        </div>
      );
    }
    // series.points.length === 0 is handled by the top-level
    // knowsThereAreNoEpochs early return once the status here (loading/
    // error/unloaded already handled above) implies a "ready"/"buffered"
    // state — unreachable here, kept only as a defensive fallback.
    if (series.points.length === 0) {
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
          {series.dropped > 0 && (
            <span
              style={{ color: "var(--color-warning)" }}
              title="record_epoch events whose loss/accuracy was non-finite (inf/nan) — likely a diverged run"
            >
              {series.dropped} dropped (non-finite)
            </span>
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
              {(hoverPoint.accuracy * 100).toFixed(1)}%
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
