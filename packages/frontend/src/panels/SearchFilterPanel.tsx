import { KNOWN_NODE_KINDS } from "@grackle/shared-types";
import type { JSX } from "react";
import { useEffect, useState } from "react";
import { isNodeVisible } from "../graph/matching";
import { useGraphStore } from "../graph/useGraphStore";

export function SearchFilterPanel(): JSX.Element | null {
  const graph = useGraphStore((s) => s.graph);
  const searchTerm = useGraphStore((s) => s.searchTerm);
  const hiddenKinds = useGraphStore((s) => s.hiddenKinds);
  const excludeGlobs = useGraphStore((s) => s.excludeGlobs);
  const setSearch = useGraphStore((s) => s.setSearch);
  const toggleKind = useGraphStore((s) => s.toggleKind);
  const setExcludes = useGraphStore((s) => s.setExcludes);

  // Local state for the textarea so mid-line edits don't lose the cursor
  const [globText, setGlobText] = useState(() => excludeGlobs.join("\n"));
  useEffect(() => {
    setGlobText(excludeGlobs.join("\n"));
  }, [excludeGlobs]);

  if (!graph) return null;

  const totalNodes = graph.nodes.length;
  const visibleNodes = graph.nodes.filter((n) =>
    isNodeVisible(n, { searchTerm, hiddenKinds, excludeGlobs })
  ).length;
  const hiddenCount = totalNodes - visibleNodes;

  return (
    <aside
      aria-label="Search and filter"
      style={{
        width: 240,
        padding: "var(--space-3) var(--space-4)",
        borderRight: "1px solid var(--color-border)",
        background: "var(--color-surface-2)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-4)",
        fontSize: "var(--text-sm)",
        fontFamily: "var(--font-sans)",
        color: "var(--color-text)",
        overflowY: "auto",
        height: "100%",
        boxSizing: "border-box",
      }}
    >
      <div>
        <label
          htmlFor="search-input"
          style={{
            display: "block",
            fontSize: "var(--text-xs)",
            color: "var(--color-text-subtle)",
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            marginBottom: "var(--space-1)",
          }}
        >
          Search
        </label>
        <input
          id="search-input"
          type="text"
          value={searchTerm}
          onChange={(e) => setSearch(e.target.value)}
          placeholder="name, path, or qualname…"
          style={{
            width: "100%",
            padding: "var(--space-1) var(--space-2)",
            background: "var(--color-surface)",
            border: "1px solid var(--color-border)",
            borderRadius: "var(--radius-sm)",
            color: "var(--color-text)",
            fontFamily: "var(--font-mono)",
            fontSize: "var(--text-xs)",
            boxSizing: "border-box",
          }}
        />
      </div>

      <div>
        <div
          style={{
            fontSize: "var(--text-xs)",
            color: "var(--color-text-subtle)",
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            marginBottom: "var(--space-1)",
          }}
        >
          Node kinds
        </div>
        {KNOWN_NODE_KINDS.map((kind) => {
          const hidden = hiddenKinds.has(kind);
          return (
            <label
              key={kind}
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-2)",
                cursor: "pointer",
                padding: "2px 0",
                color: hidden
                  ? "var(--color-text-subtle)"
                  : "var(--color-text)",
                opacity: hidden ? 0.6 : 1,
              }}
            >
              <input
                type="checkbox"
                checked={!hidden}
                onChange={() => toggleKind(kind)}
              />
              <span
                style={{
                  width: 8,
                  height: 8,
                  borderRadius: "50%",
                  background: `var(--color-node-${kind})`,
                  display: "inline-block",
                  flexShrink: 0,
                }}
              />
              <span>{kind}</span>
            </label>
          );
        })}
      </div>

      <div>
        <label
          htmlFor="exclude-globs"
          style={{
            display: "block",
            fontSize: "var(--text-xs)",
            color: "var(--color-text-subtle)",
            textTransform: "uppercase",
            letterSpacing: "0.08em",
            marginBottom: "var(--space-1)",
          }}
        >
          Exclude paths (globs)
        </label>
        <textarea
          id="exclude-globs"
          value={globText}
          onChange={(e) => setGlobText(e.target.value)}
          onBlur={() =>
            setExcludes(globText.split("\n").filter((g) => g.trim().length > 0))
          }
          placeholder={"tests/**\n**/conftest.py"}
          rows={3}
          style={{
            width: "100%",
            padding: "var(--space-1) var(--space-2)",
            background: "var(--color-surface)",
            border: "1px solid var(--color-border)",
            borderRadius: "var(--radius-sm)",
            color: "var(--color-text)",
            fontFamily: "var(--font-mono)",
            fontSize: "var(--text-xs)",
            resize: "vertical",
            boxSizing: "border-box",
          }}
        />
      </div>

      {hiddenCount > 0 ? (
        <div
          role="status"
          aria-label={`Hidden: ${hiddenCount} of ${totalNodes} nodes`}
          style={{
            fontSize: "var(--text-xs)",
            color: "var(--color-text-muted)",
            fontFamily: "var(--font-mono)",
          }}
        >
          {"Hidden: "}
          <span style={{ color: "var(--color-accent-bright)" }}>
            {hiddenCount}
          </span>
          {` / ${totalNodes}`}
        </div>
      ) : null}
    </aside>
  );
}
