/**
 * DiffPanel -- differential analysis panel (Phase 8.4 / ADR-0021).
 *
 * Two modes:
 *
 * 1. **Trace-vs-static** (default): shows how many graph nodes were touched
 *    vs. cold in the current session.  Active whenever a trace session exists.
 *
 * 2. **Trace-vs-trace** (with baseline): when the user clicks "Set as baseline",
 *    the current per-node counts are snapshotted.  On the next (or current)
 *    session the panel computes a node-level diff vs. that baseline.
 *
 * The panel ALWAYS shows the diff summary (chips + node lists). Painting the
 * graph itself is OPT-IN via the "Show overlay" toggle: the overlay displaces
 * the runtime heat-map (GraphCanvas paints overlay before heat), so leaving it
 * off by default keeps the Phase-6 heat-map as the default visualization.
 * Clicking "Set as baseline" auto-enables the overlay (the user just asked for
 * a diff). The store write is debounced to avoid thrashing Sigma during live
 * streaming.
 *
 * Graph overlay colours (all hex -- ADR-0015) live in graph/diff.ts
 * (DIFF_STATUS_COLORS), shared with GraphCanvas so they cannot drift.
 */

import type { JSX } from "react";
import { useEffect, useMemo, useState } from "react";
import type { DiffStatus } from "../graph/diff";
import {
  DIFF_STATUS_COLORS,
  diffCounts,
  diffToOverlay,
  diffTraceVsStatic,
  diffTraceVsTrace,
  hasRegression,
} from "../graph/diff";
import {
  persistBaseline,
  restoreBaseline,
} from "../graph/diffBaselinePersistence";
import { useGraphStore } from "../graph/useGraphStore";
import { useRuntimeCoverage } from "../graph/useRuntimeCoverage";

// ---------------------------------------------------------------------------
// Status display helpers
// ---------------------------------------------------------------------------

const STATUS_LABELS: Record<DiffStatus, string> = {
  hotter: "Hotter",
  new: "New",
  gone: "Gone",
  colder: "Colder",
  same: "Same",
  cold: "Cold",
  touched: "Touched",
};

/** Muted fallback for statuses whose shared overlay colour is "" (e.g. same). */
const MUTED = "var(--color-text-muted, #888)";

/** Chip colour for a status — reuse the shared overlay map, muted when empty. */
function chipColor(status: DiffStatus): string {
  return DIFF_STATUS_COLORS[status] || MUTED;
}

/** Debounce window (ms) for pushing the overlay to the graph during streaming. */
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

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

/** Build a node->count map from trace events. */
function countEvents(
  events: Array<{ node_id: string }>
): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const ev of events) {
    counts[ev.node_id] = (counts[ev.node_id] ?? 0) + 1;
  }
  return counts;
}

// ---------------------------------------------------------------------------
// DiffPanel
// ---------------------------------------------------------------------------

