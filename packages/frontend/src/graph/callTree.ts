import type { TraceEvent } from "@grackle/shared-types";

/**
 * Call-tree reconstruction from a flat trace-event stream (Phase 8.2, ADR-0019).
 *
 * The `sys.monitoring` tracer (ADR-0013) emits only four event kinds:
 *   - `call`      — PUSH: a frame is entered. `frame_depth` is the entered
 *                   frame's own depth (0 = outermost), captured *before* the
 *                   per-thread depth counter is incremented.
 *   - `return`    — POP: a frame exits normally. `frame_depth` is the returning
 *                   frame's own depth, captured *after* the counter is
 *                   decremented — so a `call`/`return` pair for the SAME frame
 *                   reports the SAME `frame_depth`.
 *   - `exception` — OBSERVATION only: a `RAISE` was seen. It does NOT pop a
 *                   frame and does NOT change depth.
 *   - `line`      — non-structural (only with `--lines`); ignored here.
 *
 * Crucially there is **no `unwind` event and no `yield`/`resume` event**.
 * A frame that exits via a propagating exception fires `PY_UNWIND`, which the
 * tracer uses purely for depth bookkeeping and emits *nothing* for. Generators
 * never emit yield/resume markers. So the stream can contain a `call` with no
 * matching `return` for two reasons: (a) the frame unwound via exception, and
 * (b) `--max-events` truncated the stream mid-frame. Reconstruction is
 * therefore depth-driven and self-correcting rather than a naive call/return
 * pairing:
 *
 *   - On `call` at depth `d`, any still-open frames at depth >= `d` must have
 *     closed silently (exception unwind) — close them implicitly first.
 *   - On `return` at depth `d`, close any deeper open frames (their children
 *     unwound), then close the depth-`d` frame normally.
 *   - At stream end, close everything still open (truncation / top-level
 *     unwind / a window that ends mid-stack).
 *
 * Implicit closes are stamped with the triggering event's `ts_ns` (an upper
 * bound that keeps the tree well-formed: a parent never ends before its
 * children). This slightly over-attributes wall-time to exception-unwound
 * frames; that approximation, plus the documented generator depth drift from
 * ADR-0013, are the only sources of timing imprecision. See ADR-0019.
 *
 * Threads are reconstructed independently: events are partitioned by
 * `thread_id` (the flat stream is a global interleave) and each thread's
 * top-level frames become roots.
 *
 * This module is a pure function over already-received events — no wire
 * messages, mirroring `heatmap.ts` / `runtimeCoverage.ts`.
 */

/** A single reconstructed call frame (one invocation in the raw tree, or a
 *  merged group of invocations in the aggregated tree). */
export interface CallFrame {
  /** Static-graph node id, e.g. `"services/auth.py:login"` or the file-level
   *  fallback `"services/auth.py"`. Always a valid graph node id (or the
   *  `"<unresolved>"` sentinel); pass straight to `selectNode`. */
  nodeId: string;
  /** Short human label derived from `nodeId` (last `:`/`/` segment). */
  label: string;
  /** OS thread this frame ran on (`thread_id`). */
  threadId: number;
  /** Original `frame_depth` from the opening `call`. NOTE: for layout use the
   *  frame's position in the reconstructed tree, not this value — in a partial
   *  (seekable-window) stream a frame whose parent opened before the window
   *  becomes a forest root while still reporting a non-zero `frame_depth`. */
  depth: number;
  /** `ts_ns` of the opening `call`. */
  startNs: number;
  /** `ts_ns` of the close (matching `return`, an implicit close, or stream end). */
  endNs: number;
  /** `endNs - startNs` (>= 0). In the aggregated tree, the sum over the group. */
  totalNs: number;
  /** `totalNs` minus the sum of children `totalNs` (>= 0). */
  selfNs: number;
  /** Invocation count: 1 in the raw tree; the merged count in the aggregated tree. */
  count: number;
  /** True when the frame was closed implicitly (exception unwind, truncation,
   *  or window edge) rather than by an observed `return`. */
  synthetic: boolean;
  /** True when an `exception` event was observed while this frame was on top. */
  raised: boolean;
  children: CallFrame[];
}

/** A reconstructed call forest plus session-level metadata. */
export interface CallTree {
  /** Top-level frames across all threads, in first-appearance order. */
  roots: CallFrame[];
  /** Thread ids seen, in first-appearance order. */
  threads: number[];
  /** Wall-clock span of the reconstructed window (`maxTs - minTs`, >= 0). */
  totalNs: number;
  /** Number of `call` events processed (raw frame count). */
  frameCount: number;
  /** True when any frame had to be closed implicitly (exceptions / truncation /
   *  windowing) — surfaced so the UI can flag an approximate reconstruction. */
  hadSynthetic: boolean;
  /** Number of `return` events that had no open frame to close (frames that
   *  opened before a seekable window). Non-zero ⇒ a partial-window tree. */
  orphanReturns: number;
}

