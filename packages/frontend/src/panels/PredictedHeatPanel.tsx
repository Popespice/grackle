/**
 * PredictedHeatPanel — model-predicted per-node "heat" overlay (Phase 12.3).
 *
 * Consumes `graph.metadata.predicted_heat` (shipped Phase 12.2, ml_bridge.py)
 * via the pure `predictedHeat.ts` helpers. Mode control mirrors DiffPanel's
 * opt-in overlay pattern: OFF by default so the runtime heat-map/diff overlay
 * stay the default visualization; the graph overlay is debounced
 * (OVERLAY_DEBOUNCE_MS, DiffPanel precedent) so live streaming doesn't thrash
 * Sigma's nodeReducer every batch.
 *
 * Two modes once enabled:
 * 1. **Predicted** — paints every node by its raw predicted score (heat ramp).
 * 2. **Vs actual** — classifies each node "over"/"under"/"on-target" by
 *    comparing the normalized prediction against the CURRENT session's
 *    actual hit counts (`agentHeat` when available, else local event
 *    counting — DiffPanel's `currentCounts` precedent, verbatim). This is
 *    heat-AT-PLAYHEAD when `agentHeat` is set (TimelinePanel re-queries it on
 *    every playhead move) — scrubbing backward can flip nodes toward "over"
 *    as their actual-so-far count drops. Accepted as DiffPanel parity, not
 *    labelled in the UI since the buffered-fallback path (local event
 *    counting) is whole-trace-so-far, not playhead-relative, so a single
 *    static label would sometimes be wrong.
 *
 * `parsePredictedHeat(graph) === null` → this panel renders nothing (no
 * `hideWhen`; the registry supports it but no shipped panel uses it — see
 * PredictedHeatPanel's ADR-0007 early return, matching DiffPanel/
 * FlameGraphPanel's own return-null convention for their "nothing to show"
 * cases).
 */

import type { JSX } from "react";
import { useCallback, useEffect, useMemo, useState } from "react";
import { heatColor } from "../graph/heatColor";
import {
  PREDICTION_STATUS_COLORS,
  type PredictionStatus,
  parsePredictedHeat,
  predictionStatuses,
} from "../graph/predictedHeat";
import { useGraphStore } from "../graph/useGraphStore";

type Mode = "off" | "predicted" | "vs-actual";

const STATUS_LABELS: Record<PredictionStatus, string> = {
  over: "Over-predicted",
  under: "Under-predicted",
  "on-target": "On-target",
};

/** Muted fallback for "on-target", whose shared overlay colour is "". */
const MUTED_COLOR = "var(--color-text-muted, #888)";

function chipColor(status: PredictionStatus): string {
  return PREDICTION_STATUS_COLORS[status] || MUTED_COLOR;
}

/** Debounce window (ms) for pushing the overlay to the graph during streaming
 *  — mirrors DiffPanel's OVERLAY_DEBOUNCE_MS. */
const OVERLAY_DEBOUNCE_MS = 150;

const PANEL_STYLE: React.CSSProperties = {
  padding: "0.75rem 1rem",
  fontSize: "0.8rem",
  color: "var(--color-text-muted, #888)",
  fontFamily: "var(--font-sans)",
};

const SECTION_TITLE: React.CSSProperties = {
  fontWeight: 600,
  fontSize: "0.75rem",
  color: "var(--color-text, #eee)",
  marginBottom: "0.4rem",
  marginTop: "0.6rem",
};

const CHIP_STYLE: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  gap: "0.3rem",
  padding: "0.15rem 0.5rem",
  borderRadius: "9999px",
  border: "1px solid var(--color-border, #333)",
  fontSize: "0.7rem",
  cursor: "default",
  marginRight: "0.3rem",
  marginBottom: "0.3rem",
};

const BTN: React.CSSProperties = {
  fontSize: "0.7rem",
  padding: "0.2rem 0.5rem",
  borderRadius: "4px",
  border: "1px solid var(--color-border, #333)",
  background: "var(--color-surface, #1a1a1a)",
  color: "var(--color-text, #eee)",
  cursor: "pointer",
  marginRight: "0.3rem",
};

const ACTIVE_BTN: React.CSSProperties = {
  ...BTN,
  borderColor: "#10b981",
  color: "#10b981",
};

const LIST_STYLE: React.CSSProperties = {
  listStyle: "none",
  margin: 0,
  padding: 0,
  fontSize: "0.7rem",
};

const LIST_ITEM_BUTTON_STYLE: React.CSSProperties = {
  width: "100%",
  padding: "0.2rem 0",
  borderBottom: "1px solid var(--color-border, #333)",
  border: "none",
  borderRadius: 0,
  background: "transparent",
  display: "flex",
  alignItems: "center",
  gap: "0.4rem",
  cursor: "pointer",
  color: "inherit",
  font: "inherit",
  textAlign: "left",
};