export function DiffPanel(): JSX.Element | null {
  const traceSessionId = useGraphStore((s) => s.traceSessionId);
  const traceEvents = useGraphStore((s) => s.traceEvents);
  const agentHeat = useGraphStore((s) => s.agentHeat);
  const graph = useGraphStore((s) => s.graph);
  const diffBaseline = useGraphStore((s) => s.diffBaseline);
  const setDiffBaseline = useGraphStore((s) => s.setDiffBaseline);
  const clearDiffBaseline = useGraphStore((s) => s.clearDiffBaseline);
  const setDiffOverlay = useGraphStore((s) => s.setDiffOverlay);
  const clearDiffOverlay = useGraphStore((s) => s.clearDiffOverlay);
  const coverage = useRuntimeCoverage();

  // Whether the diff overlay paints the graph. OFF by default so the runtime
  // heat-map (which the overlay displaces) stays the default visualization;
  // the user opts in via the toggle, and setting a baseline auto-enables it.
  const [overlayEnabled, setOverlayEnabled] = useState(false);

  // Current node->count snapshot: prefer agent heat (accurate for seekable
  // sessions), fall back to local event counting.
  const currentCounts = useMemo<Record<string, number>>(() => {
    if (agentHeat !== null) return agentHeat;
    return countEvents(traceEvents);
  }, [agentHeat, traceEvents]);

  // Compute diff entries whenever mode inputs change.
  const entries = useMemo(() => {
    if (!graph || !traceSessionId) return null;

    if (diffBaseline !== null) {
      // Trace-vs-trace: compare baseline against current session.
      const nodeIds = graph.nodes.map((n) => n.id);
      return diffTraceVsTrace(diffBaseline, currentCounts, nodeIds);
    }

    if (coverage !== null) {
      // Trace-vs-static: classify every graph node as touched / cold.
      return diffTraceVsStatic(graph, coverage);
    }

    return null;
  }, [graph, traceSessionId, diffBaseline, currentCounts, coverage]);

  // Push the overlay to the graph only when the user has enabled it. Debounced
  // (OVERLAY_DEBOUNCE_MS) so live streaming — which grows traceEvents and thus
  // recomputes `entries` every batch — does not reset Sigma's nodeReducer on
  // every frame. Mirrors TimelinePanel's cumulative-heat query debounce.
  useEffect(() => {
    if (!overlayEnabled || entries === null) {
      clearDiffOverlay();
      return;
    }
    const timer = setTimeout(() => {
      setDiffOverlay(diffToOverlay(entries));
    }, OVERLAY_DEBOUNCE_MS);
    return () => {
      clearTimeout(timer);
    };
  }, [overlayEnabled, entries, setDiffOverlay, clearDiffOverlay]);

  // Ensure the overlay is removed when the panel unmounts (heat returns).
  useEffect(() => {
    return () => {
      clearDiffOverlay();
    };
  }, [clearDiffOverlay]);

  // Restore a persisted baseline on graph (re)load — e.g. after F5 (Phase
  // 9.3, ADR-0021 amendment). `setGraph` always clears `diffBaseline` to
  // null on a new static_graph push, so this effect re-fires right after
  // that clear and re-applies the stored value for the *same* project
  // (graphCacheKey content hash). The `getState().graph === graph` identity
  // check guards against a stale resolution from a previous graph landing
  // after a newer graph has already replaced it; the `=== null` check
  // avoids clobbering a baseline the user just set.
  useEffect(() => {
    if (!graph) return;
    restoreBaseline(graph).then((stored) => {
      if (
        stored !== null &&
        useGraphStore.getState().graph === graph &&
        useGraphStore.getState().diffBaseline === null
      ) {
        setDiffBaseline(stored);
      }
    });
  }, [graph, setDiffBaseline]);

  // Pre-filter the actionable buckets once (used by lists + empty-state checks)
  // instead of re-running entries.filter() several times during render.
  const hotterEntries = useMemo(
    () => (entries ?? []).filter((e) => e.status === "hotter"),
    [entries]
  );
  const coldEntries = useMemo(
    () => (entries ?? []).filter((e) => e.status === "cold"),
    [entries]
  );

  // No trace session -- show a placeholder.
  if (!traceSessionId || !graph) {
    return (
      <div style={PANEL_STYLE}>
        <p style={{ margin: 0 }}>Load a trace session to view coverage diff.</p>
      </div>
    );
  }

  const counts = entries ? diffCounts(entries) : null;
  const regression = entries ? hasRegression(entries) : false;
  const mode = diffBaseline !== null ? "trace-vs-trace" : "trace-vs-static";

  return (
    <div style={PANEL_STYLE}>
      {/* Mode header + controls */}
      <div style={SECTION_TITLE}>
        Diff{" "}
        <span
          style={{
            fontWeight: 400,
            color: "var(--color-text-muted, #888)",
            fontSize: "0.7rem",
          }}
        >
          ({mode})
        </span>
      </div>

      <div style={{ marginBottom: "0.5rem" }}>
        {diffBaseline === null ? (
          <button
            type="button"
            style={BTN}
            title="Snapshot current session counts as the baseline for the next diff"
            onClick={() => {
              setDiffBaseline(currentCounts);
              // Setting a baseline is an explicit request to see the diff —
              // turn the graph overlay on so the result is visible.
              setOverlayEnabled(true);
              // Persist on explicit user action only (never via a store
              // subscriber — see the restore effect above for why).
              void persistBaseline(graph, currentCounts).catch(() => {});
            }}
          >
            Set as baseline
          </button>
        ) : (
          <button
            type="button"
            style={{ ...BTN, borderColor: "#f59e0b", color: "#f59e0b" }}
            onClick={() => {
              clearDiffBaseline();
              void persistBaseline(graph, null).catch(() => {});
            }}
          >
            Clear baseline
          </button>
        )}
        <button
          type="button"
          aria-pressed={overlayEnabled}
          style={
            overlayEnabled
              ? { ...BTN, borderColor: "#10b981", color: "#10b981" }
              : BTN
          }
          onClick={() => setOverlayEnabled((v) => !v)}
          title={
            overlayEnabled
              ? "Stop painting the graph (restores the runtime heat-map)"
              : "Paint the graph with diff colours (replaces the heat-map)"
          }
        >
          {overlayEnabled ? "Hide overlay" : "Show overlay"}
        </button>
      </div>

      {/* Regression banner */}
      {regression && (
        <div
          style={{
            background: "#7f1d1d",
            color: "#fca5a5",
            borderRadius: "4px",
            padding: "0.3rem 0.5rem",
            fontSize: "0.7rem",
            marginBottom: "0.5rem",
          }}
        >
          Regression detected -- some nodes are hotter than baseline
        </div>
      )}

      {/* Status summary chips */}
      {counts !== null && (
        <div style={{ marginBottom: "0.4rem" }}>
          {(
            [
              "hotter",
              "new",
              "gone",
              "colder",
              "cold",
              "touched",
              "same",
            ] as DiffStatus[]
          ).map((s) => {
            const n = counts[s];
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

      {/* Top-5 hottest regressions */}
      {entries !== null && diffBaseline !== null && (
        <>
          <div style={SECTION_TITLE}>Top regressions</div>
          {hotterEntries.length === 0 ? (
            <p style={{ margin: 0, fontSize: "0.7rem" }}>
              None -- no regressions found.
            </p>
          ) : (
            <ul
              style={{
                listStyle: "none",
                margin: 0,
                padding: 0,
                fontSize: "0.7rem",
              }}
            >
              {hotterEntries.slice(0, 5).map((e) => (
                <li
                  key={e.nodeId}
                  style={{
                    padding: "0.2rem 0",
                    borderBottom: "1px solid var(--color-border, #333)",
                    display: "flex",
                    justifyContent: "space-between",
                    gap: "0.5rem",
                  }}
                >
                  <span
                    style={{
                      overflow: "hidden",
                      textOverflow: "ellipsis",
                      whiteSpace: "nowrap",
                      flex: 1,
                      fontFamily: "var(--font-mono)",
                      color: "#ef4444",
                    }}
                    title={e.nodeId}
                  >
                    {e.nodeId}
                  </span>
                  <span style={{ flexShrink: 0, color: "#ef4444" }}>
                    {e.countA} &rarr; {e.countB} (+{e.delta})
                  </span>
                </li>
              ))}
            </ul>
          )}
        </>
      )}

      {/* Top-5 cold nodes (vs-static only) */}
      {entries !== null && diffBaseline === null && (
        <>
          <div style={SECTION_TITLE}>Cold nodes (never called)</div>
          {coldEntries.length === 0 ? (
            <p style={{ margin: 0, fontSize: "0.7rem" }}>
              All nodes were touched.
            </p>
          ) : (
            <ul
              style={{
                listStyle: "none",
                margin: 0,
                padding: 0,
                fontSize: "0.7rem",
              }}
            >
              {coldEntries.slice(0, 5).map((e) => (
                <li
                  key={e.nodeId}
                  style={{
                    padding: "0.2rem 0",
                    borderBottom: "1px solid var(--color-border, #333)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    fontFamily: "var(--font-mono)",
                    color: "#f59e0b",
                  }}
                  title={e.nodeId}
                >
                  {e.nodeId}
                </li>
              ))}
              {coldEntries.length > 5 && (
                <li style={{ color: "var(--color-text-muted, #888)" }}>
                  and {coldEntries.length - 5} more
                </li>
              )}
            </ul>
          )}
        </>
      )}
    </div>
  );
}
