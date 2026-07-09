import { afterEach, describe, expect, it, vi } from "vitest";
import {
  type AnimationState,
  createAnimationState,
  elapsedFraction,
  enterScale,
  exitScale,
  hasActiveAnimations,
  lerpHex,
  prefersReducedMotion,
  pulseEnvelope,
  recordDiffAnimations,
  tickAnimations,
} from "./graphAnimation";

const HEX_RE = /^#[0-9a-f]{6}$/;

describe("elapsedFraction", () => {
  it("clamps to [0, 1]", () => {
    expect(elapsedFraction(0, -100, 600)).toBe(0); // before start
    expect(elapsedFraction(0, 0, 600)).toBe(0);
    expect(elapsedFraction(0, 300, 600)).toBeCloseTo(0.5, 10);
    expect(elapsedFraction(0, 600, 600)).toBe(1);
    expect(elapsedFraction(0, 10_000, 600)).toBe(1); // long after end
  });

  it("returns 1 for a non-positive duration rather than dividing by zero", () => {
    expect(elapsedFraction(0, 50, 0)).toBe(1);
    expect(Number.isFinite(elapsedFraction(0, 50, 0))).toBe(true);
  });
});

describe("enterScale", () => {
  it("starts at 0 and ends at 1", () => {
    expect(enterScale(0)).toBeCloseTo(0, 10);
    expect(enterScale(1)).toBeCloseTo(1, 10);
  });

  it("overshoots to exactly 1.25 at the midpoint", () => {
    expect(enterScale(0.5)).toBeCloseTo(1.25, 10);
  });

  it("clamps out-of-range input to the [0, 1] domain", () => {
    expect(enterScale(-1)).toBe(enterScale(0));
    expect(enterScale(2)).toBe(enterScale(1));
  });
});

describe("exitScale", () => {
  it("starts at 1 and ends at 0", () => {
    expect(exitScale(0)).toBe(1);
    expect(exitScale(1)).toBe(0);
  });

  it("is linear at the midpoint", () => {
    expect(exitScale(0.5)).toBeCloseTo(0.5, 10);
  });

  it("clamps out-of-range input to the [0, 1] domain", () => {
    expect(exitScale(-1)).toBe(1);
    expect(exitScale(2)).toBe(0);
  });
});

describe("lerpHex", () => {
  it("reproduces the endpoints exactly at t=0 and t=1", () => {
    expect(lerpHex("#000000", "#ffffff", 0)).toBe("#000000");
    expect(lerpHex("#000000", "#ffffff", 1)).toBe("#ffffff");
  });

  it("interpolates at the midpoint", () => {
    expect(lerpHex("#000000", "#ffffff", 0.5)).toBe("#808080");
  });

  it("always outputs #rrggbb, including at intermediate values", () => {
    for (const t of [0, 0.1, 0.25, 0.5, 0.75, 0.9, 1]) {
      expect(lerpHex("#6366f1", "#cbd5e1", t)).toMatch(HEX_RE);
    }
  });

  it("clamps out-of-range t into a valid color", () => {
    expect(lerpHex("#000000", "#ffffff", -1)).toBe("#000000");
    expect(lerpHex("#000000", "#ffffff", 2)).toBe("#ffffff");
  });
});

describe("prefersReducedMotion", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("returns false by default (matchMedia stubbed to matches:false in test setup)", () => {
    expect(prefersReducedMotion()).toBe(false);
  });

  it("returns true when matchMedia reports a reduced-motion preference", () => {
    const matchMedia = vi.fn().mockReturnValue({ matches: true });
    vi.stubGlobal("matchMedia", matchMedia);

    expect(prefersReducedMotion()).toBe(true);
    expect(matchMedia).toHaveBeenCalledWith("(prefers-reduced-motion: reduce)");
  });
});

describe("pulseEnvelope", () => {
  it("is 0 at the endpoints and 1 at the midpoint", () => {
    expect(pulseEnvelope(0)).toBeCloseTo(0, 10);
    expect(pulseEnvelope(1)).toBeCloseTo(0, 10);
    expect(pulseEnvelope(0.5)).toBeCloseTo(1, 10);
  });

  it("clamps out-of-range input to the [0, 1] domain", () => {
    expect(pulseEnvelope(-1)).toBe(pulseEnvelope(0));
    expect(pulseEnvelope(2)).toBe(pulseEnvelope(1));
  });
});

