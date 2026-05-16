import { afterEach, describe, expect, it } from "vitest";
import {
  _resetHighlighterForTest,
  getHighlighter,
  highlightPython,
} from "./highlighter";

afterEach(() => {
  _resetHighlighterForTest();
});

describe("highlighter", () => {
  it("returns a highlighter instance with python loaded", async () => {
    const hl = await getHighlighter();
    expect(hl).toBeDefined();
    const langs = hl.getLoadedLanguages();
    expect(langs).toContain("python");
  });

  it("returns the same instance on repeated calls", async () => {
    const [a, b] = await Promise.all([getHighlighter(), getHighlighter()]);
    expect(a).toBe(b);
  });

  it("produces HTML containing shiki output for a python snippet", async () => {
    const html = await highlightPython("x = 1", true);
    expect(html).toContain("<pre");
    expect(html).toContain("x");
  });

  it("produces different output for dark vs light theme", async () => {
    const dark = await highlightPython("x = 1", true);
    const light = await highlightPython("x = 1", false);
    // Themes embed different background colors in the style attribute.
    expect(dark).not.toBe(light);
  });
});
