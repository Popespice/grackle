import type { GraphNode } from "@grackle/shared-types";
import type { JSX } from "react";
import type { CycleEntry, HubEntry } from "../graph/analysis";
import { useAnalysis } from "../graph/analysis";
import type { DegreeEntry, KindCount } from "../graph/stats";
import { useGraphStore } from "../graph/useGraphStore";
import { useRuntimeCoverage } from "../graph/useRuntimeCoverage";

const _sep = (
  <span
    style={{
      width: 1,
      height: 16,
      background: "var(--color-border)",
      flexShrink: 0,
      margin: "0 var(--space-1)",
    }}
  />
);

export function StatsPanel(): JSX.Element | null {
  const graph = useGraphStore((s) => s.graph);
  const traceEvents = useGraphStore((s) => s.traceEvents);
  const traceSeekable = useGraphStore((s) => s.traceSeekable);
  const traceTotal = useGraphStore((s) => s.traceTotal);
  const kinds = useAnalysis<KindCount[]>("count-by-kind");
  const top = useAnalysis<DegreeEntry[]>("top-in-degree");
  const orphanList = useAnalysis<GraphNode[]>("orphans");
  const hubs = useAnalysis<HubEntry[]>("hub-score");
  const cycles = useAnalysis<CycleEntry[]>("cycles");
  const coverage = useRuntimeCoverage();

  if (!graph) return null;

  const orphanCount = orphanList?.length ?? 0;
  const cycleCount = cycles?.length ?? 0;
  const httpEdges = graph.edges.filter(
    (e) => e.kind === "cross_language_call"
  ).length;
  const spawnEdges = graph.edges.filter(
    (e) => e.kind === "cross_language_spawn"
  ).length;
  const crossLangTotal = httpEdges + spawnEdges;

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
      {kinds?.map(({ kind, count }) => (
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

      {_sep}

      <span
        style={{
          color: "var(--color-text-subtle)",
          marginRight: "var(--space-1)",
        }}
      >
        Top:
      </span>
      {top
        ?.filter((e) => e.inDegree > 0)
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

      {_sep}

      <span>
        <span style={{ color: "var(--color-text-subtle)" }}>Orphans: </span>
        <span style={{ color: "var(--color-text)" }}>{orphanCount}</span>
      </span>

      {_sep}

      <span>
        <span style={{ color: "var(--color-text-subtle)" }}>Cycles: </span>
        <span style={{ color: "var(--color-text)" }}>{cycleCount}</span>
      </span>

      {crossLangTotal > 0 && (
        <>
          {_sep}
          <span>
            <span style={{ color: "var(--color-text-subtle)" }}>
              Cross-language:{" "}
            </span>
            <span style={{ color: "var(--color-text)" }}>{crossLangTotal}</span>
            <span style={{ color: "var(--color-text-subtle)" }}>
              {" "}
              ({httpEdges} HTTP, {spawnEdges} subprocess)
            </span>
          </span>
        </>
      )}

      {traceEvents.length > 0 && coverage && (
        <>
          {_sep}
          <span>
            <span style={{ color: "var(--color-text-subtle)" }}>Runtime: </span>
            <span style={{ color: "var(--color-text)" }}>
              {traceSeekable ? traceTotal : traceEvents.length}
            </span>
            <span style={{ color: "var(--color-text-subtle)" }}>
              {" "}
              events ·{" "}
            </span>
            <span style={{ color: "var(--color-text)" }}>
              {coverage.touchedCount}
            </span>
            <span style={{ color: "var(--color-text-subtle)" }}>
              {" "}
              touched ·{" "}
            </span>
            <span style={{ color: "var(--color-text)" }}>
              {coverage.hotCount}
            </span>
            <span style={{ color: "var(--color-text-subtle)" }}> hot</span>
          </span>
        </>
      )}

      {_sep}

      <span
        style={{
          color: "var(--color-text-subtle)",
          marginRight: "var(--space-1)",
        }}
      >
        Hub:
      </span>
      {hubs
        ?.filter((e) => e.score > 0)
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
              +{entry.score}
            </span>
          </span>
        ))}
    </div>
  );
}
