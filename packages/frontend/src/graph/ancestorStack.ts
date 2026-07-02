import type { ArgValue, TraceEvent } from "@grackle/shared-types";
import { frameLabel } from "./callTree";

/**
 * One open frame in the call stack at a point in time (Phase 10.3).
 *
 * Unlike `callTree.ts`'s `CallFrame` (a finished tree node with timing +
 * children), a `StackFrame` is a *live* ancestor: it exists only while the
 * frame is still open. It carries the arguments captured on its opening `call`
 * event (ADR-0025 `values.args`) so the value inspector can show what the
 * function was called with — even though its return value lives on a separate,
 * later `return` event and is therefore unavailable while the frame is open.
 */
export interface StackFrame {
  nodeId: string;
  /** Short human label, e.g. `"Foo.bar"` — reuses `callTree.frameLabel`. */
  label: string;
  /** `frame_depth` of the opening call (0 = outermost). */
  depth: number;
  threadId: number;
  /** Absolute index of the opening `call` event in the replayed array. */
  callIndex: number;
  /** Sampled args from the opening call's `values.args`; absent if uncaptured. */
  args?: ArgValue[];
}

/** The open call stacks across all threads at a replay point. */
export interface AncestorStacks {
  /**
   * Per-thread open stacks in push order (outermost first, innermost last).
   * Only threads that opened at least one frame in the prefix appear; a
   * thread whose frames all returned maps to an empty array. The UI reverses a
   * stack for innermost-first display.
   */
  byThread: Map<number, StackFrame[]>;
  /** Thread of the event at the replay index — the stack the UI shows by default. */
  activeThreadId: number | null;
}

/**
 * Derive the open ancestor stack after replaying `events[0..=index]`.
 *
 * This deliberately **mirrors** `buildCallTree`'s depth-driven unwind rule
 * (callTree.ts) rather than reusing it: `buildCallTree` runs a stream-end close
 * loop that drains every thread's live stack before returning, so the open
 * stack it computes is destroyed by the time it returns. The rule is short and
 * a consistency cross-check test guards the two from drifting.
 *
 * Rule (per thread): on `call` at depth `d`, pop every open frame with
 * `depth >= d` (they unwound silently, e.g. via an exception) and push the new
 * frame; on `return` at depth `d`, pop deeper children (`depth > d`) then the
 * matching depth-`d` frame (a return with no matching open frame is an orphan —
 * it opened before this prefix/window — and is ignored). `line`, `exception`
 * and unknown event kinds are non-structural (ADR-0004) and never change the
 * stack.
 *
 * `index` is clamped to `[0, events.length - 1]`: the playhead can equal
 * `events.length` (end of trace), in which case the stack after the final event
 * is returned.
 */
export function ancestorStackAt(
  events: TraceEvent[],
  index: number
): AncestorStacks {
  const byThread = new Map<number, StackFrame[]>();
  if (events.length === 0) return { byThread, activeThreadId: null };

  const last = Math.min(Math.max(index, 0), events.length - 1);

  for (let i = 0; i <= last; i++) {
    const ev = events[i];
    if (!ev) continue; // noUncheckedIndexedAccess guard

    if (ev.event === "call") {
      let stack = byThread.get(ev.thread_id);
      if (!stack) {
        stack = [];
        byThread.set(ev.thread_id, stack);
      }
      // Anything open at depth >= d unwound silently — pop it.
      while (
        stack.length > 0 &&
        (stack[stack.length - 1]?.depth ?? -1) >= ev.frame_depth
      ) {
        stack.pop();
      }
      const args = ev.values?.args;
      // Only set `args` when captured — `exactOptionalPropertyTypes` forbids
      // assigning an explicit `undefined` to the optional field.
      stack.push({
        nodeId: ev.node_id,
        label: frameLabel(ev.node_id),
        depth: ev.frame_depth,
        threadId: ev.thread_id,
        callIndex: i,
        ...(args !== undefined ? { args } : {}),
      });
    } else if (ev.event === "return") {
      const stack = byThread.get(ev.thread_id);
      if (!stack) continue; // orphan return — no open frame on this thread
      // Deeper frames (children) unwound — pop them.
      while (
        stack.length > 0 &&
        (stack[stack.length - 1]?.depth ?? -1) > ev.frame_depth
      ) {
        stack.pop();
      }
      const top = stack[stack.length - 1];
      if (top && top.depth === ev.frame_depth) {
        stack.pop();
      }
      // else: orphan return (frame opened before this prefix) — ignore.
    }
    // `line` / `exception` / unknown: non-structural, skip.
  }

  const activeThreadId = events[last]?.thread_id ?? null;
  return { byThread, activeThreadId };
}

/**
 * Nearest index strictly after `from` whose event is a `call` or `return`
 * (a value boundary), or `null` if none. Matches on structural kind regardless
 * of whether `values` was captured, so stepping works with capture off too.
 */
export function nextCallBoundary(
  events: TraceEvent[],
  from: number
): number | null {
  for (let i = from + 1; i < events.length; i++) {
    const e = events[i];
    if (e && (e.event === "call" || e.event === "return")) return i;
  }
  return null;
}

/** Nearest index strictly before `from` that is a `call` or `return`, else `null`. */
export function prevCallBoundary(
  events: TraceEvent[],
  from: number
): number | null {
  for (let i = Math.min(from, events.length) - 1; i >= 0; i--) {
    const e = events[i];
    if (e && (e.event === "call" || e.event === "return")) return i;
  }
  return null;
}
