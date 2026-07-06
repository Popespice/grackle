import type { GraphEdge, GraphNode } from "@grackle/shared-types";
import type { JSX } from "react";
import { useMemo } from "react";
import { useGraphStore } from "../graph/useGraphStore";
import { useSource } from "../source/useSource";

// ── Styling (mirrors ValueInspectorPanel idioms) ────────────────────────────

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

const ROW_BUTTON_STYLE: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  alignItems: "stretch",
  gap: "2px",
  width: "100%",
  textAlign: "left",
  padding: "var(--space-1) var(--space-2)",
  borderRadius: "var(--radius-sm)",
  border: "1px solid var(--color-border)",
  background: "var(--color-surface)",
  color: "var(--color-text-muted)",
  cursor: "pointer",
  fontFamily: "var(--font-sans)",
  fontSize: "var(--text-xs)",
};

const KIND_VERB: Record<string, string> = {
  import: "imports",
  call: "calls",
  inherit: "inherits",
  implements: "implements",
  cross_language_call: "calls (HTTP)",
  cross_language_spawn: "spawns",
};

function verbForKind(kind: string): string {
  return KIND_VERB[kind] ?? kind;
}

/** The 1-based edge-evidence line, or null when the adapter emitted none. */
function edgeLine(edge: GraphEdge): number | null {
  const line = edge.metadata?.line;
  return typeof line === "number" ? line : null;
}

function clampSnippet(s: string, max = 80): string {
  const t = s.trim();
  return t.length > max ? `${t.slice(0, max - 1)}…` : t;
}

interface EvidenceRow {
  key: string;
  edge: GraphEdge;
  direction: "in" | "out";
  /** Node ID (or bare unresolved name) of the OTHER endpoint. */
  otherId: string;
  /** POSIX path of the edge's SOURCE node file (where the evidence lives). */
  sourcePath: string | null;
  line: number | null;
}

