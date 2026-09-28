/**
 * `prefers-color-scheme` true branch of `useTheme` (campaign T11-7,
 * `docs/test-campaigns/phase-12.md`).
 *
 * `getInitialTheme()` runs once, when the store module is first evaluated —
 * `useTheme.test.ts` only ever sees the copy imported under the default stub
 * (nothing matches → "dark"), so the OS-prefers-light branch had never run in
 * any test. Each test here sets the OS preference through the controllable
 * stub, then evaluates a FRESH copy of the module (`vi.resetModules()` + a
 * dynamic import) so the initializer actually runs under that preference.
 */
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import {
  PREFERS_DARK_QUERY,
  PREFERS_LIGHT_QUERY,
  setMatchingMediaQueries,
} from "../test/matchMedia";

async function freshTheme() {
  vi.resetModules();
  const mod = await import("./useTheme");
  return mod.useTheme;
}

beforeEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
  // Call history only — the stub's implementation stays installed.
  vi.mocked(window.matchMedia).mockClear();
});

afterEach(() => {
  localStorage.clear();
  document.documentElement.removeAttribute("data-theme");
});

describe("useTheme — initial theme from the OS color scheme", () => {
  it("starts light when the OS prefers light and nothing is stored", async () => {
    setMatchingMediaQueries(PREFERS_LIGHT_QUERY);
    const useTheme = await freshTheme();

    expect(useTheme.getState().theme).toBe("light");
    // The initializer applies what it chose: <html data-theme> drives the
    // CSS tokens, so a store saying "light" over a dark page is the bug class.
    expect(document.documentElement.getAttribute("data-theme")).toBe("light");
    expect(window.matchMedia).toHaveBeenCalledWith(PREFERS_LIGHT_QUERY);
  });

  it("starts dark when the OS preference does not match (the pre-T11-7 path)", async () => {
    const useTheme = await freshTheme();

    expect(useTheme.getState().theme).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("an OS dark preference alone does not make the theme light", async () => {
    // Discriminates "consults the light query" from "any color-scheme query
    // answering true means light".
    setMatchingMediaQueries(PREFERS_DARK_QUERY);
    const useTheme = await freshTheme();

    expect(useTheme.getState().theme).toBe("dark");
  });

  it("an explicitly stored theme wins over the OS preference", async () => {
    setMatchingMediaQueries(PREFERS_LIGHT_QUERY);
    localStorage.setItem("grackle:theme", "dark");
    const useTheme = await freshTheme();

    expect(useTheme.getState().theme).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("an unrecognised stored value is ignored in favour of the OS preference", async () => {
    setMatchingMediaQueries(PREFERS_LIGHT_QUERY);
    localStorage.setItem("grackle:theme", "sepia");
    const useTheme = await freshTheme();

    expect(useTheme.getState().theme).toBe("light");
  });

  it("toggle from an OS-derived light start goes to dark", async () => {
    setMatchingMediaQueries(PREFERS_LIGHT_QUERY);
    const useTheme = await freshTheme();

    useTheme.getState().toggle();
    expect(useTheme.getState().theme).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    expect(localStorage.getItem("grackle:theme")).toBe("dark");
  });
});
