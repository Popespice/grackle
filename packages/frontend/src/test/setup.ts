import "@testing-library/jest-dom/vitest";
import { vi } from "vitest";

Object.defineProperty(window, "matchMedia", {
  writable: true,
  value: vi.fn().mockImplementation((query: string) => ({
    matches: false,
    media: query,
    onchange: null,
    addListener: vi.fn(),
    removeListener: vi.fn(),
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
    dispatchEvent: vi.fn(),
  })),
});

// jsdom implements no layout, so Element.prototype.scrollIntoView is absent in
// some versions (it was on the Ubuntu CI leg, not local/Windows — an
// environment-dependent flake). SourceViewer scrolls the target line into view;
// any test whose target line resolves to a rendered element would otherwise
// throw "scrollIntoView is not a function". Stub it unconditionally so the
// behavior is deterministic across every jsdom build.
Element.prototype.scrollIntoView = vi.fn();
