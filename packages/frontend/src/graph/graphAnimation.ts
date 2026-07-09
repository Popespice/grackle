/**
 * Pure animation math for phase 10.7's enter-pulse / exit-fade. No Sigma, no
 * timers, no DOM — GraphCanvas owns the rAF loop and the per-node timestamp
 * maps; this module only turns "how much time has elapsed" into "what scale
 * / what color".
 *
 * All colors are `#rrggbb` in and out — never oklch/hsl/CSS-var (ADR-0015:
 * Sigma 3.x `parseColor` silently maps unknown formats to black).
 */

export const ENTER_DURATION_MS = 600;
export const EXIT_DURATION_MS = 400;

/** Lerp target for an exiting node's color — the existing "dimmed" gray. */
export const EXIT_FADE_COLOR = "#cbd5e1";

/**
 * Lerp source for an entering node/edge's color (settles to the normal
 * cascade/kind color over ENTER_DURATION_MS). Echoes diff.ts's "new"
 * status green by coincidence, not by coupling — a fresh literal, not an
 * import, so the two features can't accidentally drift together.
 */
export const ENTER_FLASH_COLOR = "#22c55e";

function clamp01(t: number): number {
  return t < 0 ? 0 : t > 1 ? 1 : t;
}

/** Elapsed time since `t0`, as a [0, 1] fraction of `durationMs`. */
export function elapsedFraction(
  t0: number,
  now: number,
  durationMs: number
): number {
  if (durationMs <= 0) return 1;
  return clamp01((now - t0) / durationMs);
}

// easeOutBack, amplified (c1=3, not the canonical 1.70158) so the overshoot
// peak is an exact, easily-asserted 1.25x at the midpoint — a visible "pop"
// at small graph-node sizes.
const BACK_C1 = 3;
const BACK_C3 = BACK_C1 + 1;

/** Size multiplier for an entering node: 0 -> 1.25 (at t=0.5) -> 1. */
export function enterScale(t: number): number {
  const u = clamp01(t) - 1;
  return 1 + BACK_C3 * u * u * u + BACK_C1 * u * u;
}

/** Size multiplier for an exiting node: 1 -> 0, linear. */
export function exitScale(t: number): number {
  return 1 - clamp01(t);
}

/**
 * Symmetric pulse envelope: 0 at t=0, 1 at t=0.5, 0 at t=1 — a hump, not a
 * settle-to-1 curve like `enterScale`. Drives the edge-pulse "1 -> 3 -> 1"
 * size profile: an edge doesn't grow permanently, it just flashes.
 */
export function pulseEnvelope(t: number): number {
  return Math.sin(clamp01(t) * Math.PI);
}

function hexToRgb(hex: string): [number, number, number] {
  const clean = hex.replace("#", "");
  return [
    Number.parseInt(clean.slice(0, 2), 16),
    Number.parseInt(clean.slice(2, 4), 16),
    Number.parseInt(clean.slice(4, 6), 16),
  ];
}

function clampByte(n: number): number {
  return n < 0 ? 0 : n > 255 ? 255 : n;
}

function toHexByte(n: number): string {
  return Math.round(clampByte(n)).toString(16).padStart(2, "0");
}

/** Interpolate between two `#rrggbb` colors. Always returns `#rrggbb`. */
export function lerpHex(a: string, b: string, t: number): string {
  const x = clamp01(t);
  const [ar, ag, ab] = hexToRgb(a);
  const [br, bg, bb] = hexToRgb(b);
  return `#${toHexByte(ar + (br - ar) * x)}${toHexByte(ag + (bg - ag) * x)}${toHexByte(ab + (bb - ab) * x)}`;
}

/**
 * `true` when the user's OS/browser requests reduced motion. Animation
 * callers check this once per apply and skip straight to final state —
 * adds appear at full size, removes drop synchronously.
 */
export function prefersReducedMotion(): boolean {
  if (
    typeof window === "undefined" ||
    typeof window.matchMedia !== "function"
  ) {
    return false;
  }
  return window.matchMedia("(prefers-reduced-motion: reduce)").matches;
}

/**
 * Per-id animation start timestamps (ms, `performance.now()` clock). Kept
 * as plain Maps in a component ref by the caller — this module never
 * touches Sigma or graphology, only the bookkeeping.
 */
export interface AnimationState {
  entering: Map<string, number>;
  exiting: Map<string, number>;
  enteringEdges: Map<string, number>;
}

export function createAnimationState(): AnimationState {
  return { entering: new Map(), exiting: new Map(), enteringEdges: new Map() };
}

export function hasActiveAnimations(anim: AnimationState): boolean {
  return (
    anim.entering.size > 0 ||
    anim.exiting.size > 0 ||
    anim.enteringEdges.size > 0
  );
}

export interface DiffAnimationInput {
  addedNodes: string[];
  removedNodes: string[];
  addedEdges: string[];
}

/**
 * Fold an applyGraphDiff result into the animation state, resolving three
 * races that a fast-editing watch session hits routinely:
 *
 *  - a ghost that reappears (no longer in `removedNodes`) has its fade
 *    cancelled — it's a survivor again, coordinates already intact via
 *    applyGraphDiff's merge path;
 *  - a node still mid-pop-in that gets removed cuts over to fade-out
 *    immediately, rather than finishing the pop first;
 *  - a node ALREADY fading keeps its ORIGINAL start time. Re-pushes can
 *    arrive faster than EXIT_DURATION_MS (hash-gated to ~300ms, vs a
 *    400ms fade) — resetting the clock on every apply would mean a
 *    persistently-removed node never finishes fading.
 *
 * Returns `false` under reduced motion without touching any map — the
 * caller drops removed nodes immediately instead of fading them.
 */
export function recordDiffAnimations(
  anim: AnimationState,
  result: DiffAnimationInput,
  now: number
): boolean {
  if (prefersReducedMotion()) return false;

  const removedSet = new Set(result.removedNodes);
  for (const id of Array.from(anim.exiting.keys())) {
    if (!removedSet.has(id)) anim.exiting.delete(id);
  }
  for (const id of result.removedNodes) {
    if (anim.entering.delete(id)) {
      anim.exiting.set(id, now);
    } else if (!anim.exiting.has(id)) {
      anim.exiting.set(id, now);
    }
  }
  for (const id of result.addedNodes) anim.entering.set(id, now);
  for (const edgeKey of result.addedEdges) anim.enteringEdges.set(edgeKey, now);

  return hasActiveAnimations(anim);
}

/**
 * Advance animation state by one frame: prune settled entries. Returns the
 * node ids whose exit just completed — the caller must `live.dropNode`
 * them (this module never touches graphology) — and whether any animation
 * is still active (the caller schedules another frame iff true).
 */
export function tickAnimations(
  anim: AnimationState,
  now: number
): { settledExits: string[]; active: boolean } {
  const settledExits: string[] = [];

  for (const [id, t0] of anim.entering) {
    if (elapsedFraction(t0, now, ENTER_DURATION_MS) >= 1)
      anim.entering.delete(id);
  }
  for (const [id, t0] of anim.exiting) {
    if (elapsedFraction(t0, now, EXIT_DURATION_MS) >= 1) {
      anim.exiting.delete(id);
      settledExits.push(id);
    }
  }
  for (const [key, t0] of anim.enteringEdges) {
    if (elapsedFraction(t0, now, ENTER_DURATION_MS) >= 1)
      anim.enteringEdges.delete(key);
  }

  return { settledExits, active: hasActiveAnimations(anim) };
}
