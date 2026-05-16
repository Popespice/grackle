import type { Highlighter } from "shiki";

let _promise: Promise<Highlighter> | null = null;

// Lazy singleton — dynamic import so Shiki is excluded from the initial bundle.
export async function getHighlighter(): Promise<Highlighter> {
  if (_promise) return _promise;
  _promise = (async () => {
    const { createHighlighter } = await import("shiki");
    return createHighlighter({
      langs: ["python"],
      themes: ["github-dark", "github-light"],
    });
  })();
  return _promise;
}

export async function highlightPython(
  source: string,
  dark: boolean
): Promise<string> {
  const hl = await getHighlighter();
  return hl.codeToHtml(source, {
    lang: "python",
    theme: dark ? "github-dark" : "github-light",
  });
}

// Reset for testing only
export function _resetHighlighterForTest(): void {
  _promise = null;
}