/** Build a node->count map from trace events — DiffPanel's local helper,
 *  copied verbatim (DiffPanel untouched; dedup noted as post-12.H cleanup). */
function countEvents(
  events: Array<{ node_id: string }>
): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const ev of events) {
    counts[ev.node_id] = (counts[ev.node_id] ?? 0) + 1;
  }
  return counts;
}

interface Swatch {
  nodeId: string;
  color: string;
  detail: string;
}

function SwatchRow({
  swatch,
  onClick,
}: {
  swatch: Swatch;
  onClick: (nodeId: string) => void;
}): JSX.Element {
  return (
    <li>
      <button
        type="button"
        style={LIST_ITEM_BUTTON_STYLE}
        onClick={() => onClick(swatch.nodeId)}
        title={swatch.nodeId}
      >
        <span
          style={{
            flexShrink: 0,
            width: "0.6rem",
            height: "0.6rem",
            borderRadius: "2px",
            background: swatch.color || MUTED_COLOR,
          }}
        />
        <span
          style={{
            overflow: "hidden",
            textOverflow: "ellipsis",
            whiteSpace: "nowrap",
            flex: 1,
            fontFamily: "var(--font-mono)",
          }}
        >
          {swatch.nodeId}
        </span>
        <span style={{ flexShrink: 0 }}>{swatch.detail}</span>
      </button>
    </li>
  );
}

