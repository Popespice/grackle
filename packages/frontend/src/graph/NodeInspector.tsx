import type { GraphNode } from "@grackle/shared-types";
import type { JSX } from "react";

interface NodeInspectorProps {
  node: GraphNode | null;
  onClose?: () => void;
}

export function NodeInspector({
  node,
  onClose,
}: NodeInspectorProps): JSX.Element | null {
  if (!node) return null;

  const meta = (node.metadata ?? {}) as Record<string, unknown>;
  const parent = typeof meta.parent === "string" ? meta.parent : null;
  const decorators = Array.isArray(meta.decorators)
    ? (meta.decorators as string[])
    : [];

  return (
    <aside
      aria-label="Node inspector"
      style={{
        width: 340,
        padding: "var(--space-4)",
        background: "var(--color-surface-2)",
        borderLeft: "1px solid var(--color-border)",
        borderBottom: "1px solid var(--color-border)",
        color: "var(--color-text)",
        fontFamily: "var(--font-sans)",
        fontSize: "var(--text-sm)",
        boxSizing: "border-box",
      }}
    >
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "baseline",
          marginBottom: "var(--space-3)",
        }}
      >
        <span
          style={{
            fontSize: "var(--text-xs)",
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            color: `var(--color-node-${node.kind}, var(--color-text-subtle))`,
          }}
        >
          {node.kind}
        </span>
        <button
          type="button"
          onClick={onClose}
          style={{
            background: "transparent",
            border: "none",
            color: "var(--color-text-muted)",
            cursor: "pointer",
            fontSize: "var(--text-lg)",
            lineHeight: 1,
            padding: 0,
          }}
          aria-label="Close inspector"
        >
          ×
        </button>
      </div>
      <h2
        style={{
          margin: 0,
          fontSize: "var(--text-xl)",
          fontFamily: "var(--font-mono)",
          color: "var(--color-text)",
        }}
      >
        {node.name}
      </h2>
      <div
        style={{
          fontFamily: "var(--font-mono)",
          fontSize: "var(--text-xs)",
          color: "var(--color-text-muted)",
          marginTop: "var(--space-1)",
          wordBreak: "break-all",
        }}
      >
        {node.path}
        {node.line !== undefined ? `:${node.line}` : null}
      </div>

      <dl
        style={{
          display: "grid",
          gridTemplateColumns: "max-content 1fr",
          gap: "var(--space-2) var(--space-3)",
          marginTop: "var(--space-4)",
          marginBottom: 0,
        }}
      >
        <dt style={{ color: "var(--color-text-subtle)" }}>id</dt>
        <dd
          style={{
            margin: 0,
            fontFamily: "var(--font-mono)",
            fontSize: "var(--text-xs)",
            wordBreak: "break-all",
          }}
        >
          {node.id}
        </dd>
        {parent ? (
          <>
            <dt style={{ color: "var(--color-text-subtle)" }}>parent</dt>
            <dd
              style={{
                margin: 0,
                fontFamily: "var(--font-mono)",
                fontSize: "var(--text-xs)",
                wordBreak: "break-all",
              }}
            >
              {parent}
            </dd>
          </>
        ) : null}
        {decorators.length > 0 ? (
          <>
            <dt style={{ color: "var(--color-text-subtle)" }}>decorators</dt>
            <dd
              style={{
                margin: 0,
                fontFamily: "var(--font-mono)",
                fontSize: "var(--text-xs)",
              }}
            >
              {decorators.map((d) => `@${d}`).join(", ")}
            </dd>
          </>
        ) : null}
      </dl>
    </aside>
  );
}
