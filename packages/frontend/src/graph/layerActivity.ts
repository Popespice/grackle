import type { TraceEvent } from "@grackle/shared-types";

/**
 * Playhead -> network-animation-phase derivation from a raw trace-event
 * array (Phase 12.4).
 *
 * One left-to-right pass over the events building an ordered list of
 * `ActivitySegment`s, each marking the event index at which a new animation
 * phase becomes active. The state machine keys ONLY on node-id suffixes,
 * never on `frame_depth` / a call stack: none of the matched functions
 * recurse (D7 — the same no-generators/no-data-dependent-branching
 * determinism invariant that makes the golden-34 event sequence exact), so
 * "is Sequential.forward currently open" is a plain boolean, not a depth
 * counter. Pure function over an already-received event array — no wire
 * messages, mirroring `epochSeries.ts` / `layerStats.ts`.
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

const IDLE_SEGMENT: ActivitySegment = {
  fromIndex: -1,
  phase: "idle",
  activeToken: -1,
};

/** Exact-or-slash-preceded suffix match — same false-positive guard as
 *  `epochSeries.ts` / `networkSpec.ts` (a bare `endsWith` would also match a
 *  node_id like "myXXX.py:..." whose filename merely ENDS WITH "XXX.py"). */
function matchesExact(nodeId: string, suffix: string): boolean {
  return nodeId === suffix || nodeId.endsWith(`/${suffix}`);
}

/** `<file>:<Class>.<method>` match, ignoring the class name. The trace's
 *  single-colon `path:qualname` node-id scheme guarantees the qualname
 *  itself never contains a colon, so splitting on the LAST colon
 *  unambiguously separates path from qualname regardless of how many colons
 *  a (POSIX-relative, so colon-free in practice) path segment has. */
function matchesMethod(nodeId: string, file: string, method: string): boolean {
  const colon = nodeId.lastIndexOf(":");
  if (colon === -1) return false;
  const path = nodeId.slice(0, colon);
  if (path !== file && !path.endsWith(`/${file}`)) return false;
  const qualname = nodeId.slice(colon + 1);
  const dot = qualname.lastIndexOf(".");
  if (dot === -1) return false;
  return qualname.slice(dot + 1) === method;
}

/**
 * Build the ordered activity-segment index for a full (or truncated-prefix)
 * trace-event array. `tokenCount` is `NetworkSpec.tokens.length` — both the
 * forward n-th-call (`activeToken = n - 1`) and the backward right-to-left
 * indexing (`activeToken = tokenCount - n`) need it to place the highlight
 * on the correct column.
 *
 * Tolerates a truncated prefix (a missing closing `return`) by construction:
 * every transition below is driven by a single `call`/`return` event in
 * isolation, never by matching a call to its eventual return, so a stream
 * that ends mid-frame simply stops producing new segments rather than
 * throwing or leaving an inconsistent index.
 */
export function buildActivityIndex(
  events: readonly TraceEvent[],
  tokenCount: number
): ActivitySegment[] {
  const segments: ActivitySegment[] = [];
  let forwardOpen = false;
  let backwardOpen = false;
  let evaluateOpen = false;
  let forwardCount = 0;
  let backwardCount = 0;

  const push = (
    fromIndex: number,
    phase: ActivityPhase,
    activeToken: number
  ) => {
    segments.push({ fromIndex, phase, activeToken });
  };

  for (let i = 0; i < events.length; i++) {
    const ev = events[i];
    if (!ev) continue; // noUncheckedIndexedAccess guard
    const nodeId = ev.node_id;

    if (ev.event === "call") {
      if (matchesExact(nodeId, "train.py:evaluate")) {
        evaluateOpen = true;
      } else if (matchesExact(nodeId, "model.py:Sequential.forward")) {
        forwardOpen = true;
        forwardCount = 0;
      } else if (forwardOpen && matchesMethod(nodeId, "layers.py", "forward")) {
        forwardCount += 1;
        push(
          i,
          evaluateOpen ? "forward-eval" : "forward-train",
          forwardCount - 1
        );
      } else if (matchesMethod(nodeId, "losses.py", "forward")) {
        push(i, "loss", -1);
      } else if (matchesMethod(nodeId, "losses.py", "backward")) {
        push(i, "loss-grad", -1);
      } else if (matchesExact(nodeId, "model.py:Sequential.backward")) {
        backwardOpen = true;
        backwardCount = 0;
      } else if (
        backwardOpen &&
        matchesMethod(nodeId, "layers.py", "backward")
      ) {
        backwardCount += 1;
        push(i, "backward", tokenCount - backwardCount);
      } else if (matchesMethod(nodeId, "optim.py", "step")) {
        push(i, "update", -1);
      } else if (matchesExact(nodeId, "model.py:Sequential.zero_grad")) {
        push(i, "reset", -1);
      }
    } else if (ev.event === "return") {
      if (matchesExact(nodeId, "train.py:evaluate")) {
        evaluateOpen = false;
      } else if (matchesExact(nodeId, "model.py:Sequential.forward")) {
        forwardOpen = false;
      } else if (matchesExact(nodeId, "model.py:Sequential.backward")) {
        backwardOpen = false;
      } else if (matchesExact(nodeId, "train.py:train_step")) {
        // Back to idle between steps (and after the last step in a trace).
        push(i, "idle", -1);
      }
    }
  }

  return segments;
}

/**
 * The segment active "as of" `playhead` — the last segment whose
 * `fromIndex` is strictly before `playhead`. Returns a synthetic idle
 * segment (never `null`) before the first real segment or for an empty
 * index, so callers never need a null-check for "nothing has happened yet".
 * `segments` must be in ascending `fromIndex` order (as returned by
 * `buildActivityIndex`).
 */
export function activityAt(
  segments: readonly ActivitySegment[],
  playhead: number
): ActivitySegment {
  let lo = 0;
  let hi = segments.length - 1;
  let result: ActivitySegment = IDLE_SEGMENT;
  while (lo <= hi) {
    const mid = (lo + hi) >> 1;
    const seg = segments[mid];
    if (!seg) break; // unreachable given the loop bounds
    if (seg.fromIndex < playhead) {
      result = seg;
      lo = mid + 1;
    } else {
      hi = mid - 1;
    }
  }
  return result;
}
