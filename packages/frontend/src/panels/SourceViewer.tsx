import type { JSX } from "react";
import { useEffect, useRef, useState } from "react";
import { useGraphStore } from "../graph/useGraphStore";
import { highlightPython } from "../source/highlighter";
import { useSource } from "../source/useSource";
import { useTheme } from "../theme/useTheme";

interface HighlightState {
  html: string | null;
  path: string | null;
}

function Skeleton(): JSX.Element {
  return (
    <div
      role="status"
      aria-label="Loading source"
      style={{
        padding: "var(--space-4)",
        display: "flex",
        flexDirection: "column",
        gap: "var(--space-2)",
      }}
    >
      {Array.from({ length: 8 }).map((_, i) => (
        <div
          // biome-ignore lint/suspicious/noArrayIndexKey: static skeleton rows
          key={i}
          style={{
            height: 12,
            borderRadius: 4,
            background: "var(--color-border)",
            width: `${60 + (i % 3) * 15}%`,
            opacity: 0.6,
          }}
        />
      ))}
    </div>
  );
}

export function SourceViewer(): JSX.Element | null {
  // All hooks must be at the top — no early returns before this block.
  const selectedNodeId = useGraphStore((s) => s.selectedNodeId);
  const graph = useGraphStore((s) => s.graph);
  const theme = useTheme((s) => s.theme);

  const [highlight, setHighlight] = useState<HighlightState>({
    html: null,
    path: null,
  });
  const lineRefs = useRef<Map<number, HTMLElement>>(new Map());
  const containerRef = useRef<HTMLDivElement>(null);

  // Derive path/line from store without conditioning on graph nullability.
  const node = graph?.nodes.find((n) => n.id === selectedNodeId) ?? null;
  const path = node?.path ?? null;
  const targetLine = node?.line ?? null;

  const sourceState = useSource(path);

  // Run highlighting whenever source or theme changes.
  useEffect(() => {
    if (sourceState.status !== "loaded") {
      setHighlight({ html: null, path: null });
      return;
    }
    let cancelled = false;
    highlightPython(sourceState.source, theme === "dark").then((html) => {
      if (!cancelled) setHighlight({ html, path: sourceState.path });
    });
    return () => {
      cancelled = true;
    };
  }, [sourceState, theme]);

  // Scroll target line into view once HTML is rendered.
  useEffect(() => {
    if (highlight.html === null || targetLine === null) return;
    const lineEl = lineRefs.current.get(targetLine);
    lineEl?.scrollIntoView({ block: "center", behavior: "smooth" });
  }, [highlight.html, targetLine]);

  // Conditional returns come after all hooks.
  if (!graph) return null;

  if (!selectedNodeId) {
    return (
      <div
        style={{
          width: 400,
          padding: "var(--space-6) var(--space-4)",
          borderLeft: "1px solid var(--color-border)",
          color: "var(--color-text-muted)",
          fontFamily: "var(--font-sans)",
          fontSize: "var(--text-sm)",
          display: "flex",
          alignItems: "center",
          justifyContent: "center",
          boxSizing: "border-box",
        }}
      >
        Click a node to view source.
      </div>
    );
  }

  return (
    <aside
      aria-label="Source viewer"
      ref={containerRef}
      style={{
        width: 400,
        borderLeft: "1px solid var(--color-border)",
        display: "flex",
        flexDirection: "column",
        overflow: "hidden",
        boxSizing: "border-box",
      }}
    >
      <div
        style={{
          padding: "var(--space-2) var(--space-4)",
          borderBottom: "1px solid var(--color-border)",
          fontFamily: "var(--font-mono)",
          fontSize: "var(--text-xs)",
          color: "var(--color-text-muted)",
          flexShrink: 0,
        }}
      >
        {path ?? "—"}
      </div>

      <div
        style={{
          flex: 1,
          overflowY: "auto",
          overflowX: "auto",
          position: "relative",
        }}
      >
        {sourceState.status === "loading" && <Skeleton />}

        {sourceState.status === "error" && (
          <div
            style={{
              padding: "var(--space-4)",
              color: "var(--color-text-muted)",
              fontFamily: "var(--font-sans)",
              fontSize: "var(--text-sm)",
            }}
          >
            Could not load source: {sourceState.reason}
          </div>
        )}

        {highlight.html !== null && (
          <HighlightedSource
            html={highlight.html}
            targetLine={targetLine}
            lineRefs={lineRefs}
          />
        )}
      </div>
    </aside>
  );
}

interface HighlightedSourceProps {
  html: string;
  targetLine: number | null;
  lineRefs: React.RefObject<Map<number, HTMLElement>>;
}

function HighlightedSource({
  html,
  targetLine,
  lineRefs,
}: HighlightedSourceProps): JSX.Element {
  const lines = splitIntoLines(html);

  return (
    <div
      style={{
        fontFamily: "var(--font-mono)",
        fontSize: "var(--text-xs)",
        lineHeight: 1.6,
        minWidth: "max-content",
      }}
    >
      {lines.map((lineHtml, idx) => {
        const lineNum = idx + 1;
        const isTarget = lineNum === targetLine;
        return (
          <div
            key={lineNum}
            ref={(el) => {
              if (el) {
                lineRefs.current.set(lineNum, el);
              } else {
                lineRefs.current.delete(lineNum);
              }
            }}
            style={{
              display: "flex",
              alignItems: "stretch",
              background: isTarget
                ? "color-mix(in srgb, var(--color-accent-bright) 15%, transparent)"
                : undefined,
              outline: isTarget
                ? "1px solid color-mix(in srgb, var(--color-accent-bright) 30%, transparent)"
                : undefined,
            }}
          >
            <span
              aria-hidden="true"
              style={{
                minWidth: 40,
                paddingRight: "var(--space-3)",
                paddingLeft: "var(--space-2)",
                color: "var(--color-text-subtle)",
                userSelect: "none",
                textAlign: "right",
                flexShrink: 0,
              }}
            >
              {lineNum}
            </span>
            <div
              className="annotation-marker"
              data-line={lineNum}
              // biome-ignore lint/security/noDangerouslySetInnerHtml: shiki-generated, server-side controlled HTML
              dangerouslySetInnerHTML={{ __html: lineHtml || " " }}
              style={{ flex: 1, paddingRight: "var(--space-4)" }}
            />
          </div>
        );
      })}
    </div>
  );
}

function splitIntoLines(shikiHtml: string): string[] {
  // Shiki wraps output in <pre><code>...</code></pre>. Each line is a <span class="line">.
  const lineRegex = /<span class="line">(.*?)<\/span>/gs;
  const lines: string[] = [];
  let match: RegExpExecArray | null;
  // biome-ignore lint/suspicious/noAssignInExpressions: idiomatic regex loop
  while ((match = lineRegex.exec(shikiHtml)) !== null) {
    lines.push(match[1] ?? "");
  }
  if (lines.length === 0) {
    const codeMatch = /<code[^>]*>([\s\S]*?)<\/code>/.exec(shikiHtml);
    const raw = codeMatch?.[1] ?? shikiHtml;
    return raw.split("\n");
  }
  return lines;
}
