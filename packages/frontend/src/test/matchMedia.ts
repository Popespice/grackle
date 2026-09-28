import { vi } from "vitest";

/**
 * A controllable `window.matchMedia` stub (campaign T11-7,
 * `docs/test-campaigns/phase-12.md`).
 *
 * jsdom has no `matchMedia`, so `setup.ts` installs one. The original stub
 * answered `matches: false` to every query, so no test could ever reach the
 * code that runs when the user DOES prefer reduced motion or a light color
 * scheme — the branches those checks exist for. This stub keeps that default
 * (nothing matches until a test says otherwise, so no existing test sees a
 * change) and lets a test name the queries that should match:
 *
 * ```ts
 * setMatchingMediaQueries(REDUCED_MOTION_QUERY);
 * expect(prefersReducedMotion()).toBe(true);
 * ```
 *
 * After every test `setup.ts` calls `resetMatchMedia()`, which clears the
 * matching set AND installs a fresh stub, so nothing one test did reaches the
 * next. Set a preference inside the test (or a `beforeEach`), not in
 * `beforeAll` — the per-test reset would clear it after the first test.
 *
 * Why a fresh stub, not just a cleared set: the stub is a `vi.fn`, so
 * `vi.spyOn(window, "matchMedia")` hands back that same function rather than
 * wrapping it, and a `mockImplementation` / `mockReturnValue` set through the
 * spy would stay on it for the rest of the file (`vi.restoreAllMocks()` does
 * not undo it). A test that instead swaps `window.matchMedia` for its own
 * function and puts the old one back with `onTestFinished` still works: that
 * hook runs after `afterEach`, so it restores the stub it saved, which reads
 * the same (by then cleared) matching set.
 *
 * Matching is by the query text with whitespace removed and case folded
 * (`"(prefers-reduced-motion:reduce)"` matches `REDUCED_MOTION_QUERY`, as it
 * would in a browser). No other media-query evaluation is done: a query
 * matches only when a test named it.
 *
 * The matching set lives on `globalThis` rather than in this module, so a test
 * that calls `vi.resetModules()` and re-imports this file still controls the
 * one stub `setup.ts` installed.
 */

export const REDUCED_MOTION_QUERY = "(prefers-reduced-motion: reduce)";
export const PREFERS_LIGHT_QUERY = "(prefers-color-scheme: light)";
export const PREFERS_DARK_QUERY = "(prefers-color-scheme: dark)";

const STATE_KEY = Symbol.for("grackle.test.matchMedia.matching");

type StubGlobal = typeof globalThis & { [STATE_KEY]?: Set<string> };

function normalize(query: string): string {
  return query.replace(/\s+/g, "").toLowerCase();
}

function matching(): Set<string> {
  const g = globalThis as StubGlobal;
  let set = g[STATE_KEY];
  if (set === undefined) {
    set = new Set<string>();
    g[STATE_KEY] = set;
  }
  return set;
}

/**
 * Make exactly these media queries match, replacing any earlier setting.
 * Called with no arguments it is the same as `resetMatchingMediaQueries()`.
 */
export function setMatchingMediaQueries(...queries: string[]): void {
  const set = matching();
  set.clear();
  for (const q of queries) set.add(normalize(q));
}

/** Back to the default: no media query matches. */
export function resetMatchingMediaQueries(): void {
  matching().clear();
}

/** Whether the stub currently answers `matches: true` for `query`. */
export function mediaQueryMatches(query: string): boolean {
  return matching().has(normalize(query));
}

/**
 * The `MediaQueryList` the stub returns. `matches` is a getter, so — like a
 * real `MediaQueryList` — a list obtained before a test changes the matching
 * set reports the new answer. No consumer in `src/` subscribes to `change`,
 * so the listener methods are inert mocks, as they were before T11-7.
 */
function createMediaQueryList(query: string): MediaQueryList {
  return {
    get matches() {
      return mediaQueryMatches(query);
    },
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  } as MediaQueryList;
}

/**
 * Install a fresh stub as `window.matchMedia`, replacing whatever is there.
 * `setup.ts` calls this when each test file loads, and again (through
 * `resetMatchMedia`) after every test. The property descriptor is the
 * pre-T11-7 one, unchanged, so `vi.spyOn(window, "matchMedia")` and
 * `vi.stubGlobal("matchMedia", …)` behave exactly as they did.
 */
export function installMatchMediaStub(): void {
  Object.defineProperty(window, "matchMedia", {
    writable: true,
    value: vi.fn().mockImplementation(createMediaQueryList),
  });
}

/**
 * The per-test reset `setup.ts` runs after every test: nothing matches, and
 * `window.matchMedia` is a fresh stub — so neither a preference nor an
 * implementation a test put on the stub (through `vi.spyOn`) survives it.
 */
export function resetMatchMedia(): void {
  resetMatchingMediaQueries();
  installMatchMediaStub();
}
