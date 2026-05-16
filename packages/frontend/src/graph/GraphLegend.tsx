import type { Graph } from "@grackle/shared-types";
import type { JSX } from "react";

interface LayoutStats {
  nodeCount: number;
  layoutMs: number;
}

interface GraphLegendProps {
  graph: Graph | null;
  hiddenKinds: ReadonlySet<string>;
  onToggleKind: (kind: string) => void;
  onShowAll: () => void;
  layoutStats?: LayoutStats;
}

const NODE_KINDS: { kind: string; label: string }[] = [
  { kind: "file", label: "File" },
  { kind: "class", label: "Class" },
  { kind: "function", label: "Function" },
  { kind: "method", label: "Method" },
];

const EDGE_KINDS: { kind: string; label: string }[] = [
  { kind: "import", label: "Import" },
  { kind: "call", label: "Call" },
  { kind: "inherit", label: "Inherits" },
];

export function GraphLegend({
  graph,
  hiddenKinds,
  onToggleKind,
  onShowAll,
  layoutStats,
}: GraphLegendProps): JSX.Element | null {
  if (!graph) return null;

  const kindCounts = new Map<string, number>();
  for (const n of graph.nodes) {
    kindCounts.set(n.kind, (kindCounts.get(n.kind) ?? 0) + 1);
  }

  const nodeCount = graph.nodes.length;
  const visibleNodeCount = graph.nodes.filter(
    (n) => !hiddenKinds.has(n.kind)
  ).length;
  const edgeCount = graph.edges.length;
  const anyHidden = hiddenKinds.size > 0;

  return (
    <aside
      style={{
        padding: "var(--space-3) var(--space-4)",
        background: "var(--color-surface-2)",
        borderLeft: "1px solid var(--color-border)",
        color: "var(--color-text)",
        fontFamily: "var(--font-sans)",
        fontSize: "var(--text-xs)",
        minWidth: 240,
        boxSizing: "border-box",
      }}
    >
      <div
        style={{
          textTransform: "uppercase",
          letterSpacing: "0.08em",
          color: "var(--color-text-subtle)",
          marginBottom: "var(--space-2)",
          fontSize: "10px",
        }}
      >
        {anyHidden ? (
          <>
            <span style={{ color: "var(--color-accent-bright)" }}>
              {visibleNodeCount.toLocaleString()}
            </span>
            <span> / {nodeCount.toLocaleString()} nodes</span>
          </>
        ) : (
          <span>{nodeCount.toLocaleString()} nodes</span>
        )}{" "}
        / {edgeCount.toLocaleString()} edges
        {layoutStats ? (
          <>
            {" · "}
            <span style={{ color: "var(--color-accent-bright)" }}>
              {layoutStats.layoutMs.toFixed(0)}ms layout
            </span>
          </>
        ) : null}
      </div>

      <div
        style={{
          display: "grid",
          gridTemplateColumns: "1fr 1fr",
          gap: "var(--space-2) var(--space-4)",
        }}
      >
        <div>
          <div
            style={{
              display: "flex",
              justifyContent: "space-between",
              alignItems: "baseline",
              color: "var(--color-text-muted)",
              marginBottom: "var(--space-1)",
            }}
          >
            <span>Nodes</span>
            {anyHidden ? (
              <button
                type="button"
                onClick={onShowAll}
                style={{
                  background: "transparent",
                  border: "none",
                  color: "var(--color-accent-bright)",
                  cursor: "pointer",
                  fontSize: "10px",
                  fontFamily: "var(--font-sans)",
                  textTransform: "uppercase",
                  letterSpacing: "0.08em",
                  padding: 0,
                }}
              >
                show all
              </button>
            ) : null}
          </div>
          {NODE_KINDS.map((k) => {
            const isHidden = hiddenKinds.has(k.kind);
            const count = kindCounts.get(k.kind) ?? 0;
            return (
              <button
                type="button"
                key={k.kind}
                onClick={() => onToggleKind(k.kind)}
                aria-pressed={!isHidden}
                aria-label={
                  isHidden
                    ? `show ${k.label.toLowerCase()} nodes`
                    : `hide ${k.label.toLowerCase()} nodes`
                }
                style={{
                  display: "flex",
                  alignItems: "center",
                  gap: 8,
                  marginBottom: 2,
                  background: "transparent",
                  border: "none",
                  color: isHidden
                    ? "var(--color-text-subtle)"
                    : "var(--color-text)",
                  opacity: isHidden ? 0.45 : 1,
                  cursor: "pointer",
                  padding: "1px 0",
                  fontFamily: "inherit",
                  fontSize: "inherit",
                  textAlign: "left",
                  width: "100%",
                  textDecoration: isHidden ? "line-through" : "none",
                }}
              >
                <span
                  style={{
                    width: 8,
                    height: 8,
                    borderRadius: "50%",
                    background: `var(--color-node-${k.kind})`,
                    opacity: isHidden ? 0.4 : 1,
                    display: "inline-block",
                    flexShrink: 0,
                  }}
                />
                <span>{k.label}</span>
                <span
                  style={{
                    marginLeft: "auto",
                    color: "var(--color-text-subtle)",
                    fontFamily: "var(--font-mono)",
                    fontSize: "10px",
                  }}
                >
                  {count}
                </span>
              </button>
            );
          })}
        </div>
        <div>
          <div
            style={{
              color: "var(--color-text-muted)",
              marginBottom: "var(--space-1)",
            }}
          >
            Edges
          </div>
          {EDGE_KINDS.map((k) => (
            <div
              key={k.kind}
              style={{
                display: "flex",
                alignItems: "center",
                gap: 8,
                marginBottom: 2,
              }}
            >
              <span
                style={{
                  width: 16,
                  height: 2,
                  background: `var(--color-edge-${k.kind})`,
                  display: "inline-block",
                  flexShrink: 0,
                }}
              />
              <span>{k.label}</span>
            </div>
          ))}
        </div>
      </div>
    </aside>
  );
}