/** Derive a readable short label from a static-graph node id.
 *  `"a/b/c.py:Foo.bar"` → `"Foo.bar"`; `"a/b/c.py"` → `"c.py"`. */
export function frameLabel(nodeId: string): string {
  const afterColon = nodeId.includes(":")
    ? (nodeId.split(":").pop() ?? nodeId)
    : nodeId;
  return afterColon.split("/").pop() ?? afterColon;
}

interface ThreadState {
  stack: CallFrame[];
  roots: CallFrame[];
  /** ts_ns of the most recent event seen on this thread (stream-end fallback). */
  lastNs: number;
}

function makeFrame(ev: TraceEvent): CallFrame {
  return {
    nodeId: ev.node_id,
    label: frameLabel(ev.node_id),
    threadId: ev.thread_id,
    depth: ev.frame_depth,
    startNs: ev.ts_ns,
    endNs: ev.ts_ns,
    totalNs: 0,
    selfNs: 0,
    count: 1,
    synthetic: false,
    raised: false,
    children: [],
  };
}

/** Pop and implicitly close every open frame whose `frame_depth` is >= `minDepth`,
 *  stamping each with `ns`. Returns how many frames were closed. */
function closeFramesAtOrAbove(
  state: ThreadState,
  minDepth: number,
  ns: number
): number {
  let closed = 0;
  while (state.stack.length > 0) {
    const top = state.stack[state.stack.length - 1];
    if (!top || top.depth < minDepth) break;
    top.endNs = Math.max(top.endNs, ns);
    top.synthetic = true;
    state.stack.pop();
    closed += 1;
  }
  return closed;
}

/** Finalise `totalNs` / `selfNs` for a frame and its subtree (bottom-up). */
function finalizeTimings(frame: CallFrame): void {
  let childrenTotal = 0;
  for (const child of frame.children) {
    finalizeTimings(child);
    childrenTotal += child.totalNs;
  }
  frame.totalNs = Math.max(0, frame.endNs - frame.startNs);
  // Children are sequential within a frame, so their summed span never exceeds
  // the parent's; clamp at 0 to defend against equal-timestamp rounding.
  frame.selfNs = Math.max(0, frame.totalNs - childrenTotal);
}

/**
 * Reconstruct a call forest from an ordered trace-event slice.
 *
 * The caller decides which events to pass: the full buffer (live/buffered
 * sessions) or a seekable window. Reconstruction is order-driven — `ts_ns` is
 * used only for durations, never for ordering (it is not strictly increasing
 * and can repeat).
 */
export function buildCallTree(events: TraceEvent[]): CallTree {
  const threadStates = new Map<number, ThreadState>();
  const threads: number[] = [];
  let frameCount = 0;
  let orphanReturns = 0;
  let minNs = Number.POSITIVE_INFINITY;
  let maxNs = Number.NEGATIVE_INFINITY;

  for (let i = 0; i < events.length; i++) {
    const ev = events[i];
    if (!ev) continue; // noUncheckedIndexedAccess guard

    if (ev.ts_ns < minNs) minNs = ev.ts_ns;
    if (ev.ts_ns > maxNs) maxNs = ev.ts_ns;

    let state = threadStates.get(ev.thread_id);
    if (!state) {
      state = { stack: [], roots: [], lastNs: ev.ts_ns };
      threadStates.set(ev.thread_id, state);
      threads.push(ev.thread_id);
    }
    state.lastNs = ev.ts_ns;

    if (ev.event === "call") {
      // Anything open at depth >= d unwound silently (exception) — close it.
      closeFramesAtOrAbove(state, ev.frame_depth, ev.ts_ns);
      const frame = makeFrame(ev);
      const parent = state.stack[state.stack.length - 1];
      if (parent) {
        parent.children.push(frame);
      } else {
        state.roots.push(frame);
      }
      state.stack.push(frame);
      frameCount += 1;
    } else if (ev.event === "return") {
      // Deeper frames (children of the returning frame) unwound — close them.
      closeFramesAtOrAbove(state, ev.frame_depth + 1, ev.ts_ns);
      const top = state.stack[state.stack.length - 1];
      if (top && top.depth === ev.frame_depth) {
        top.endNs = Math.max(top.endNs, ev.ts_ns);
        top.synthetic = false;
        state.stack.pop();
      } else {
        // A return for a frame that opened before this (seekable) window.
        orphanReturns += 1;
      }
    } else if (ev.event === "exception") {
      // Observation only — do not pop. RAISE fires once per frame an exception
      // is raised in or propagates through, and the deeper (still-open) frames
      // remain on the stack, so attribute it to the matching open frame by
      // node_id rather than blindly to the top. Falls back to the top when no
      // open frame matches (e.g. an originating RAISE whose frame_depth is the
      // callee's; the call already pushed it).
      let target = state.stack[state.stack.length - 1];
      for (let j = state.stack.length - 1; j >= 0; j--) {
        const frame = state.stack[j];
        if (frame && frame.nodeId === ev.node_id) {
          target = frame;
          break;
        }
      }
      if (target) target.raised = true;
    }
    // `line` and unknown event kinds are non-structural (ADR-0004): ignore.
  }

  // Close everything still open: truncated streams, top-level exception exits,
  // or a window that ends mid-stack.
  let hadSynthetic = false;
  const roots: CallFrame[] = [];
  for (const tid of threads) {
    const state = threadStates.get(tid);
    if (!state) continue;
    if (closeFramesAtOrAbove(state, 0, state.lastNs) > 0) hadSynthetic = true;
    for (const root of state.roots) {
      finalizeTimings(root);
      roots.push(root);
    }
  }
  // A frame closed by `closeFramesAtOrAbove` during the main loop is also
  // synthetic; recompute the flag from the finalised forest so it is accurate
  // regardless of when the implicit close happened.
  if (!hadSynthetic) hadSynthetic = roots.some(hasSyntheticFrame);

  const totalNs = maxNs >= minNs && Number.isFinite(minNs) ? maxNs - minNs : 0;

  return { roots, threads, totalNs, frameCount, hadSynthetic, orphanReturns };
}

