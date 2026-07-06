import type { ArgValue, TraceEvent } from "@grackle/shared-types";
import { ancestorStackAt, type StackFrame } from "./ancestorStack";

/**
 * Causal-path derivation (Phase 10.5, ADR-0026 §8): "why did this node fire?"
 *
 * The ancestor stack at the exact moment a `call` event opens IS the causal
 * path for that firing — `ancestorStackAt(events, firing.callIndex)` replays
 * `events[0..=callIndex]` and returns `[root, …, THIS]` on the firing's thread,
 * each frame already carrying its opening call's `values.args` (ADR-0025).
 * This module adds the two things `ancestorStack.ts` doesn't provide:
 * enumerating a node's firings, and picking a sensible default one.
 *
 * **Load-bearing precondition**: `events` must be a TRUE PREFIX starting at
 * absolute index 0 (never a windowed slice with a non-zero start). Given that,
 * `causalPathAt(events, callIndex, …)` is correct for ANY `callIndex` in range
 * — including one from a truncated (>50k) `useFullTrace` prefix — because
 * replaying `[0, callIndex]` never touches events past `callIndex`, so
 * truncation past the cliff cannot affect it. Truncation only limits *which
 * firings can be enumerated*, never the correctness of a path for an
 * already-enumerated one. Do not "optimize" this to accept a windowed slice —
 * that would resurrect `ancestorStackAt`'s orphan-return case (see its
 * docstring) and silently break causal paths.
 */

/** One occurrence of a node firing (a `call` event for that node_id). */
export interface Firing {
  /** Absolute index of the `call` event in `events`. */
  callIndex: number;
  threadId: number;
  /** Always present (a required `TraceEvent` field) — the primary
   *  disambiguator between firings when `--capture-values` is off or args are
   *  identical across invocations. Never rely on args for disambiguation. */
  tsNs: number;
  /** Sampled args from this call's `values.args`; absent if uncaptured. */
  args?: ArgValue[];
}

/** Bound on enumerated firings for a single node — a hot helper called
 *  thousands of times must not materialize thousands of stepper states.
 *  `firingsOf` stops scanning as soon as the cap is hit (not a full-prefix
 *  scan with a truncated result) — cheaper than the cap alone implies, but
 *  it also means firings past this bound are simply never seen; see its
 *  `capped` return field. */
export const MAX_FIRINGS = 200;

export interface FiringsResult {
  /** Firings in call order, capped at `MAX_FIRINGS`. */
  firings: Firing[];
  /** True when more firings exist beyond `MAX_FIRINGS` in `events`. */
  capped: boolean;
}

/**
 * Enumerate every `call` event for `nodeId` in `events`, in order.
 * `firings` is `[]` when the node never fired in the given prefix — this
 * covers both "the trace just doesn't reach it yet" and "this is a pure
 * static-graph node that was never called" (e.g. an unresolved edge target).
 */
export function firingsOf(events: TraceEvent[], nodeId: string): FiringsResult {
  const firings: Firing[] = [];
  let capped = false;

  for (let i = 0; i < events.length; i++) {
    const ev = events[i];
    if (!ev) continue; // noUncheckedIndexedAccess guard
    if (ev.event !== "call" || ev.node_id !== nodeId) continue;

    if (firings.length >= MAX_FIRINGS) {
      capped = true;
      break;
    }
    const args = ev.values?.args;
    firings.push({
      callIndex: i,
      threadId: ev.thread_id,
      tsNs: ev.ts_ns,
      ...(args !== undefined ? { args } : {}),
    });
  }

  return { firings, capped };
}

/**
 * Index into `firings` of the firing nearest `playhead`, preferring the
 * latest firing at or before it ("prefer <="). If every firing is after
 * `playhead`, returns the earliest (index 0) rather than nothing.
 *
 * Contract: `firings` must be non-empty — callers check `firings.length > 0`
 * first (an empty result means "did not fire", handled before this is ever
 * called). Called on an empty array, returns `-1` as a defensive sentinel;
 * this is unreachable in normal use, not a supported input.
 */
export function nearestFiring(firings: Firing[], playhead: number): number {
  if (firings.length === 0) return -1;

  let best = 0;
  for (let i = 0; i < firings.length; i++) {
    const f = firings[i];
    if (f && f.callIndex <= playhead) {
      best = i;
    } else {
      break; // firings are in increasing callIndex order — no candidate past here.
    }
  }
  return best;
}

/**
 * The causal path for one firing: the open ancestor stack at the moment it
 * fired, root-first, THIS firing last. A thin, drift-guarded wrapper —
 * `causalPath.test.ts` cross-checks it against `ancestorStackAt` directly so
 * the two can never silently diverge.
 *
 * Returns `[]` only for a `threadId` with no frames in `events` — unreachable
 * for a `callIndex`/`threadId` pair taken from a real `Firing` (the firing's
 * own frame is always on top of its own thread's stack at its `callIndex`);
 * kept as defense-in-depth, not a supported "no path" input.
 */
export function causalPathAt(
  events: TraceEvent[],
  callIndex: number,
  threadId: number
): StackFrame[] {
  return ancestorStackAt(events, callIndex).byThread.get(threadId) ?? [];
}
