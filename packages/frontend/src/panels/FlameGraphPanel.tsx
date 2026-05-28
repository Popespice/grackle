import type { TraceEvent } from "@grackle/shared-types";
import type { JSX } from "react";
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { exportChromeTrace, parseChromeTrace } from "../export/chromeTrace";
import { exportSpeedscope, parseSpeedscope } from "../export/speedscope";
import type { CallFrame } from "../graph/callTree";
import { fetchFullTrace } from "../graph/fetchFullTrace";
import {
  frameColor,
  hitTest,
  layoutFlame,
  maxDepth,
} from "../graph/flameLayout";
import { useCallTree } from "../graph/useCallTree";
import { useGraphStore } from "../graph/useGraphStore";
import { useGrackleClient } from "../ws/client";

const ROW_HEIGHT = 20;
const LABEL_MIN_WIDTH = 32;
// The canvas is the full tree height; the container bounds it and scrolls, so
// deep stacks are never silently truncated.
const MAX_CONTAINER_HEIGHT = "40vh";

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

function formatNs(ns: number): string {
  if (ns >= 1_000_000_000) return `${(ns / 1_000_000_000).toFixed(2)} s`;
  if (ns >= 1_000_000) return `${(ns / 1_000_000).toFixed(2)} ms`;
  if (ns >= 1_000) return `${(ns / 1_000).toFixed(2)} µs`;
  return `${ns} ns`;
}

interface HoverState {
  frame: CallFrame;
  x: number;
  y: number;
}

/** Detect and parse a speedscope or Chrome-trace JSON blob into trace events. */
function parseTraceFile(text: string): TraceEvent[] | null {
  let json: unknown;
  try {
    json = JSON.parse(text);
  } catch {
    return null;
  }
  if (!json || typeof json !== "object") return null;
  const obj = json as Record<string, unknown>;
  if ("$schema" in obj && Array.isArray(obj.profiles)) {
    return parseSpeedscope(json as Parameters<typeof parseSpeedscope>[0]);
  }
  if (Array.isArray(obj.traceEvents)) {
    return parseChromeTrace(json as Parameters<typeof parseChromeTrace>[0]);
  }
  return null;
}

function triggerDownload(filename: string, data: unknown): void {
  if (typeof URL === "undefined" || typeof URL.createObjectURL !== "function") {
    return;
  }
  const blob = new Blob([JSON.stringify(data, null, 2)], {
    type: "application/json",
  });
  const url = URL.createObjectURL(blob);
  const a = document.createElement("a");
  a.href = url;
  a.download = filename;
  document.body.appendChild(a);
  a.click();
  a.remove();
  URL.revokeObjectURL(url);
}

/**
 * FlameGraphPanel — canvas flame graph reconstructed from the trace event
 * stream (Phase 8.2, ADR-0019).
 *
 * All geometry lives in pure helpers (`layoutFlame` / `hitTest`), so the canvas
 * is a thin drawing shell; under jsdom (`getContext` → null) the draw effect
 * no-ops and only the chrome/controls render. ADR-0007: every hook runs before
 * the `traceSessionId === null` early return.
 */