function hasSyntheticFrame(frame: CallFrame): boolean {
  if (frame.synthetic) return true;
  return frame.children.some(hasSyntheticFrame);
}

/**
 * Aggregate a raw call forest into the classic flame-graph shape: sibling
 * frames sharing a `nodeId` under the same parent are merged, summing
 * `totalNs` / `selfNs` / `count`. Children are emitted in descending `totalNs`
 * order ("left-heavy" flame). Recursion (same `nodeId` nested) is preserved —
 * only *siblings* merge, never ancestors into descendants.
 */
export function aggregateCallTree(roots: CallFrame[]): CallFrame[] {
  return aggregateSiblings(roots);
}

function aggregateSiblings(frames: CallFrame[]): CallFrame[] {
  // Preserve first-appearance order of node ids, then sort by totalNs at the end.
  const order: string[] = [];
  const groups = new Map<string, CallFrame[]>();
  for (const f of frames) {
    let g = groups.get(f.nodeId);
    if (!g) {
      g = [];
      groups.set(f.nodeId, g);
      order.push(f.nodeId);
    }
    g.push(f);
  }

  const merged: CallFrame[] = [];
  for (const nodeId of order) {
    const group = groups.get(nodeId);
    if (!group || group.length === 0) continue;
    const first = group[0];
    if (!first) continue;

    const childPool: CallFrame[] = [];
    let totalNs = 0;
    let selfNs = 0;
    let count = 0;
    let startNs = Number.POSITIVE_INFINITY;
    let endNs = Number.NEGATIVE_INFINITY;
    let synthetic = false;
    let raised = false;
    for (const f of group) {
      totalNs += f.totalNs;
      selfNs += f.selfNs;
      count += f.count;
      if (f.startNs < startNs) startNs = f.startNs;
      if (f.endNs > endNs) endNs = f.endNs;
      synthetic ||= f.synthetic;
      raised ||= f.raised;
      for (const child of f.children) childPool.push(child);
    }

    merged.push({
      nodeId,
      label: first.label,
      threadId: first.threadId,
      depth: first.depth,
      startNs,
      endNs,
      totalNs,
      selfNs,
      count,
      synthetic,
      raised,
      children: aggregateSiblings(childPool),
    });
  }

  merged.sort((a, b) => b.totalNs - a.totalNs || b.count - a.count);
  return merged;
}

/**
 * The hot path: the heaviest root-to-leaf chain by `totalNs`. Returns the set
 * of frame *references* on that chain so a renderer can highlight them.
 * Operates on whichever forest it is given (raw or aggregated).
 */
export function hotPath(roots: CallFrame[]): Set<CallFrame> {
  const path = new Set<CallFrame>();
  let frontier = roots;
  while (frontier.length > 0) {
    let best: CallFrame | undefined;
    for (const f of frontier) {
      if (!best || f.totalNs > best.totalNs) best = f;
    }
    if (!best) break;
    path.add(best);
    frontier = best.children;
  }
  return path;
}
