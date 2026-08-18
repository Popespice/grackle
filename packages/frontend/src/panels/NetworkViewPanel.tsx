import type { JSX } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { type EpochPoint, extractEpochSeries } from "../graph/epochSeries";
import {
  type ActivityPhase,
  type ActivitySegment,
  activityAt,
  buildActivityIndex,
} from "../graph/layerActivity";
import {
  extractLayerStats,
  type LayerStatsMaxima,
  type LayerStatsPoint,
  maxima,
  statsAtPlayhead,
} from "../graph/layerStats";
import {
  type HitResult,
  hitTest,
  layoutNetwork,
  type NetworkLayout,
} from "../graph/networkLayout";
import { extractNetworkSpec, type NetworkSpec } from "../graph/networkSpec";
import { useFullTrace } from "../graph/useFullTrace";
import { useGraphStore } from "../graph/useGraphStore";
import { useSeekablePrefixState } from "../graph/useSeekablePrefixState";

/**
 * NetworkViewPanel — the NN rendered AS a network (Phase 12.4): neuron
 * columns, weight bundles, and a playhead-driven forward/backward/update
 * sweep, floating over the code graph. All geometry lives in the pure
 * `networkSpec.ts` / `layerActivity.ts` / `layerStats.ts` / `networkLayout.ts`
 * helpers (the `flameLayout.ts` precedent, ADR-0019); this component is a
 * thin canvas-painting + chrome shell reading the SAME `record_architecture`/
 * `record_layer_stats` beacons the Phase-11.H traceability tests pin.
 *
 * ADR-0007: every hook runs before the `traceSessionId === null` early
 * return. Placement (D-N4): `floating-overlay`, order 10 — a later DOM
 * sibling of `GraphCanvas` (order 0), so it paints above without z-index
 * games. Collapsed (a small chip) by default; the code graph stays fully
 * interactive underneath until the user opens the card.
 *
 * Colors are literal hex, never CSS `var()` or the theme's oklch tokens —
 * the 2D canvas context can't resolve either (FlameGraphPanel:203-205).
 */

const BASE_EDGE_COLOR = "#8899aa";
const NEURON_FILL = "#39404d";
const NEURON_STROKE = "#aab4c0";
const FORWARD_TRAIN_COLOR = "#e86b20";
const FORWARD_EVAL_COLOR = "#29a669";
const BACKWARD_COLOR = "#8b5cf6";
const UPDATE_COLOR = "#f5d90a";
const NEUTRAL_TEXT_COLOR = "#aab4c0";
const CHIP_BG = "#1b1f27";
const CHIP_BORDER = "#3d4657";

const BASE_ALPHA_MIN = 0.15;
const BASE_ALPHA_MAX = 0.75;
const BASE_WIDTH_MIN = 0.5;
const BASE_WIDTH_MAX = 2;

function clamp01(n: number): number {
  return Math.max(0, Math.min(1, n));
}

/** Phase -> the color it borrows for chip/overdraw/highlight; the neutral
 *  phases (loss, loss-grad, reset, idle) have no single associated column or
 *  bundle to overdraw, so they get a muted neutral rather than a fabricated
 *  color. */
function phaseColor(phase: ActivityPhase): string {
  switch (phase) {
    case "forward-train":
      return FORWARD_TRAIN_COLOR;
    case "forward-eval":
      return FORWARD_EVAL_COLOR;
    case "backward":
      return BACKWARD_COLOR;
    case "update":
      return UPDATE_COLOR;
    default:
      return NEUTRAL_TEXT_COLOR;
  }
}

function phaseLabel(phase: ActivityPhase): string {
  switch (phase) {
    case "idle":
      return "idle";
    case "forward-train":
      return "forward · train";
    case "forward-eval":
      return "forward · eval";
    case "loss":
      return "loss";
    case "loss-grad":
      return "loss · grad";
    case "backward":
      return "backward";
    case "update":
      return "update";
    case "reset":
      return "reset";
  }
}

/** Last epoch point at or before `playhead` — same "last point reached"
 *  semantics as LossCurvePanel's playhead marker (`eventIndex <=
 *  playhead`), reading the SAME `extractEpochSeries` data for the header's
 *  `epoch N · loss … · acc …` readout. */
function epochAtPlayhead(
  points: readonly EpochPoint[],
  playhead: number
): EpochPoint | null {
  let result: EpochPoint | null = null;
  for (const p of points) {
    if (p.eventIndex <= playhead) result = p;
    else break;
  }
  return result;
}