export function EdgeEvidencePanel(): JSX.Element | null {
  // ── ALL HOOKS FIRST (ADR-0007) ──────────────────────────────────────────
  const graph = useGraphStore((s) => s.graph);
  const selectedEdge = useGraphStore((s) => s.selectedEdge);
  const selectedNodeId = useGraphStore((s) => s.selectedNodeId);
  const jumpToSourceLine = useGraphStore((s) => s.jumpToSourceLine);

  const nodeById = useMemo(() => {
    const m = new Map<string, GraphNode>();
    for (const n of graph?.nodes ?? []) m.set(n.id, n);
    return m;
  }, [graph]);

  // The one file whose source we fetch for inline snippets: the source node of
  // the picked edge, or the selected node (whose OUT-edges live in its file).
  // IN-edges originate in other files and show path:line without a snippet.
  const focusPath = useMemo((): string | null => {
    if (selectedEdge) return nodeById.get(selectedEdge.source)?.path ?? null;
    if (selectedNodeId) return nodeById.get(selectedNodeId)?.path ?? null;
    return null;
  }, [selectedEdge, selectedNodeId, nodeById]);

  const src = useSource(focusPath);
  const srcLines = useMemo(
    () => (src.status === "loaded" ? src.source.split("\n") : null),
    [src]
  );

  const rows = useMemo((): EvidenceRow[] => {
    if (!graph) return [];
    const pathOf = (id: string): string | null =>
      nodeById.get(id)?.path ?? null;
    if (selectedEdge) {
      return graph.edges
        .filter(
          (e) =>
            e.source === selectedEdge.source && e.target === selectedEdge.target
        )
        .map((e, i) => ({
          key: `e${i}`,
          edge: e,
          direction: "out" as const,
          otherId: e.target,
          sourcePath: pathOf(e.source),
          line: edgeLine(e),
        }));
    }
    if (selectedNodeId) {
      const out: EvidenceRow[] = graph.edges
        .filter((e) => e.source === selectedNodeId)
        .map((e, i) => ({
          key: `o${i}`,
          edge: e,
          direction: "out" as const,
          otherId: e.target,
          sourcePath: pathOf(e.source),
          line: edgeLine(e),
        }));
      const inc: EvidenceRow[] = graph.edges
        .filter((e) => e.target === selectedNodeId)
        .map((e, i) => ({
          key: `i${i}`,
          edge: e,
          direction: "in" as const,
          otherId: e.source,
          sourcePath: pathOf(e.source),
          line: edgeLine(e),
        }));
      return [...out, ...inc];
    }
    return [];
  }, [graph, selectedEdge, selectedNodeId, nodeById]);

  // ── EARLY RETURN (after all hooks) ──────────────────────────────────────
  if (!graph || (!selectedEdge && !selectedNodeId)) return null;

  const label = (id: string): string => nodeById.get(id)?.name ?? id;

  const snippetFor = (row: EvidenceRow): string | null => {
    if (
      row.line === null ||
      row.sourcePath !== focusPath ||
      srcLines === null
    ) {
      return null;
    }
    const raw = srcLines[row.line - 1];
    return raw ? clampSnippet(raw) : null;
  };

  const onJump = (row: EvidenceRow): void => {
    if (row.sourcePath && row.line !== null) {
      jumpToSourceLine(row.sourcePath, row.line);
    }
  };

  const heading = selectedEdge
    ? `${label(selectedEdge.source)} → ${label(selectedEdge.target)}`
    : `${label(selectedNodeId ?? "")} — ${rows.length} connection${rows.length === 1 ? "" : "s"}`;

  return (
    <section aria-label="Edge evidence" style={PANEL_STYLE}>
      <div style={SECTION_LABEL}>
        <span>Edge evidence</span>
      </div>
      <div style={{ color: "var(--color-text)", wordBreak: "break-all" }}>
        {heading}
      </div>

      {rows.length === 0 ? (
        <div style={MUTED}>No edges.</div>
      ) : (
        <ul
          style={{
            listStyle: "none",
            margin: 0,
            padding: 0,
            display: "flex",
            flexDirection: "column",
            gap: "var(--space-1)",
          }}
        >
          {rows.map((row) => {
            const snippet = snippetFor(row);
            const arrow = row.direction === "in" ? "←" : "→";
            const hasLine = row.line !== null && row.sourcePath !== null;
            return (
              <li key={row.key}>
                <button
                  type="button"
                  onClick={() => onJump(row)}
                  disabled={!hasLine}
                  title={
                    hasLine
                      ? "Jump to the source line for this edge"
                      : "No source line available for this edge"
                  }
                  style={{
                    ...ROW_BUTTON_STYLE,
                    cursor: hasLine ? "pointer" : "default",
                    opacity: hasLine ? 1 : 0.7,
                  }}
                >
                  <span
                    style={{
                      display: "flex",
                      alignItems: "baseline",
                      gap: "var(--space-1)",
                      flexWrap: "wrap",
                    }}
                  >
                    <span style={MUTED}>{arrow}</span>
                    <span style={MUTED}>{verbForKind(row.edge.kind)}</span>
                    <span
                      style={{
                        color: "var(--color-text)",
                        fontFamily: "var(--font-mono)",
                      }}
                    >
                      {label(row.otherId)}
                    </span>
                  </span>
                  {snippet !== null && (
                    <code
                      style={{
                        fontFamily: "var(--font-mono)",
                        color: "var(--color-accent)",
                        wordBreak: "break-all",
                      }}
                    >
                      {snippet}
                    </code>
                  )}
                  <span style={{ ...MUTED, fontFamily: "var(--font-mono)" }}>
                    {`${row.sourcePath ?? "?"}${row.line !== null ? `:${row.line}` : ""}`}
                  </span>
                </button>
              </li>
            );
          })}
        </ul>
      )}
    </section>
  );
}
