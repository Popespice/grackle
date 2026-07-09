import type { JSX } from "react";
import { useGrackleClient } from "../ws/client";

/** Demo-only: renders null outside the demo agent (no fixtures pushed). */
export function FixtureSwitcher(): JSX.Element | null {
  const fixtures = useGrackleClient((s) => s.availableFixtures);
  const active = useGrackleClient((s) => s.activeFixtureName);
  const loading = useGrackleClient((s) => s.isLoadingFixture);
  const loadFixture = useGrackleClient((s) => s.loadFixture);

  if (fixtures.length === 0) return null;

  return (
    <label
      style={{
        display: "flex",
        alignItems: "center",
        gap: "var(--space-2)",
        color: "var(--color-text-muted)",
        fontFamily: "var(--font-sans)",
        fontSize: "var(--text-xs)",
        textTransform: "uppercase",
        letterSpacing: "0.08em",
      }}
    >
      <span>fixture</span>
      <select
        value={active ?? ""}
        onChange={(e) => loadFixture(e.target.value)}
        disabled={loading}
        style={{
          background: "var(--color-surface-2)",
          color: "var(--color-text)",
          border: "1px solid var(--color-border-strong)",
          borderRadius: "var(--radius-md)",
          padding: "var(--space-1) var(--space-3)",
          fontFamily: "var(--font-mono)",
          fontSize: "var(--text-xs)",
          textTransform: "none",
          letterSpacing: "normal",
          cursor: loading ? "wait" : "pointer",
          opacity: loading ? 0.6 : 1,
          minWidth: 180,
        }}
      >
        {fixtures.map((f) => (
          <option key={f.name} value={f.name}>
            {f.label} — {f.nodeCount}n / {f.edgeCount}e
          </option>
        ))}
      </select>
      {loading ? (
        <span
          style={{
            color: "var(--color-accent-bright)",
            textTransform: "none",
            letterSpacing: "normal",
            fontFamily: "var(--font-mono)",
            fontSize: "var(--text-xs)",
          }}
        >
          loading…
        </span>
      ) : null}
    </label>
  );
}