function formatReadout(spec: NetworkSpec, epoch: EpochPoint | null): string {
  const model = `model: ${spec.columns.join("-")}`;
  if (!epoch) return model;
  return `${model} · epoch ${epoch.epoch} · loss ${epoch.loss.toFixed(4)} · acc ${epoch.accuracy.toFixed(3)}`;
}

const CHIP_STYLE: React.CSSProperties = {
  position: "absolute",
  top: "var(--space-3)",
  right: "var(--space-3)",
  padding: "var(--space-1) var(--space-3)",
  borderRadius: "var(--radius-full)",
  border: `1px solid ${CHIP_BORDER}`,
  background: CHIP_BG,
  color: "#e2e8f0",
  fontFamily: "var(--font-sans)",
  fontSize: "var(--text-xs)",
  cursor: "pointer",
  zIndex: 50,
};

const CARD_STYLE: React.CSSProperties = {
  position: "absolute",
  inset: "48px",
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-2)",
  padding: "var(--space-3) var(--space-4)",
  borderRadius: "var(--radius-lg)",
  border: "1px solid var(--color-border-strong)",
  background: "var(--color-surface-2)",
  boxShadow: "0 8px 32px rgba(0,0,0,0.5)",
  fontFamily: "var(--font-sans)",
  fontSize: "var(--text-xs)",
  color: "var(--color-text-muted)",
  zIndex: 50,
};

const HEADER_ROW: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-3)",
  flexShrink: 0,
};

const TITLE_STYLE: React.CSSProperties = {
  fontSize: "13px",
  fontWeight: 500,
  color: "var(--color-text)",
  flexShrink: 0,
};

const READOUT_STYLE: React.CSSProperties = {
  fontFamily: "var(--font-mono)",
  color: "var(--color-text-muted)",
  whiteSpace: "nowrap",
  overflow: "hidden",
  textOverflow: "ellipsis",
};

const CLOSE_BUTTON_STYLE: React.CSSProperties = {
  border: "none",
  background: "transparent",
  color: "var(--color-text-muted)",
  cursor: "pointer",
  fontSize: "var(--text-base)",
  lineHeight: 1,
  padding: "0 var(--space-1)",
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

const LEGEND_ROW: React.CSSProperties = {
  display: "flex",
  flexWrap: "wrap",
  gap: "var(--space-3)",
  fontSize: "11px",
  color: "var(--color-text-subtle)",
  flexShrink: 0,
};

function LegendSwatch({
  color,
  label,
}: {
  color: string;
  label: string;
}): JSX.Element {
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: "4px" }}>
      <span
        style={{
          width: "8px",
          height: "8px",
          borderRadius: "2px",
          background: color,
          flexShrink: 0,
        }}
      />
      {label}
    </span>
  );
}

interface HoverState {
  x: number;
  y: number;
  hit: HitResult;
}

function tooltipText(
  hit: HitResult,
  layout: NetworkLayout,
  statsPoint: LayerStatsPoint | null
): string {
  if (hit.kind === "glyph") {
    const glyph = layout.glyphs.find((g) => g.tokenIndex === hit.tokenIndex);
    return glyph ? glyph.name : "";
  }
  if (hit.kind === "column") {
    const column = layout.columns.find(
      (c) => c.columnIndex === hit.columnIndex
    );
    if (!column) return "";
    return column.overflowLabel
      ? `${column.caption} (${column.overflowLabel} not drawn)`
      : column.caption;
  }
  const bundleIndex = layout.bundles.findIndex(
    (b) => b.tokenIndex === hit.tokenIndex
  );
  const bundle = layout.bundles[bundleIndex];
  if (!bundle) return "";
  const stat =
    bundleIndex >= 0 ? statsPoint?.perLinear[bundleIndex] : undefined;
  if (!stat) return bundle.label;
  return `${bundle.label} · w ${stat.wRms.toFixed(3)} · dw ${stat.dwRms.toFixed(3)}`;
}

