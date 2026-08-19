import type { TraceEvent } from "@grackle/shared-types";
import { matchesBeaconMethod, matchesBeaconNode } from "./beaconNode";
import { lastAtOrBefore } from "./playheadLookup";
import type { ScanStep } from "./useAppendOnlyScan";

/**
 * Playhead -> network-animation-phase derivation from a raw trace-event
 * array (Phase 12.4).
 *
 * One left-to-right pass over the events building an ordered list of
 * `ActivitySegment`s, each marking the event index at which a new animation
 * phase becomes active. Pure function over an already-received event array —
 * no wire messages, mirroring `epochSeries.ts` / `layerStats.ts`.
 *
 * ## Why frames are closed by depth, not by their `return` event
 *
 * "Is `Sequential.forward` currently open" cannot be a flag set on `call` and
 * cleared on `return`: the tracer emits **no return event at all** for a frame
 * that exits via an exception (`tracer.py`'s `PY_UNWIND` callback is depth
 * bookkeeping only, and the `"exception"` event `RAISE` emits also fires for
 * exceptions that are caught locally, so it cannot stand in for one). A single
 * raise anywhere under `evaluate()` — `SoftmaxCrossEntropy.backward`'s
 * "backward called before forward", any numpy shape error, a diverged run —
 * would latch the flag true and mislabel every later forward pass as
 * `forward-eval` for the rest of the trace. Crashed and partial traces are
 * first-class input since Phase 12.0's incremental `.part` writer.
 *
 * `frame_depth` IS correct across both exits: `_on_unwind` mirrors
 * `_on_return`'s decrement precisely so that "a clean return and an exception
 * unwind leave the depth counter in the same state". A frame opened by a
 * `call` at depth D is therefore open exactly while later events on the same
 * thread report a depth greater than D — its own `return` carries D again, and
 * after an unwind the next event on that thread is necessarily at depth <= D.
 */

export type ActivityPhase =
  | "idle"
  | "forward-train"
  | "forward-eval"
  | "loss"
  | "loss-grad"
  | "backward"
  | "update"
  | "reset";

export interface ActivitySegment {
  /** Event-array index at which this segment becomes active. */
  fromIndex: number;
  phase: ActivityPhase;
  /** Index into `NetworkSpec.tokens` the sweep currently highlights (a
   *  layer-forward/backward call). `-1` when the phase has no single
   *  associated token (loss, loss-grad, update, reset, idle). */
  activeToken: number;
}

/** A frame known to be open: the depth its `call` event reported, and the
 *  thread it belongs to (depths are per-thread, so an event from another
 *  thread says nothing about whether this frame has exited). */
interface OpenFrame {
  depth: number;
  threadId: number;
}

/** Resumable state of the left-to-right walk — see `useAppendOnlyScan`. */
export interface ActivityScanState {
  forward: OpenFrame | null;
  backward: OpenFrame | null;
  evaluate: OpenFrame | null;
  forwardCount: number;
  backwardCount: number;
}

const IDLE_SEGMENT: ActivitySegment = {
  fromIndex: -1,
  phase: "idle",
  activeToken: -1,
};

function initialState(): ActivityScanState {
  return {
    forward: null,
    backward: null,
    evaluate: null,
    forwardCount: 0,
    backwardCount: 0,
  };
}

/** Drop `frame` if `ev` proves it has exited (same thread, depth back at or
 *  above the frame's own). Covers a normal `return` and an exception unwind
 *  identically — see the module docstring. */
function closeIfExited(
  frame: OpenFrame | null,
  ev: TraceEvent
): OpenFrame | null {
  if (!frame) return null;
  if (ev.thread_id !== frame.threadId) return frame;
  return ev.frame_depth <= frame.depth ? null : frame;
}

function openFrame(ev: TraceEvent): OpenFrame {
  return { depth: ev.frame_depth, threadId: ev.thread_id };
}

/**
 * Scan `events[startIndex..]` for activity segments, resuming from `carry`.
 * Segments carry ABSOLUTE indices into `events` regardless of where the scan
 * started; `scanActivitySegments(events, n, 0, undefined)` and any sequence of
 * incremental calls produce the same total.
 *
 * `tokenCount` is `NetworkSpec.tokens.length` — both the forward n-th-call
 * (`activeToken = n - 1`) and the backward right-to-left indexing
 * (`activeToken = tokenCount - n`) need it to place the highlight on the
 * correct column.
 */
