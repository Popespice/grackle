import type { ArgValue, GraphNode } from "@grackle/shared-types";
import type { JSX } from "react";
import { useCallback, useMemo, useState } from "react";
import {
  causalPathAt,
  type Firing,
  firingsOf,
  MAX_FIRINGS,
  nearestFiring,
} from "../graph/causalPath";
import { useFullTrace } from "../graph/useFullTrace";
import { useGraphStore } from "../graph/useGraphStore";
import { useSeekablePrefixState } from "../graph/useSeekablePrefixState";

// ── Styling (mirrors ValueInspectorPanel / EdgeEvidencePanel idioms) ────────

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
  gap: "var(--space-2)",
  flexWrap: "wrap",
};

const SECTION_LABEL: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-2)",
  color: "var(--color-text-subtle)",
  textTransform: "uppercase",
  letterSpacing: "0.06em",
  fontSize: "0.9em",
};

const MUTED: React.CSSProperties = { color: "var(--color-text-subtle)" };

const CODE: React.CSSProperties = {
  fontFamily: "var(--font-mono)",
  color: "var(--color-accent)",
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

const HOP_BUTTON_STYLE: React.CSSProperties = {
  ...BUTTON_STYLE,
  padding: "0 var(--space-2)",
  flexShrink: 0,
  color: "var(--color-text-muted)",
};

function formatInt(n: number): string {
  return n.toLocaleString();
}

function clampStr(s: string, max: number): string {
  return s.length > max ? `${s.slice(0, max - 1)}…` : s;
}

/** A compact `(name=repr, …)` preview of a hop's captured args. Mirrors
 *  ValueInspectorPanel's `argsPreview` (private to that panel, so mirrored
 *  rather than imported — pure styling helper, not behavioral state). */
function argsPreview(args: ArgValue[] | undefined): string {
  if (!args || args.length === 0) return "()";
  return `(${args.map((a) => `${a.name}=${clampStr(a.repr, 28)}`).join(", ")})`;
}

/** The 1-based evidence line on the parent→hop edge, or null when absent.
 *  Mirrors EdgeEvidencePanel's private `edgeLine` helper (ADR-0026). */
function edgeLine(
  metadata: Record<string, unknown> | undefined
): number | null {
  const line = metadata?.line;
  return typeof line === "number" ? line : null;
}

function formatRelativeNs(tsNs: number, baseNs: number): string {
  const deltaMs = (tsNs - baseNs) / 1e6;
  return `+${deltaMs.toFixed(1)}ms`;
}

function Badge({
  label,
  title,
}: {
  label: string;
  title: string;
}): JSX.Element {
  return (
    <span
      title={title}
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: "0.9em",
        color: "var(--color-warning)",
        border: "1px solid var(--color-warning)",
        borderRadius: "var(--radius-sm)",
        padding: "0 4px",
        opacity: 0.85,
      }}
    >
      {label}
    </span>
  );
}

/**
 * CausalPathPanel — explanation layer: causal "why did this fire" path
 * (Phase 10.5, ADR-0026 §8). Selection-driven (unlike the playhead-driven
 * ValueInspectorPanel): pick a node, pick which firing (when it fired more
 * than once), and see the ancestor call chain root→…→THIS with the argument
 * values that drove each hop — fusing 10.2's captured values, 10.3's stack
 * reconstruction, and 10.4's edge evidence (the per-hop "call site" jump).
 *
 * Frontend-only, no wire-schema change. ADR-0007: every hook runs before the
 * early return; auto-wrapped in an ErrorBoundary by `SlotContainer`.
 */