function paintNetwork(
  ctx: CanvasRenderingContext2D,
  layout: NetworkLayout,
  activity: ActivitySegment,
  statsPoint: LayerStatsPoint | null,
  statsMax: LayerStatsMaxima
): void {
  ctx.clearRect(0, 0, layout.width, layout.height);

  const activeBundleIndex =
    activity.phase === "forward-train" ||
    activity.phase === "forward-eval" ||
    activity.phase === "backward"
      ? layout.bundles.findIndex((b) => b.tokenIndex === activity.activeToken)
      : -1;
  const activeBundle =
    activeBundleIndex >= 0 ? layout.bundles[activeBundleIndex] : undefined;
  const activeGlyph = layout.glyphs.find(
    (g) => g.tokenIndex === activity.activeToken
  );

  // 1. Base bundles — one beginPath/stroke per bundle (D-N3: not per line),
  // opacity+width proportional to that bundle's OWN wRms/max (0 before any
  // stats exist yet, which naturally falls back to a faint, uniform base —
  // "no stats yet -> static architecture at base styling").
  for (let i = 0; i < layout.bundles.length; i++) {
    const bundle = layout.bundles[i];
    if (!bundle) continue;
    const wRms = statsPoint?.perLinear[i]?.wRms ?? 0;
    const max = statsMax.wRms[i] ?? 0;
    const norm = clamp01(max > 0 ? wRms / max : 0);
    ctx.globalAlpha = BASE_ALPHA_MIN + (BASE_ALPHA_MAX - BASE_ALPHA_MIN) * norm;
    ctx.strokeStyle = BASE_EDGE_COLOR;
    ctx.lineWidth = BASE_WIDTH_MIN + (BASE_WIDTH_MAX - BASE_WIDTH_MIN) * norm;
    ctx.beginPath();
    for (const pair of bundle.pairs) {
      ctx.moveTo(pair.src.x, pair.src.y);
      ctx.lineTo(pair.dst.x, pair.dst.y);
    }
    ctx.stroke();
  }
  ctx.globalAlpha = 1;

  // 2. Active bundle overdraw. Forward: solid phase color. Backward: violet,
  // alpha proportional to dwRms/max — "weight change this epoch", never
  // "gradient" (D-N4/mockup — grads are always zero at an epoch boundary,
  // Discrepancy 1).
  let highlightColumnIndex = -1;
  if (activeBundle) {
    if (activity.phase === "backward") {
      const dwRms = statsPoint?.perLinear[activeBundleIndex]?.dwRms ?? 0;
      const max = statsMax.dwRms[activeBundleIndex] ?? 0;
      const norm = clamp01(max > 0 ? dwRms / max : 0);
      ctx.strokeStyle = BACKWARD_COLOR;
      ctx.globalAlpha = 0.35 + 0.55 * norm;
      highlightColumnIndex = activeBundle.srcColumnIndex;
    } else {
      ctx.strokeStyle = phaseColor(activity.phase);
      ctx.globalAlpha = 0.9;
      highlightColumnIndex = activeBundle.dstColumnIndex;
    }
    ctx.lineWidth = 2;
    ctx.beginPath();
    for (const pair of activeBundle.pairs) {
      ctx.moveTo(pair.src.x, pair.src.y);
      ctx.lineTo(pair.dst.x, pair.dst.y);
    }
    ctx.stroke();
    ctx.globalAlpha = 1;
  } else if (activeGlyph) {
    highlightColumnIndex = activeGlyph.columnIndex;
  }

  // 3. Neuron circles.
  for (const column of layout.columns) {
    const highlighted = column.columnIndex === highlightColumnIndex;
    ctx.fillStyle = highlighted ? phaseColor(activity.phase) : NEURON_FILL;
    ctx.strokeStyle = NEURON_STROKE;
    ctx.lineWidth = 1;
    for (const neuron of column.neurons) {
      ctx.beginPath();
      ctx.arc(neuron.x, neuron.y, column.radius, 0, Math.PI * 2);
      ctx.fill();
      ctx.stroke();
    }
  }

  // 4. Activation glyphs — small pills above the shared column they sit at.
  ctx.font = '10px ui-monospace, "SF Mono", Menlo, Consolas, monospace';
  ctx.textAlign = "center";
  ctx.textBaseline = "middle";
  for (const glyph of layout.glyphs) {
    const active = glyph.tokenIndex === activity.activeToken;
    const color = active ? phaseColor(activity.phase) : NEUTRAL_TEXT_COLOR;
    ctx.fillStyle = active ? color : "transparent";
    ctx.strokeStyle = color;
    ctx.beginPath();
    ctx.arc(glyph.x, glyph.y, 8, 0, Math.PI * 2);
    if (active) ctx.fill();
    ctx.stroke();
    ctx.fillStyle = active ? "#0b0e14" : color;
    ctx.fillText(glyph.name.slice(0, 1).toUpperCase(), glyph.x, glyph.y);
  }

  // 5. Column captions beneath, bundle labels at their midpoints.
  ctx.fillStyle = NEUTRAL_TEXT_COLOR;
  ctx.textAlign = "center";
  for (const column of layout.columns) {
    const y = Math.min(
      layout.height - 4,
      (column.neurons[column.neurons.length - 1]?.y ?? layout.height / 2) + 16
    );
    const text = column.overflowLabel
      ? `${column.caption} (${column.overflowLabel})`
      : column.caption;
    ctx.fillText(text, column.x, y);
  }
  for (let i = 0; i < layout.bundles.length; i++) {
    const bundle = layout.bundles[i];
    if (!bundle) continue;
    const wRms = statsPoint?.perLinear[i]?.wRms;
    const text =
      wRms !== undefined
        ? `${bundle.label} · w ${wRms.toFixed(2)}`
        : bundle.label;
    ctx.fillText(text, bundle.labelX, bundle.labelY);
  }
}

