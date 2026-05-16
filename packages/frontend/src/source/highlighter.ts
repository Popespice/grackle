import type { HighlighterCore } from "shiki/core";

let _promise: Promise<HighlighterCore> | null = null;

// Lazy singleton — dynamic import keeps Shiki out of the initial bundle, and
// `shiki/core` + explicit lang/theme imports prevents Vite from emitting one
// chunk per bundled language (otherwise ~300 unused language files end up in
// dist/). Budget per the Phase 3 plan: ~600 KB total for python + 2 themes.
export async function getHighlighter(): Promise<HighlighterCore> {
  if (_promise) return _promise;
  _promise = (async () => {
    const [
      { createHighlighterCore },
      { createJavaScriptRegexEngine },
      python,
      dark,
      light,
    ] = await Promise.all([
      import("shiki/core"),
      import("shiki/engine/javascript"),
      import("shiki/langs/python.mjs"),
      import("shiki/themes/github-dark.mjs"),
      import("shiki/themes/github-light.mjs"),
    ]);
    return createHighlighterCore({
      engine: createJavaScriptRegexEngine(),
      langs: [python.default],
      themes: [dark.default, light.default],
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
