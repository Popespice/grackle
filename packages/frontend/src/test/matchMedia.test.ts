/**
 * Guard-of-the-guard for the controllable matchMedia stub (campaign T11-7,
 * `docs/test-campaigns/phase-12.md`). Every reduced-motion / color-scheme
 * true-branch test in the suite rests on three properties of the stub, each
 * pinned here: the default is exactly the pre-T11-7 `matches: false`, a named
 * query matches (and only that query), and nothing a test sets reaches the
 * next test.
 */
import { describe, expect, it, onTestFinished, vi } from "vitest";
import {
  mediaQueryMatches,
  PREFERS_DARK_QUERY,
  PREFERS_LIGHT_QUERY,
  REDUCED_MOTION_QUERY,
  resetMatchingMediaQueries,
  setMatchingMediaQueries,
} from "./matchMedia";

describe("matchMedia stub — default", () => {
  it("answers matches:false to every query, with the queried media echoed", () => {
    for (const q of [
      REDUCED_MOTION_QUERY,
      PREFERS_LIGHT_QUERY,
      PREFERS_DARK_QUERY,
      "(min-width: 600px)",
    ]) {
      const mql = window.matchMedia(q);
      expect(mql.matches).toBe(false);
      expect(mql.media).toBe(q);
      expect(mql.onchange).toBeNull();
    }
  });

  it("is a vi.fn, so tests can still assert on the queries a consumer made", () => {
    expect(vi.isMockFunction(window.matchMedia)).toBe(true);
  });
});

describe("matchMedia stub — control", () => {
  it("matches exactly the named queries", () => {
    setMatchingMediaQueries(REDUCED_MOTION_QUERY);
    expect(window.matchMedia(REDUCED_MOTION_QUERY).matches).toBe(true);
    expect(window.matchMedia(PREFERS_LIGHT_QUERY).matches).toBe(false);
    expect(
      window.matchMedia("(prefers-reduced-motion: no-preference)").matches
    ).toBe(false);
  });

  it("replaces, rather than adds to, an earlier setting", () => {
    setMatchingMediaQueries(REDUCED_MOTION_QUERY);
    setMatchingMediaQueries(PREFERS_LIGHT_QUERY);
    expect(mediaQueryMatches(REDUCED_MOTION_QUERY)).toBe(false);
    expect(mediaQueryMatches(PREFERS_LIGHT_QUERY)).toBe(true);
  });

  it("can match several queries at once", () => {
    setMatchingMediaQueries(REDUCED_MOTION_QUERY, PREFERS_LIGHT_QUERY);
    expect(window.matchMedia(REDUCED_MOTION_QUERY).matches).toBe(true);
    expect(window.matchMedia(PREFERS_LIGHT_QUERY).matches).toBe(true);
    expect(window.matchMedia(PREFERS_DARK_QUERY).matches).toBe(false);
  });

  it("ignores whitespace and case, as a browser's media-query parser does", () => {
    setMatchingMediaQueries(REDUCED_MOTION_QUERY);
    expect(window.matchMedia("(prefers-reduced-motion:reduce)").matches).toBe(
      true
    );
    expect(
      window.matchMedia("( PREFERS-REDUCED-MOTION : REDUCE )").matches
    ).toBe(true);
  });

  it("reports a live answer on a list obtained before the setting changed", () => {
    const mql = window.matchMedia(PREFERS_LIGHT_QUERY);
    expect(mql.matches).toBe(false);
    setMatchingMediaQueries(PREFERS_LIGHT_QUERY);
    expect(mql.matches).toBe(true);
  });

  it("reset, and set() with no arguments, both return to the default", () => {
    setMatchingMediaQueries(REDUCED_MOTION_QUERY);
    resetMatchingMediaQueries();
    expect(window.matchMedia(REDUCED_MOTION_QUERY).matches).toBe(false);

    setMatchingMediaQueries(REDUCED_MOTION_QUERY);
    setMatchingMediaQueries();
    expect(window.matchMedia(REDUCED_MOTION_QUERY).matches).toBe(false);
  });

  it("survives vi.resetModules(): a re-imported helper drives the installed stub", async () => {
    vi.resetModules();
    const fresh = await import("./matchMedia");
    fresh.setMatchingMediaQueries(PREFERS_LIGHT_QUERY);
    expect(window.matchMedia(PREFERS_LIGHT_QUERY).matches).toBe(true);
  });
});

// These two run in declaration order (vitest's default within a file): the
// first sets a preference and never clears it; the second proves setup.ts's
// afterEach reset did. Without that reset, the second fails.
describe("matchMedia stub — no leak between tests", () => {
  it("(1 of 2) sets reduced motion and leaves it set", () => {
    setMatchingMediaQueries(REDUCED_MOTION_QUERY, PREFERS_LIGHT_QUERY);
    expect(window.matchMedia(REDUCED_MOTION_QUERY).matches).toBe(true);
  });

  it("(2 of 2) starts from the default again", () => {
    expect(window.matchMedia(REDUCED_MOTION_QUERY).matches).toBe(false);
    expect(window.matchMedia(PREFERS_LIGHT_QUERY).matches).toBe(false);
  });
});

// `vi.spyOn` on the stub returns the stub itself (it is already a mock), so an
// implementation set through the spy lands on the shared function. The
// per-test re-install is what keeps it from outliving the test.
describe("matchMedia stub — a spied override does not leak", () => {
  it("(1 of 2) overrides the stub through vi.spyOn and leaves it", () => {
    vi.spyOn(window, "matchMedia").mockImplementation(
      (query: string) => ({ matches: true, media: query }) as MediaQueryList
    );
    expect(window.matchMedia(PREFERS_LIGHT_QUERY).matches).toBe(true);
  });

  it("(2 of 2) gets the default stub back, and the helper drives it", () => {
    expect(window.matchMedia(PREFERS_LIGHT_QUERY).matches).toBe(false);
    setMatchingMediaQueries(REDUCED_MOTION_QUERY);
    expect(window.matchMedia(REDUCED_MOTION_QUERY).matches).toBe(true);
    expect(window.matchMedia(PREFERS_LIGHT_QUERY).matches).toBe(false);
  });
});

// The other pattern in the suite (GraphCanvas.test.tsx): swap the property for
// the test's own function and put the saved one back with onTestFinished,
// which runs after setup.ts's afterEach. The stub it restores must still work.
describe("matchMedia stub — a test that swaps window.matchMedia itself", () => {
  it("(1 of 2) swaps in its own matchMedia and restores it with onTestFinished", () => {
    const original = window.matchMedia;
    onTestFinished(() => {
      window.matchMedia = original;
    });
    window.matchMedia = (query: string) =>
      ({ matches: query.includes("prefers"), media: query }) as MediaQueryList;
    expect(window.matchMedia(REDUCED_MOTION_QUERY).matches).toBe(true);
  });

  it("(2 of 2) is back on a working stub: default false, the helper drives it", () => {
    expect(vi.isMockFunction(window.matchMedia)).toBe(true);
    expect(window.matchMedia(REDUCED_MOTION_QUERY).matches).toBe(false);
    setMatchingMediaQueries(REDUCED_MOTION_QUERY);
    expect(window.matchMedia(REDUCED_MOTION_QUERY).matches).toBe(true);
  });
});