export function NetworkViewPanel(): JSX.Element | null {
  // ── ALL HOOKS FIRST (ADR-0007) ──────────────────────────────────────────
  const traceSessionId = useGraphStore((s) => s.traceSessionId);
  const traceSeekable = useGraphStore((s) => s.traceSeekable);
  const traceTotal = useGraphStore((s) => s.traceTotal);
  const tracePlayhead = useGraphStore((s) => s.tracePlayhead);

  const full = useFullTrace();
  const prefixState = useSeekablePrefixState(full);

  const [expanded, setExpanded] = useState(false);
  const [containerEl, setContainerEl] = useState<HTMLDivElement | null>(null);
  const [width, setWidth] = useState(0);
  const [height, setHeight] = useState(0);
  const [hover, setHover] = useState<HoverState | null>(null);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  // A new session starts collapsed with no stale hover — mirrors
  // FlameGraphPanel/LossCurvePanel's session-keyed reset.
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset keyed on session id only.
  useEffect(() => {
    setExpanded(false);
    setHover(null);
  }, [traceSessionId]);

  // Callback ref (not useRef) — FlameGraphPanel's latched-null-ref gotcha:
  // the panel first mounts collapsed (no card, no container), so a
  // useRef-based effect with `[]` deps would never measure once the card
  // later opens.
  useEffect(() => {
    if (!containerEl) return;
    const measure = () => {
      setWidth(containerEl.clientWidth);
      setHeight(containerEl.clientHeight);
    };
    measure();
    if (typeof ResizeObserver === "undefined") return;
    const ro = new ResizeObserver(measure);
    ro.observe(containerEl);
    return () => ro.disconnect();
  }, [containerEl]);

  const spec = useMemo(() => extractNetworkSpec(full.events), [full.events]);

  const linearCount = useMemo(
    () => (spec ? spec.tokens.filter((t) => t.kind === "linear").length : 0),
    [spec]
  );

  // Index/series/spec are memoized on the events array — an O(n) pass once
  // per load; in buffered (live-streaming) mode this re-runs once per
  // rAF-batched store update, which stays cheap at the trace sizes this
  // panel targets (<=~25.8k events for the demo net) and is what makes live
  // training visibly animate here without any bespoke animation loop.
  const activitySegments = useMemo(
    () => (spec ? buildActivityIndex(full.events, spec.tokens.length) : []),
    [full.events, spec]
  );
  const statsSeries = useMemo(
    () => (spec ? extractLayerStats(full.events, linearCount) : []),
    [full.events, spec, linearCount]
  );
  const statsMax = useMemo(() => maxima(statsSeries), [statsSeries]);
  const epochSeries = useMemo(
    () => extractEpochSeries(full.events),
    [full.events]
  );

  const layout = useMemo(
    () =>
      spec && width > 0 && height > 0
        ? layoutNetwork(spec, width, height)
        : null,
    [spec, width, height]
  );

  const activity = useMemo(
    () => activityAt(activitySegments, tracePlayhead),
    [activitySegments, tracePlayhead]
  );
  const statsPoint = useMemo(
    () => statsAtPlayhead(statsSeries, tracePlayhead),
    [statsSeries, tracePlayhead]
  );
  const epochPoint = useMemo(
    () => epochAtPlayhead(epochSeries.points, tracePlayhead),
    [epochSeries.points, tracePlayhead]
  );

  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas || !layout) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const dpr =
      typeof window !== "undefined" && window.devicePixelRatio
        ? window.devicePixelRatio
        : 1;
    canvas.width = Math.max(1, Math.floor(layout.width * dpr));
    canvas.height = Math.max(1, Math.floor(layout.height * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    paintNetwork(ctx, layout, activity, statsPoint, statsMax);
  }, [layout, activity, statsPoint, statsMax]);

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
      const y = e.clientY - box.top;
      const hit = hitTest(layout, x, y);
      setHover(hit ? { x, y, hit } : null);
    },
    [layout]
  );
  const onCanvasLeave = useCallback(() => setHover(null), []);

  // ── EARLY RETURN (after all hooks) ──────────────────────────────────────
  if (traceSessionId === null) return null;

  if (!expanded) {
    return (
      <button
        type="button"
        style={CHIP_STYLE}
        onClick={() => setExpanded(true)}
        aria-label="Open network view"
      >
        network view
      </button>
    );
  }

  const readout = spec ? formatReadout(spec, epochPoint) : "";

  return (
    <section aria-label="Network view" style={CARD_STYLE}>
      <div style={HEADER_ROW}>
        <span style={TITLE_STYLE}>Network view</span>
        <span style={READOUT_STYLE}>{readout}</span>
        <span style={{ flex: 1 }} />
        {spec && (
          <span
            style={{
              padding: "1px var(--space-2)",
              borderRadius: "var(--radius-full)",
              border: `1px solid ${phaseColor(activity.phase)}`,
              color: phaseColor(activity.phase),
              fontFamily: "var(--font-mono)",
              flexShrink: 0,
            }}
          >
            {phaseLabel(activity.phase)}
          </span>
        )}
        <button
          type="button"
          style={CLOSE_BUTTON_STYLE}
          onClick={() => setExpanded(false)}
          aria-label="Close network view"
        >
          ×
        </button>
      </div>

      {prefixState.status === "loading" && <div>Loading network view…</div>}
      {prefixState.status === "error" && (
        <div role="alert" style={{ color: "var(--color-error)" }}>
          Failed to load the full trace.{" "}
          <button type="button" onClick={prefixState.load} style={BUTTON_STYLE}>
            Retry
          </button>
        </div>
      )}
      {prefixState.status === "unloaded" && (
        <button type="button" onClick={prefixState.load} style={BUTTON_STYLE}>
          Load network view ({traceTotal} events)
        </button>
      )}

      {(prefixState.status === "buffered" || prefixState.status === "ready") &&
        (spec === null ? (
          <div>No network beacons (record_architecture) in this trace.</div>
        ) : (
          <>
            {traceSeekable && full.truncated && (
              <div style={{ color: "var(--color-warning)" }}>
                Network view covers the first {full.events.length} events only.
              </div>
            )}
            <div
              ref={setContainerEl}
              style={{ position: "relative", flex: 1, minHeight: 0 }}
            >
              <canvas
                ref={canvasRef}
                aria-label="Network view canvas"
                onMouseMove={onCanvasMove}
                onMouseLeave={onCanvasLeave}
                style={{ display: "block", width: "100%", height: "100%" }}
              />
              {hover && layout && (
                <div
                  style={{
                    position: "absolute",
                    left: Math.min(hover.x + 12, Math.max(0, width - 220)),
                    top: hover.y + 12,
                    pointerEvents: "none",
                    maxWidth: 220,
                    padding: "var(--space-1) var(--space-2)",
                    borderRadius: "var(--radius-sm)",
                    border: "1px solid var(--color-border-strong)",
                    background: "var(--color-surface)",
                    color: "var(--color-text)",
                    fontFamily: "var(--font-mono)",
                    zIndex: 100, // --z-overlay
                    boxShadow: "0 2px 8px rgba(0,0,0,0.4)",
                  }}
                >
                  {tooltipText(hover.hit, layout, statsPoint)}
                </div>
              )}
            </div>
            <div style={LEGEND_ROW}>
              <LegendSwatch
                color={BASE_EDGE_COLOR}
                label="bundle intensity = whole-matrix RMS — individual weights are not captured"
              />
              <LegendSwatch
                color={BACKWARD_COLOR}
                label="weight change this epoch"
              />
              <LegendSwatch
                color={FORWARD_TRAIN_COLOR}
                label="forward (train)"
              />
              <LegendSwatch color={FORWARD_EVAL_COLOR} label="forward (eval)" />
              <LegendSwatch color={UPDATE_COLOR} label="optimizer update" />
            </div>
          </>
        ))}
    </section>
  );
}
