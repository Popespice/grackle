import type { JSX } from "react";
import { countByKind, orphans, topByInDegree } from "../graph/stats";
import { useGraphStore } from "../graph/useGraphStore";

export function StatsPanel(): JSX.Element | null {
  const graph = useGraphStore((s) => s.graph);

  if (!graph) return null;

  const kinds = countByKind(graph);
  const top = topByInDegree(graph, 5);
  const orphanCount = orphans(graph).length;

  return (
    <div
      role="status"
      aria-label="Graph statistics"
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-4)",
        padding: "0 var(--space-4)",
        height: 32,
        borderTop: "1px solid var(--color-border)",
        background: "var(--color-surface-2)",
        fontFamily: "var(--font-sans)",
        fontSize: "var(--text-xs)",
        color: "var(--color-text-muted)",
        overflowX: "auto",
        flexShrink: 0,
        whiteSpace: "nowrap",
      }}
    >
      <span
        style={{
          color: "var(--color-text-subtle)",
          marginRight: "var(--space-1)",
        }}
      >
        Kinds:
      </span>
      {kinds.map(({ kind, count }) => (
        <span
          key={kind}
          style={{
            display: "inline-flex",
            alignItems: "center",
            gap: "var(--space-1)",
          }}
        >
          <span
            style={{
              width: 6,
              height: 6,
              borderRadius: "50%",
              background: `var(--color-node-${kind}, var(--color-accent))`,
              display: "inline-block",
            }}
          />
          <span style={{ color: "var(--color-text)" }}>{kind}</span>
          <span style={{ color: "var(--color-text-subtle)" }}>{count}</span>
        </span>
      ))}

      <span
        style={{
          width: 1,
          height: 16,
          background: "var(--color-border)",
          flexShrink: 0,
          margin: "0 var(--space-1)",
        }}
      />

      <span
        style={{
          color: "var(--color-text-subtle)",
          marginRight: "var(--space-1)",
        }}
      >
        Top:
      </span>
      {top
        .filter((e) => e.inDegree > 0)
        .slice(0, 3)
        .map((entry) => (
          <span key={entry.node.id}>
            <span
              style={{
                fontFamily: "var(--font-mono)",
                color: "var(--color-text)",
              }}
            >
              {entry.node.name}
            </span>
            <span style={{ color: "var(--color-text-subtle)" }}>
              ×{entry.inDegree}
            </span>
          </span>
        ))}

      <span
        style={{
          width: 1,
          height: 16,
          background: "var(--color-border)",
          flexShrink: 0,
          margin: "0 var(--space-1)",
        }}
      />

      <span>
        <span style={{ color: "var(--color-text-subtle)" }}>Orphans: </span>
        <span style={{ color: "var(--color-text)" }}>{orphanCount}</span>
      </span>
    </div>
  );
}