export function scanActivitySegments(
  events: readonly TraceEvent[],
  tokenCount: number,
  startIndex = 0,
  carry?: ActivityScanState
): ScanStep<ActivitySegment, ActivityScanState> {
  const state: ActivityScanState = carry ? { ...carry } : initialState();
  const items: ActivitySegment[] = [];

  const push = (
    fromIndex: number,
    phase: ActivityPhase,
    activeToken: number
  ) => {
    items.push({ fromIndex, phase, activeToken });
  };

  for (let i = startIndex; i < events.length; i++) {
    const ev = events[i];
    if (!ev) continue; // noUncheckedIndexedAccess guard
    const nodeId = ev.node_id;

    // Close before opening: a frame's own `return`, and the first event after
    // an exception unwound out of it, both land here.
    state.forward = closeIfExited(state.forward, ev);
    state.backward = closeIfExited(state.backward, ev);
    state.evaluate = closeIfExited(state.evaluate, ev);

    if (ev.event !== "call") {
      if (
        ev.event === "return" &&
        matchesBeaconNode(nodeId, "train.py:train_step")
      ) {
        // Back to idle between steps (and after the last step in a trace).
        push(i, "idle", -1);
      }
      continue;
    }

    if (matchesBeaconNode(nodeId, "train.py:evaluate")) {
      state.evaluate = openFrame(ev);
    } else if (matchesBeaconNode(nodeId, "model.py:Sequential.forward")) {
      state.forward = openFrame(ev);
      state.forwardCount = 0;
    } else if (
      state.forward &&
      matchesBeaconMethod(nodeId, "layers.py", "forward")
    ) {
      state.forwardCount += 1;
      push(
        i,
        state.evaluate ? "forward-eval" : "forward-train",
        state.forwardCount - 1
      );
    } else if (matchesBeaconMethod(nodeId, "losses.py", "forward")) {
      push(i, "loss", -1);
    } else if (matchesBeaconMethod(nodeId, "losses.py", "backward")) {
      push(i, "loss-grad", -1);
    } else if (matchesBeaconNode(nodeId, "model.py:Sequential.backward")) {
      state.backward = openFrame(ev);
      state.backwardCount = 0;
    } else if (
      state.backward &&
      matchesBeaconMethod(nodeId, "layers.py", "backward")
    ) {
      state.backwardCount += 1;
      push(i, "backward", tokenCount - state.backwardCount);
    } else if (matchesBeaconMethod(nodeId, "optim.py", "step")) {
      push(i, "update", -1);
    } else if (matchesBeaconNode(nodeId, "model.py:Sequential.zero_grad")) {
      push(i, "reset", -1);
    }
  }

  return { items, carry: state };
}

/**
 * Build the ordered activity-segment index for a full (or truncated-prefix)
 * trace-event array — the one-shot form of `scanActivitySegments` for callers
 * that don't need incremental scanning.
 *
 * Tolerates a truncated prefix (a missing closing `return`) by construction:
 * every transition is driven by a single event in isolation, never by matching
 * a call to its eventual return, so a stream that ends mid-frame simply stops
 * producing new segments.
 */
export function buildActivityIndex(
  events: readonly TraceEvent[],
  tokenCount: number
): ActivitySegment[] {
  return scanActivitySegments(events, tokenCount, 0, undefined).items;
}

/**
 * The segment active as of `playhead` — the last segment whose `fromIndex` is
 * at or before it (`playheadLookup.ts`'s inclusive convention: the event AT
 * the playhead has happened). Returns a synthetic idle segment (never `null`)
 * before the first real segment or for an empty index, so callers never need a
 * null-check for "nothing has happened yet". `segments` must be in ascending
 * `fromIndex` order (as returned by `buildActivityIndex`).
 */
export function activityAt(
  segments: readonly ActivitySegment[],
  playhead: number
): ActivitySegment {
  return lastAtOrBefore(segments, playhead, (s) => s.fromIndex) ?? IDLE_SEGMENT;
}
