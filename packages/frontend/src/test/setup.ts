import "@testing-library/jest-dom/vitest";
import { afterEach, vi } from "vitest";
import { installMatchMediaStub, resetMatchMedia } from "./matchMedia";

// jsdom has no matchMedia. By default the stub answers `matches: false` to
// every query; a test that needs a preference to hold (reduced motion, a light
// color scheme) names it with `setMatchingMediaQueries` from `./matchMedia`
// (campaign T11-7). The per-test reset clears that preference and installs a
// fresh stub, so neither it nor anything a test did to the stub leaks forward.
installMatchMediaStub();
afterEach(() => {
  resetMatchMedia();
});

// jsdom implements no layout, so Element.prototype.scrollIntoView is absent in
// some versions (it was on the Ubuntu CI leg, not local/Windows — an
// environment-dependent flake). SourceViewer scrolls the target line into view;
// any test whose target line resolves to a rendered element would otherwise
// throw "scrollIntoView is not a function". Stub it unconditionally so the
// behavior is deterministic across every jsdom build.
Element.prototype.scrollIntoView = vi.fn();