export function FlameGraphPanel(): JSX.Element | null {
  // ── ALL HOOKS FIRST (ADR-0007) ──────────────────────────────────────────
  const traceSessionId = useGraphStore((s) => s.traceSessionId);
  const traceSessionComplete = useGraphStore((s) => s.traceSessionComplete);
  const storeEvents = useGraphStore((s) => s.traceEvents);
  const selectedNodeId = useGraphStore((s) => s.selectedNodeId);
  const graph = useGraphStore((s) => s.graph);
  const traceSeekable = useGraphStore((s) => s.traceSeekable);
  const traceTotal = useGraphStore((s) => s.traceTotal);
  const selectNode = useGraphStore((s) => s.selectNode);
  const setHighlightedNodes = useGraphStore((s) => s.setHighlightedNodes);
  const startTraceSession = useGraphStore((s) => s.startTraceSession);
  const addTraceEvents = useGraphStore((s) => s.addTraceEvents);
  const requestTraceWindow = useGrackleClient((s) => s.requestTraceWindow);

  const canvasRef = useRef<HTMLCanvasElement>(null);
  const fileInputRef = useRef<HTMLInputElement>(null);

  // Callback ref (not useRef) so the measure effect re-runs when the container
  // actually (re)mounts. The panel first mounts with no session and hits the
  // early `return null`, so a useRef-based effect with `[]` deps would latch a
  // null ref and never measure once a session later arrives (flame stays 0-wide
  // and unclickable). The callback ref fires on every mount/unmount instead.
  const [containerEl, setContainerEl] = useState<HTMLDivElement | null>(null);

  const [width, setWidth] = useState(0);
  const [fullEvents, setFullEvents] = useState<TraceEvent[] | null>(null);
  const [loadingFull, setLoadingFull] = useState(false);
  const [truncated, setTruncated] = useState(false);
  const [hover, setHover] = useState<HoverState | null>(null);
  const [error, setError] = useState<string | null>(null);

  const { tree, aggregated, hot } = useCallTree(fullEvents);

  // Set of static-graph node ids, so click-to-focus only selects a node that
  // actually exists in the graph (an unresolved/imported frame would otherwise
  // dim the whole Sigma view to grey with nothing highlighted).
  const graphNodeIds = useMemo(
    () => new Set(graph?.nodes.map((n) => n.id) ?? []),
    [graph]
  );

  const rects = useMemo(
    () =>
      layoutFlame(aggregated, {
        width,
        rowHeight: ROW_HEIGHT,
        minWidth: 0.5,
      }),
    [aggregated, width]
  );

  const depth = maxDepth(aggregated);
  const canvasHeight = Math.max(ROW_HEIGHT, (depth + 1) * ROW_HEIGHT);

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

  // Reset transient panel state whenever the session changes.
  // biome-ignore lint/correctness/useExhaustiveDependencies: reset keyed on session id only.
  useEffect(() => {
    setFullEvents(null);
    setTruncated(false);
    setLoadingFull(false);
    setError(null);
    setHover(null);
  }, [traceSessionId]);

  // Paint the canvas (no-op under jsdom where getContext returns null).
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;
    const dpr =
      typeof window !== "undefined" && window.devicePixelRatio
        ? window.devicePixelRatio
        : 1;
    canvas.width = Math.max(1, Math.floor(width * dpr));
    canvas.height = Math.max(1, Math.floor(canvasHeight * dpr));
    ctx.setTransform(dpr, 0, 0, dpr, 0, 0);
    ctx.clearRect(0, 0, width, canvasHeight);
    ctx.textBaseline = "middle";
    // Literal font stack — the 2D context can't resolve CSS var(); a var() here
    // is silently ignored and labels fall back to the default 10px sans.
    ctx.font = '11px ui-monospace, "SF Mono", Menlo, Consolas, monospace';
    const hotActive = hot.size > 0;

    for (const r of rects) {
      ctx.fillStyle = frameColor(
        r.frame.nodeId,
        hotActive && !hot.has(r.frame)
      );
      ctx.fillRect(r.x, r.y, Math.max(0, r.w - 1), r.h - 1);

      const selected = r.frame.nodeId === selectedNodeId;
      const onHot = hot.has(r.frame);
      if (selected || onHot) {
        ctx.lineWidth = selected ? 2 : 1.5;
        ctx.strokeStyle = selected ? "#b794f6" : "#fff3c4";
        ctx.strokeRect(r.x + 0.5, r.y + 0.5, Math.max(0, r.w - 1.5), r.h - 1.5);
      }

      if (r.w >= LABEL_MIN_WIDTH) {
        ctx.fillStyle = "#1a1208";
        ctx.fillText(r.frame.label, r.x + 3, r.y + r.h / 2, r.w - 6);
      }
    }
  }, [rects, width, canvasHeight, hot, selectedNodeId]);

  const onCanvasClick = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const box = canvas.getBoundingClientRect();
      const hitRect = hitTest(rects, e.clientX - box.left, e.clientY - box.top);
      if (!hitRect) return;
      // Only focus a frame that maps to a real static-graph node. An
      // unresolved/imported frame id isn't in the graph; selecting it would dim
      // every node to grey with nothing highlighted (GraphCanvas's reducer).
      if (!graphNodeIds.has(hitRect.frame.nodeId)) return;
      // Clear any active multi-node highlight so the selection is visible
      // (highlight takes colour precedence over selection — see GraphCanvas).
      setHighlightedNodes(null);
      selectNode(hitRect.frame.nodeId);
    },
    [rects, selectNode, setHighlightedNodes, graphNodeIds]
  );

  const onCanvasMove = useCallback(
    (e: React.MouseEvent<HTMLCanvasElement>) => {
      const canvas = canvasRef.current;
      if (!canvas) return;
      const box = canvas.getBoundingClientRect();
      const x = e.clientX - box.left;
      const y = e.clientY - box.top;
      const hitRect = hitTest(rects, x, y);
      setHover(hitRect ? { frame: hitRect.frame, x, y } : null);
    },
    [rects]
  );

  const onCanvasLeave = useCallback(() => setHover(null), []);

  const onImportFile = useCallback(
    async (e: React.ChangeEvent<HTMLInputElement>) => {
      const file = e.target.files?.[0];
      e.target.value = "";
      if (!file) return;
      try {
        const text = await file.text();
        const events = parseTraceFile(text);
        if (!events || events.length === 0) {
          setError("Unrecognised or empty trace file.");
          return;
        }
        setError(null);
        setFullEvents(null);
        startTraceSession("imported", false);
        addTraceEvents(events);
      } catch {
        setError("Failed to read trace file.");
      }
    },
    [startTraceSession, addTraceEvents]
  );

  const loadFull = useCallback(async () => {
    if (traceSessionId === null) return;
    const sid = traceSessionId;
    // Ignore results once the session has changed mid-fetch (e.g. an import or
    // a re-attach landed while paging) so stale events never overwrite the new
    // session's view.
    const stale = () => useGraphStore.getState().traceSessionId !== sid;
    setLoadingFull(true);
    setError(null);
    try {
      const res = await fetchFullTrace(requestTraceWindow, sid, traceTotal);
      if (stale()) return;
      setFullEvents(res.events);
      setTruncated(res.truncated);
    } catch {
      if (!stale()) setError("Failed to load the full trace.");
    } finally {
      if (!stale()) setLoadingFull(false);
    }
  }, [traceSessionId, requestTraceWindow, traceTotal]);

  // ── EARLY RETURN (after all hooks) ──────────────────────────────────────
  if (traceSessionId === null) return null;

  const windowed =
    traceSeekable && fullEvents === null && storeEvents.length < traceTotal;
  const empty = aggregated.length === 0;
  // Exporting a windowed (partial) reconstruction would silently produce a
  // file that looks complete — require "Load full trace" first.
  const canExport = !empty && !windowed;
  // A buffered session that is still streaming keeps appending live events into
  // the store; importing over it would mix the live and imported traces. Allow
  // import only when idle: no session, a finished session, or a seekable replay.
  const liveStreaming =
    traceSessionId !== null && !traceSessionComplete && !traceSeekable;

  return (
    <section aria-label="Flame graph" style={PANEL_STYLE}>
      <div style={ROW_STYLE}>
        <span style={{ color: "var(--color-text-subtle)", flexShrink: 0 }}>
          Flame graph
        </span>
        <span style={{ fontFamily: "var(--font-mono)" }}>
          {tree.frameCount} frame{tree.frameCount === 1 ? "" : "s"}
          {tree.threads.length > 1 ? ` · ${tree.threads.length} threads` : ""}
          {tree.totalNs > 0 ? ` · ${formatNs(tree.totalNs)}` : ""}
        </span>
        {tree.hadSynthetic && (
          <span
            title="Some frames were closed implicitly (exceptions, generators, truncation, or a partial window) — durations are approximate."
            style={{ color: "var(--color-warning)" }}
          >
            ~approx
          </span>
        )}
        {truncated && (
          <span style={{ color: "var(--color-warning)" }}>
            (first 50k events)
          </span>
        )}

        <span style={{ flex: 1 }} />

        {windowed && (
          <button
            type="button"
            onClick={loadFull}
            disabled={loadingFull}
            style={BUTTON_STYLE}
          >
            {loadingFull ? "Loading…" : `Load full trace (${traceTotal})`}
          </button>
        )}
        <button
          type="button"
          onClick={() =>
            triggerDownload(
              "grackle-trace.speedscope.json",
              exportSpeedscope(tree)
            )
          }
          disabled={!canExport}
          title={
            windowed ? "Load the full trace first to export the whole run" : ""
          }
          style={BUTTON_STYLE}
        >
          ↓ speedscope
        </button>
        <button
          type="button"
          onClick={() =>
            triggerDownload(
              "grackle-trace.chrome.json",
              exportChromeTrace(tree)
            )
          }
          disabled={!canExport}
          title={
            windowed ? "Load the full trace first to export the whole run" : ""
          }
          style={BUTTON_STYLE}
        >
          ↓ Chrome trace
        </button>
        <button
          type="button"
          onClick={() => fileInputRef.current?.click()}
          disabled={liveStreaming}
          title={
            liveStreaming ? "Finish the live session before importing" : ""
          }
          style={BUTTON_STYLE}
        >
          ↑ Import
        </button>
        <input
          ref={fileInputRef}
          type="file"
          accept="application/json,.json"
          aria-label="Import trace file"
          onChange={onImportFile}
          style={{ display: "none" }}
        />
      </div>

      {error && (
        <div role="alert" style={{ color: "var(--color-error)" }}>
          {error}
        </div>
      )}

      <div
        ref={setContainerEl}
        style={{
          position: "relative",
          width: "100%",
          maxHeight: MAX_CONTAINER_HEIGHT,
          overflow: "auto",
        }}
      >
        {empty ? (
          <div
            style={{
              padding: "var(--space-3)",
              color: "var(--color-text-subtle)",
            }}
          >
            No call events in this session yet.
          </div>
        ) : (
          <canvas
            ref={canvasRef}
            aria-label="Flame graph canvas"
            onClick={onCanvasClick}
            onMouseMove={onCanvasMove}
            onMouseLeave={onCanvasLeave}
            style={{
              display: "block",
              width: "100%",
              height: canvasHeight,
              cursor: "pointer",
            }}
          />
        )}

        {hover && (
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
              fontSize: "var(--text-xs)",
              zIndex: 100, // --z-overlay
              boxShadow: "0 2px 8px rgba(0,0,0,0.4)",
            }}
          >
            <div style={{ wordBreak: "break-all" }}>{hover.frame.nodeId}</div>
            <div style={{ color: "var(--color-text-muted)" }}>
              total {formatNs(hover.frame.totalNs)} · self{" "}
              {formatNs(hover.frame.selfNs)} · ×{hover.frame.count}
              {hover.frame.raised ? " · raised" : ""}
            </div>
          </div>
        )}
      </div>
    </section>
  );
}