function reduceMotion(matches: boolean) {
  vi.stubGlobal("matchMedia", vi.fn().mockReturnValue({ matches }));
}

describe("recordDiffAnimations / tickAnimations", () => {
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("records added nodes/edges as entering and removed nodes as exiting", () => {
    const anim = createAnimationState();
    const active = recordDiffAnimations(
      anim,
      { addedNodes: ["a"], removedNodes: ["b"], addedEdges: ["e1"] },
      1000
    );

    expect(active).toBe(true);
    expect(anim.entering.get("a")).toBe(1000);
    expect(anim.exiting.get("b")).toBe(1000);
    expect(anim.enteringEdges.get("e1")).toBe(1000);
  });

  it("under reduced motion, touches nothing and returns false", () => {
    reduceMotion(true);
    const anim = createAnimationState();
    const active = recordDiffAnimations(
      anim,
      { addedNodes: ["a"], removedNodes: ["b"], addedEdges: ["e1"] },
      1000
    );

    expect(active).toBe(false);
    expect(hasActiveAnimations(anim)).toBe(false);
  });

  it("cancels a fade when a ghost reappears as a survivor", () => {
    const anim = createAnimationState();
    recordDiffAnimations(
      anim,
      { addedNodes: [], removedNodes: ["b"], addedEdges: [] },
      1000
    );
    expect(anim.exiting.has("b")).toBe(true);

    // b is no longer in removedNodes — it's back.
    recordDiffAnimations(
      anim,
      { addedNodes: [], removedNodes: [], addedEdges: [] },
      1100
    );

    expect(anim.exiting.has("b")).toBe(false);
  });

  it("cuts a mid-pop-in node over to fade-out when it's removed before settling", () => {
    const anim = createAnimationState();
    recordDiffAnimations(
      anim,
      { addedNodes: ["a"], removedNodes: [], addedEdges: [] },
      1000
    );
    expect(anim.entering.has("a")).toBe(true);

    recordDiffAnimations(
      anim,
      { addedNodes: [], removedNodes: ["a"], addedEdges: [] },
      1050
    );

    expect(anim.entering.has("a")).toBe(false);
    expect(anim.exiting.get("a")).toBe(1050);
  });

  it("does NOT reset an already-fading node's start time on a later apply", () => {
    const anim = createAnimationState();
    recordDiffAnimations(
      anim,
      { addedNodes: [], removedNodes: ["b"], addedEdges: [] },
      1000
    );
    expect(anim.exiting.get("b")).toBe(1000);

    // b is still removed on this next re-push, arriving before EXIT_DURATION_MS
    // has elapsed. A naive implementation would overwrite t0 to 1100, and a
    // persistently-removed node under continuous re-pushes would then never
    // finish fading.
    recordDiffAnimations(
      anim,
      { addedNodes: [], removedNodes: ["b"], addedEdges: [] },
      1100
    );

    expect(anim.exiting.get("b")).toBe(1000);
  });

  it("tickAnimations prunes settled entries and reports settled exits", () => {
    const anim = createAnimationState();
    anim.entering.set("a", 0);
    anim.exiting.set("b", 0);
    anim.enteringEdges.set("e1", 0);

    // Halfway through the shortest (exit) duration: nothing settled yet.
    const mid = tickAnimations(anim, 200);
    expect(mid.settledExits).toEqual([]);
    expect(mid.active).toBe(true);
    expect(anim.exiting.has("b")).toBe(true);

    // Past both durations: everything settles.
    const done = tickAnimations(anim, 10_000);
    expect(done.settledExits).toEqual(["b"]);
    expect(done.active).toBe(false);
    expect(anim.entering.size).toBe(0);
    expect(anim.exiting.size).toBe(0);
    expect(anim.enteringEdges.size).toBe(0);
  });

  it("hasActiveAnimations reflects any non-empty map", () => {
    const anim: AnimationState = createAnimationState();
    expect(hasActiveAnimations(anim)).toBe(false);
    anim.enteringEdges.set("e1", 0);
    expect(hasActiveAnimations(anim)).toBe(true);
  });
});