export function PredictedHeatPanel(): JSX.Element | null {
  // ── ALL HOOKS FIRST (ADR-0007) ──────────────────────────────────────────
  const graph = useGraphStore((s) => s.graph);
  const traceSessionId = useGraphStore((s) => s.traceSessionId);
  const traceEvents = useGraphStore((s) => s.traceEvents);
  const agentHeat = useGraphStore((s) => s.agentHeat);
  const setHighlightedNodes = useGraphStore((s) => s.setHighlightedNodes);
  const selectNode = useGraphStore((s) => s.selectNode);
  const setPredictedOverlay = useGraphStore((s) => s.setPredictedOverlay);
  const clearPredictedOverlay = useGraphStore((s) => s.clearPredictedOverlay);

  const [mode, setMode] = useState<Mode>("off");

  const parsed = useMemo(() => parsePredictedHeat(graph), [graph]);

  const graphNodeIds = useMemo(
    () => new Set(graph?.nodes.map((n) => n.id) ?? []),
    [graph]
  );

  // Current node->count snapshot: prefer agent heat, fall back to local event
  // counting — DiffPanel's currentCounts, verbatim.
  const currentCounts = useMemo<Record<string, number>>(() => {
    if (agentHeat !== null) return agentHeat;
    return countEvents(traceEvents);
  }, [agentHeat, traceEvents]);

  // Split memos so scores mode (which only depends on the static `parsed`
  // heat, not the live-streaming `currentCounts`) does not recompute — and
  // thus does not re-push to the store — on every trace batch. Only
  // vs-actual mode churns per batch, matching diffOverlay's accepted
  // streaming profile.
  const scoresOverlay = useMemo(
    () =>
      parsed
        ? { kind: "scores" as const, scores: parsed.scores, max: parsed.max }
        : null,
    [parsed]
  );
  const statusOverlay = useMemo(
    () =>
      parsed
        ? {
            kind: "status" as const,
            statuses: predictionStatuses(parsed, currentCounts),
          }
        : null,
    [parsed, currentCounts]
  );

  // Push the overlay to the graph only when the user has enabled it.
  // Debounced (OVERLAY_DEBOUNCE_MS) so live streaming — which recomputes
  // currentCounts/statusOverlay every batch in vs-actual mode — does not
  // reset Sigma's nodeReducer on every frame (DiffPanel precedent). This
  // single null-check ALSO covers the "payload disappeared" case (a
  // watch-mode re-push without a model): setGraph clears predictedOverlay
  // (F0) and `parsed` recomputes to null in the same render, so
  // scoresOverlay/statusOverlay both go null and this effect clears again —
  // no separate effect needed.
  useEffect(() => {
    const overlay =
      mode === "off"
        ? null
        : mode === "predicted"
          ? scoresOverlay
          : statusOverlay;
    if (overlay === null) {
      clearPredictedOverlay();
      return;
    }
    const timer = setTimeout(() => {
      setPredictedOverlay(overlay);
    }, OVERLAY_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [
    mode,
    scoresOverlay,
    statusOverlay,
    setPredictedOverlay,
    clearPredictedOverlay,
  ]);

  // Ensure the overlay is removed when the panel unmounts.
  useEffect(() => {
    return () => {
      clearPredictedOverlay();
    };
  }, [clearPredictedOverlay]);

  const onSelect = useCallback(
    (nodeId: string) => {
      // Only focus a node that actually exists in the graph (FlameGraphPanel
      // precedent) — clear any active highlight so the selection is visible.
      if (!graphNodeIds.has(nodeId)) return;
      setHighlightedNodes(null);
      selectNode(nodeId);
    },
    [graphNodeIds, setHighlightedNodes, selectNode]
  );

  const predictedTop10 = useMemo<Swatch[]>(() => {
    if (!parsed) return [];
    return Array.from(parsed.scores.entries())
      .sort((a, b) => b[1] - a[1])
      .slice(0, 10)
      .map(([nodeId, score]) => ({
        nodeId,
        color: heatColor(parsed.max > 0 ? score / parsed.max : 0),
        detail: score.toFixed(4),
      }));
  }, [parsed]);

  const statusCounts = useMemo(() => {
    const counts: Record<PredictionStatus, number> = {
      over: 0,
      under: 0,
      "on-target": 0,
    };
    if (statusOverlay) {
      for (const st of statusOverlay.statuses.values()) counts[st] += 1;
    }
    return counts;
  }, [statusOverlay]);

  const underTop10 = useMemo<Swatch[]>(() => {
    if (!statusOverlay) return [];
    const unders: Array<{ nodeId: string; count: number }> = [];
    for (const [nodeId, st] of statusOverlay.statuses) {
      if (st === "under")
        unders.push({ nodeId, count: currentCounts[nodeId] ?? 0 });
    }
    unders.sort((a, b) => b.count - a.count);
    return unders.slice(0, 10).map((u) => ({
      nodeId: u.nodeId,
      color: chipColor("under"),
      detail: String(u.count),
    }));
  }, [statusOverlay, currentCounts]);

  // ── EARLY RETURN (after all hooks) ──────────────────────────────────────
  if (parsed === null) return null;

  return (
    <div style={PANEL_STYLE}>
      <div style={SECTION_TITLE}>
        Predicted heat{" "}
        <span
          style={{
            fontWeight: 400,
            color: "var(--color-text-muted, #888)",
            fontSize: "0.7rem",
          }}
        >
          (model v{parsed.modelVersion}, {parsed.scores.size} nodes)
        </span>
      </div>

      <div style={{ marginBottom: "0.5rem" }}>
        <button
          type="button"
          aria-pressed={mode === "off"}
          style={mode === "off" ? ACTIVE_BTN : BTN}
          onClick={() => setMode("off")}
        >
          Off
        </button>
        <button
          type="button"
          aria-pressed={mode === "predicted"}
          style={mode === "predicted" ? ACTIVE_BTN : BTN}
          onClick={() => setMode("predicted")}
        >
          Predicted
        </button>
        <button
          type="button"
          aria-pressed={mode === "vs-actual"}
          disabled={traceSessionId === null}
          title={
            traceSessionId === null
              ? "Load a trace session to compare against actual heat"
              : ""
          }
          style={mode === "vs-actual" ? ACTIVE_BTN : BTN}
          onClick={() => setMode("vs-actual")}
        >
          Vs actual
        </button>
      </div>

      {mode === "vs-actual" && statusOverlay && (
        <div style={{ marginBottom: "0.4rem" }}>
          {(["over", "under", "on-target"] as PredictionStatus[]).map((s) => {
            const n = statusCounts[s];
            if (n === 0) return null;
            return (
              <span
                key={s}
                style={{
                  ...CHIP_STYLE,
                  borderColor: chipColor(s),
                  color: chipColor(s),
                }}
                title={`${n} node${n !== 1 ? "s" : ""} classified as ${s}`}
              >
                {STATUS_LABELS[s]} {n}
              </span>
            );
          })}
        </div>
      )}

      <div style={SECTION_TITLE}>Top predicted</div>
      {predictedTop10.length === 0 ? (
        <p style={{ margin: 0, fontSize: "0.7rem" }}>No predicted nodes.</p>
      ) : (
        <ul style={LIST_STYLE}>
          {predictedTop10.map((swatch) => (
            <SwatchRow key={swatch.nodeId} swatch={swatch} onClick={onSelect} />
          ))}
        </ul>
      )}

      {mode === "vs-actual" && (
        <>
          <div style={SECTION_TITLE}>Surprise hotspots (under-predicted)</div>
          {underTop10.length === 0 ? (
            <p style={{ margin: 0, fontSize: "0.7rem" }}>
              None — no surprises.
            </p>
          ) : (
            <ul style={LIST_STYLE}>
              {underTop10.map((swatch) => (
                <SwatchRow
                  key={swatch.nodeId}
                  swatch={swatch}
                  onClick={onSelect}
                />
              ))}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