export function CausalPathPanel(): JSX.Element | null {
  // ── ALL HOOKS FIRST (ADR-0007) ──────────────────────────────────────────
  const graph = useGraphStore((s) => s.graph);
  const selectedNodeId = useGraphStore((s) => s.selectedNodeId);
  const traceSessionId = useGraphStore((s) => s.traceSessionId);
  const traceSeekable = useGraphStore((s) => s.traceSeekable);
  const tracePlayhead = useGraphStore((s) => s.tracePlayhead);
  const setPlayhead = useGraphStore((s) => s.setPlayhead);
  const selectNode = useGraphStore((s) => s.selectNode);
  const setHighlightedNodes = useGraphStore((s) => s.setHighlightedNodes);
  const jumpToSourceLine = useGraphStore((s) => s.jumpToSourceLine);

  const full = useFullTrace();
  const prefixState = useSeekablePrefixState(full);

  const nodeById = useMemo(() => {
    const m = new Map<string, GraphNode>();
    for (const n of graph?.nodes ?? []) m.set(n.id, n);
    return m;
  }, [graph]);

  const graphNodeIds = useMemo(
    () => new Set(graph?.nodes.map((n) => n.id) ?? []),
    [graph]
  );

  // O(1)-lookup index of call-site lines (source → target → line), built once
  // per `graph` change instead of scanning graph.edges per hop per render
  // (mirrors EdgeEvidencePanel's memoized-lookup idiom). Nested maps keep
  // source/target as separate keys — a single concatenated `${source}->${target}`
  // key could alias two edges if a node id contained the "->" substring.
  // Only "call"/"cross_language_call" edges represent an invocation — an
  // import/inherit/implements edge sharing the same (source, target) pair must
  // never stand in as a call site. The first line-bearing match per
  // (source, target) wins, matching the prior per-hop scan's behavior
  // (documented parallel-edge approximation, ADR-0026 §8).
  const callEdgeLineIndex = useMemo(() => {
    const bySource = new Map<string, Map<string, number>>();
    for (const e of graph?.edges ?? []) {
      if (e.kind !== "call" && e.kind !== "cross_language_call") continue;
      const line = edgeLine(e.metadata);
      if (line === null) continue;
      let byTarget = bySource.get(e.source);
      if (!byTarget) {
        byTarget = new Map<string, number>();
        bySource.set(e.source, byTarget);
      }
      if (!byTarget.has(e.target)) byTarget.set(e.target, line);
    }
    return bySource;
  }, [graph]);

  const { firings, capped } = useMemo(
    () =>
      selectedNodeId
        ? firingsOf(full.events, selectedNodeId)
        : { firings: [] as Firing[], capped: false },
    [full.events, selectedNodeId]
  );

  // Sticky firing selection: re-derive the nearest-to-playhead default when
  // the SELECTION or SESSION changes, or when firings first become available
  // (e.g. after a lazy "Load call stack"). Otherwise the index is fully
  // user-controlled by the stepper — immune to playhead moves triggered by a
  // hop's own "time-travel" action, which must not silently swap which
  // firing this panel is showing. `traceSessionId` is part of the key so a
  // new session (same node still selected) re-defaults rather than carrying
  // the previous session's stepper index.
  const derivationKey = `${traceSessionId ?? ""}:${selectedNodeId ?? ""}:${firings.length > 0}`;
  const [prevDerivationKey, setPrevDerivationKey] = useState("");
  const [firingIndex, setFiringIndex] = useState(0);
  if (derivationKey !== prevDerivationKey) {
    setPrevDerivationKey(derivationKey);
    setFiringIndex(
      firings.length > 0 ? nearestFiring(firings, tracePlayhead) : 0
    );
  }
  const clampedIndex =
    firings.length === 0
      ? -1
      : Math.min(Math.max(firingIndex, 0), firings.length - 1);
  const firing: Firing | undefined =
    clampedIndex >= 0 ? firings[clampedIndex] : undefined;

  const path = useMemo(
    () =>
      firing
        ? causalPathAt(full.events, firing.callIndex, firing.threadId)
        : [],
    [full.events, firing]
  );

  const onPrevFiring = useCallback(
    () => setFiringIndex((i) => Math.max(0, i - 1)),
    []
  );
  const onNextFiring = useCallback(
    () => setFiringIndex((i) => Math.min(firings.length - 1, i + 1)),
    [firings.length]
  );

  const onSelectHop = useCallback(
    (nodeId: string) => {
      // Graph-guarded, per ValueInspectorPanel's onSelectFrame precedent —
      // selecting an unresolved/imported id would dim the whole Sigma view.
      if (!graphNodeIds.has(nodeId)) return;
      setHighlightedNodes(null);
      selectNode(nodeId);
    },
    [graphNodeIds, setHighlightedNodes, selectNode]
  );

  const callSiteFor = useCallback(
    (
      parentId: string,
      childId: string
    ): { path: string; line: number } | null => {
      const parentNode = nodeById.get(parentId);
      if (!parentNode) return null;
      const line = callEdgeLineIndex.get(parentId)?.get(childId);
      return line !== undefined ? { path: parentNode.path, line } : null;
    },
    [callEdgeLineIndex, nodeById]
  );

  // ── EARLY RETURN (after all hooks) ──────────────────────────────────────
  if (!graph || !selectedNodeId || traceSessionId === null) return null;

  const nodeLabel = nodeById.get(selectedNodeId)?.name ?? selectedNodeId;

  const renderBody = (): JSX.Element => {
    if (prefixState.status === "loading") {
      return <div style={MUTED}>Reconstructing call stack…</div>;
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
          Load call stack to see firings
        </button>
      );
    }

    if (firings.length === 0) {
      // A truncated seekable prefix may simply not reach this node's firings —
      // "did not fire" would be misleading. Distinguish the two.
      if (traceSeekable && full.truncated) {
        return (
          <div style={MUTED}>
            This node did not fire within the first{" "}
            {formatInt(full.events.length)} events — later events are not
            loaded.
          </div>
        );
      }
      return (
        <div style={MUTED}>This node did not fire in the loaded trace.</div>
      );
    }

    return (
      <>
        <div style={ROW_STYLE}>
          <button
            type="button"
            onClick={onPrevFiring}
            disabled={clampedIndex <= 0}
            title="Previous firing"
            style={BUTTON_STYLE}
          >
            ◀ firing
          </button>
          <span style={{ fontFamily: "var(--font-mono)" }}>
            {clampedIndex + 1} / {firings.length}
          </span>
          <button
            type="button"
            onClick={onNextFiring}
            disabled={clampedIndex >= firings.length - 1}
            title="Next firing"
            style={BUTTON_STYLE}
          >
            firing ▶
          </button>
          {firing && (
            <span style={{ ...MUTED, fontFamily: "var(--font-mono)" }}>
              {formatRelativeNs(
                firing.tsNs,
                full.events[0]?.ts_ns ?? firing.tsNs
              )}
              {firing.threadId !== full.events[0]?.thread_id
                ? ` · thread ${firing.threadId}`
                : ""}
            </span>
          )}
        </div>

        {capped && (
          <div style={{ color: "var(--color-warning)" }}>
            Showing the first {MAX_FIRINGS} firings for this node — later
            firings are not collected, and one nearer the current playhead may
            exist beyond them.
          </div>
        )}
        {traceSeekable && full.truncated && (
          <div style={{ color: "var(--color-warning)" }}>
            {firings.length} firing{firings.length === 1 ? "" : "s"} within the
            first {formatInt(full.events.length)} events — later firings not
            shown.
          </div>
        )}

        <ol
          style={{
            listStyle: "none",
            margin: 0,
            padding: 0,
            display: "flex",
            flexDirection: "column",
            gap: "2px",
          }}
        >
          {path.map((hop, i) => {
            const isThis = i === path.length - 1;
            const parent = i > 0 ? path[i - 1] : undefined;
            const site = parent ? callSiteFor(parent.nodeId, hop.nodeId) : null;
            const inGraph = graphNodeIds.has(hop.nodeId);
            const redacted = hop.args?.some((a) => a.redacted) ?? false;

            return (
              <li
                key={hop.callIndex}
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "var(--space-1)",
                  flexWrap: "wrap",
                  background: isThis ? "var(--color-surface)" : "transparent",
                  borderRadius: "var(--radius-sm)",
                  padding: "2px var(--space-2)",
                }}
              >
                <span
                  style={{ color: "var(--color-text-subtle)", flexShrink: 0 }}
                >
                  d{hop.depth}
                </span>
                <span
                  style={{
                    flexShrink: 0,
                    fontFamily: "var(--font-mono)",
                    color: "var(--color-text)",
                    fontWeight: isThis ? "bold" : "normal",
                  }}
                >
                  {hop.label}
                  {isThis ? " (THIS)" : ""}
                </span>
                <span
                  style={{
                    color: "var(--color-text-muted)",
                    fontFamily: "var(--font-mono)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                  }}
                >
                  {argsPreview(hop.args)}
                </span>
                {redacted && (
                  <Badge label="redacted" title="A captured arg was redacted" />
                )}
                <span style={{ flex: 1 }} />
                <button
                  type="button"
                  onClick={() => setPlayhead(hop.callIndex)}
                  title="Jump the playhead to this call"
                  style={HOP_BUTTON_STYLE}
                >
                  → time-travel
                </button>
                <button
                  type="button"
                  onClick={() => onSelectHop(hop.nodeId)}
                  disabled={!inGraph}
                  title={
                    inGraph
                      ? "Select node + jump to source"
                      : "Frame not in the static graph"
                  }
                  style={{
                    ...HOP_BUTTON_STYLE,
                    cursor: inGraph ? "pointer" : "default",
                  }}
                >
                  select
                </button>
                {site && (
                  <button
                    type="button"
                    onClick={() => jumpToSourceLine(site.path, site.line)}
                    title="Jump to the call site that invoked this hop"
                    style={HOP_BUTTON_STYLE}
                  >
                    ↳ call site {site.path}:{site.line}
                  </button>
                )}
              </li>
            );
          })}
        </ol>

        {!prefixState.captureSeen && (
          <div style={{ ...MUTED, fontStyle: "italic" }}>
            No captured values here. Re-run with{" "}
            <code style={CODE}>--capture-values</code> to inspect arguments at
            each hop.
          </div>
        )}
      </>
    );
  };

  return (
    <section aria-label="Causal path" style={PANEL_STYLE}>
      <div style={SECTION_LABEL}>
        <span>Causal path</span>
      </div>
      <div style={{ color: "var(--color-text)", wordBreak: "break-all" }}>
        {nodeLabel}
      </div>
      {renderBody()}
    </section>
  );
}
