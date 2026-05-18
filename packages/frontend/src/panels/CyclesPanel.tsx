import type { JSX } from "react";
import { useState } from "react";
import type { CycleEntry } from "../graph/analysis";
import { useAnalysis } from "../graph/analysis";
import { useGraphStore } from "../graph/useGraphStore";

function nodeLabel(id: string): string {
  // Last segment after the last ":" or "/" is the most readable short name.
  const colonPart = id.split(":").pop() ?? id;
  return colonPart.split("/").pop() ?? colonPart;
}

export function CyclesPanel(): JSX.Element | null {
  const cycles = useAnalysis<CycleEntry[]>("cycles");
  const graph = useGraphStore((s) => s.graph);
  const highlightedNodeIds = useGraphStore((s) => s.highlightedNodeIds);
  const setHighlightedNodes = useGraphStore((s) => s.setHighlightedNodes);
  const [expandedId, setExpandedId] = useState<string | null>(null);

  if (!graph || !cycles || cycles.length === 0) return null;

  // Build id→name map for display
  const nameOf = new Map(graph.nodes.map((n) => [n.id, n.name]));

  function handleCycleClick(cycle: CycleEntry) {
    const isActive =
      highlightedNodeIds !== null &&
      cycle.nodes.every((id) => highlightedNodeIds.has(id)) &&
      highlightedNodeIds.size === cycle.nodes.length;
    setHighlightedNodes(isActive ? null : cycle.nodes);
    setExpandedId(isActive ? null : cycle.id);
  }

  return (
    <section
      aria-label="Cycles"
      style={{
        padding: "var(--space-3) var(--space-4)",
        borderTop: "1px solid var(--color-border)",
        background: "var(--color-surface-2)",
        fontFamily: "var(--font-sans)",
        fontSize: "var(--text-xs)",
        color: "var(--color-text-muted)",
      }}
    >
      <div
        style={{
          color: "var(--color-text-subtle)",
          marginBottom: "var(--space-2)",
          fontWeight: 500,
        }}
      >
        Cycles ({cycles.length})
      </div>
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
        {cycles.map((cycle) => {
          const isActive =
            highlightedNodeIds !== null &&
            cycle.nodes.every((id) => highlightedNodeIds.has(id)) &&
            highlightedNodeIds.size === cycle.nodes.length;
          const isExpanded = expandedId === cycle.id;
          const preview = cycle.nodes
            .slice(0, 3)
            .map((id) => nameOf.get(id) ?? nodeLabel(id));
          const hasMore = cycle.nodes.length > 3;

          return (
            <li key={cycle.id}>
              <button
                type="button"
                onClick={() => handleCycleClick(cycle)}
                style={{
                  width: "100%",
                  textAlign: "left",
                  background: isActive
                    ? "oklch(72% 0.2 40 / 0.15)"
                    : "transparent",
                  border: isActive
                    ? "1px solid var(--color-highlight-cycle)"
                    : "1px solid transparent",
                  borderRadius: "var(--radius-sm)",
                  padding: "var(--space-1) var(--space-2)",
                  cursor: "pointer",
                  color: "var(--color-text)",
                  fontFamily: "var(--font-sans)",
                  fontSize: "var(--text-xs)",
                  display: "flex",
                  alignItems: "baseline",
                  gap: "var(--space-2)",
                }}
              >
                <span
                  style={{
                    color: isActive
                      ? "var(--color-highlight-cycle)"
                      : "var(--color-text-subtle)",
                    fontVariantNumeric: "tabular-nums",
                    minWidth: "1.5ch",
                  }}
                >
                  {cycle.size}
                </span>
                <span
                  style={{
                    fontFamily: "var(--font-mono)",
                    color: "var(--color-text-muted)",
                    overflow: "hidden",
                    textOverflow: "ellipsis",
                    whiteSpace: "nowrap",
                    flex: 1,
                  }}
                >
                  {preview.join(" → ")}
                  {hasMore && (
                    <span style={{ color: "var(--color-text-subtle)" }}>
                      {" "}
                      +{cycle.nodes.length - 3}
                    </span>
                  )}
                </span>
              </button>
              {isExpanded && isActive && cycle.nodes.length > 3 && (
                <ul
                  style={{
                    listStyle: "none",
                    margin: "var(--space-1) 0 0 var(--space-4)",
                    padding: 0,
                    display: "flex",
                    flexDirection: "column",
                    gap: 2,
                  }}
                >
                  {cycle.nodes.slice(3).map((id) => (
                    <li
                      key={id}
                      style={{
                        fontFamily: "var(--font-mono)",
                        fontSize: "var(--text-xs)",
                        color: "var(--color-text-muted)",
                      }}
                    >
                      {nameOf.get(id) ?? nodeLabel(id)}
                    </li>
                  ))}
                </ul>
              )}
            </li>
          );
        })}
      </ul>
    </section>
  );
}
