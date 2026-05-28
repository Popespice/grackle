import type { TraceEvent } from "@grackle/shared-types";
import {
  buildCallTree,
  type CallFrame,
  type CallTree,
} from "../graph/callTree";

/**
 * Chrome Trace Event Format interchange (Phase 8.2, ADR-0019).
 *
 * Exports a reconstructed call tree to the Chrome Trace Event Format
 * (the JSON consumed by `chrome://tracing` and also by speedscope.app), and
 * parses that format back into `TraceEvent[]`.
 *
 * Each frame becomes one "complete" event (`ph: "X"`) with a duration. Chrome
 * trace timestamps are MICROSECONDS (floats permitted) regardless of
 * `displayTimeUnit`, so nanoseconds are divided by 1000. Timestamps are
 * session-relative (global earliest event = origin), since `ts_ns` is only
 * meaningful as a difference (ADR-0013). `pid` is fixed at 1; `tid` is the
 * trace `thread_id`. The event `name` is the static-graph node id, so a
 * round-trip preserves node identity.
 */

const NS_PER_US = 1000;

interface ChromeCompleteEvent {
  name: string;
  cat: string;
  ph: "X";
  ts: number;
  dur: number;
  pid: number;
  tid: number;
}

/** A Chrome trace event as it may appear in an *imported* file: `ph` is an open
 *  string ("X" complete, or "B"/"E" begin/end) and most fields are optional. */
export interface ChromeTraceEvent {
  name?: string;
  cat?: string;
  ph: string;
  ts?: number;
  dur?: number;
  pid?: number;
  tid?: number;
}

export interface ChromeTraceFile {
  displayTimeUnit?: "ns" | "ms";
  traceEvents: ChromeTraceEvent[];
}

/** A normalised [start, end] span (microseconds) used during import. Both
 *  complete (X) events and paired begin/end (B/E) events reduce to this. */
interface Interval {
  start: number;
  end: number;
  name: string;
}

function globalOrigin(tree: CallTree): number {
  let origin = Number.POSITIVE_INFINITY;
  for (const root of tree.roots) {
    if (root.startNs < origin) origin = root.startNs;
  }
  return Number.isFinite(origin) ? origin : 0;
}

/** Serialise a call tree to a Chrome Trace Event Format file. */
export function exportChromeTrace(tree: CallTree): ChromeTraceFile {
  const origin = globalOrigin(tree);
  const traceEvents: ChromeCompleteEvent[] = [];

  const dfs = (frame: CallFrame): void => {
    traceEvents.push({
      name: frame.nodeId,
      cat: "function",
      ph: "X",
      ts: (frame.startNs - origin) / NS_PER_US,
      dur: frame.totalNs / NS_PER_US,
      pid: 1,
      tid: frame.threadId,
    });
    for (const child of frame.children) dfs(child);
  };
  for (const root of tree.roots) dfs(root);

  return { displayTimeUnit: "ns", traceEvents };
}

/**
 * Parse a Chrome trace back into `TraceEvent[]`.
 *
 * Both complete (`ph: "X"`) events and paired begin/end (`ph: "B"`/`"E"`)
 * events reduce to `[start, end]` intervals, which are merged **per thread**
 * into one time-ordered list and replayed through a containment stack — so a
 * file that mixes X and B/E on the same thread reconstructs as one correctly
 * nested, time-ordered tree (not B/E-then-X in two disjoint passes). `B`/`E`
 * are paired LIFO; an unclosed `B` becomes a zero-span interval.
 *
 * Intervals are sorted by start ascending, then by end descending so an outer
 * frame opens before a frame it contains. `frame_depth` is rebuilt from the
 * stack height; timestamps come back in nanoseconds.
 *
 * Known lossy edge: the X (interval) format cannot distinguish a zero-duration
 * frame that *precedes* a sibling at the same timestamp from one *nested* at
 * that timestamp — both are `[t, t]` beside `[t, …]`. Such collisions (only
 * possible when `ts_ns` repeats) reconstruct as nested. The speedscope evented
 * format has no such ambiguity; prefer it for lossless round-trips.
 */
export function parseChromeTrace(file: ChromeTraceFile): TraceEvent[] {
  const raw = file.traceEvents ?? [];

  // chrome://tracing nesting is keyed on (pid, tid), not tid alone. When the
  // file spans multiple processes, fold pid into the thread key so a tid reused
  // across processes is not conflated into one stack; with a single process
  // (incl. grackle's own pid:1 exports) the key stays the bare tid so
  // round-trips and the reconstructed thread ids are unchanged.
  const pids = new Set<number>();
  for (const e of raw) pids.add(e.pid ?? 0);
  const multiProcess = pids.size > 1;
  const threadKey = (e: ChromeTraceEvent): number =>
    multiProcess ? (e.pid ?? 0) * 1_000_000 + (e.tid ?? 0) : (e.tid ?? 0);
  // Numbers may arrive as JSON strings from a foreign exporter; coerce defensively.
  const num = (v: unknown): number => {
    const n = Number(v);
    return Number.isFinite(n) ? n : 0;
  };

  const byTid = new Map<number, Interval[]>();
  const beStacks = new Map<number, { name: string; start: number }[]>();
  const pushInterval = (tid: number, iv: Interval): void => {
    const list = byTid.get(tid);
    if (list) list.push(iv);
    else byTid.set(tid, [iv]);
  };

  for (const e of raw) {
    const tid = threadKey(e);
    if (e.ph === "X") {
      const start = num(e.ts);
      pushInterval(tid, {
        start,
        end: start + num(e.dur),
        name: e.name ?? "<unresolved>",
      });
    } else if (e.ph === "B") {
      const s = beStacks.get(tid) ?? [];
      s.push({ name: e.name ?? "<unresolved>", start: num(e.ts) });
      beStacks.set(tid, s);
    } else if (e.ph === "E") {
      const open = beStacks.get(tid)?.pop();
      if (open) {
        pushInterval(tid, {
          start: open.start,
          end: num(e.ts),
          name: open.name,
        });
      }
    }
  }
  // Unclosed B events at end-of-stream → zero-span intervals.
  for (const [tid, s] of beStacks) {
    for (const open of s) {
      pushInterval(tid, {
        start: open.start,
        end: open.start,
        name: open.name,
      });
    }
  }

  const out: TraceEvent[] = [];
  for (const [tid, intervals] of byTid) {
    intervals.sort((a, b) => a.start - b.start || b.end - a.end);
    const stack: Interval[] = [];
    const closeTo = (t: number): void => {
      while (stack.length > 0) {
        const top = stack[stack.length - 1];
        if (!top || top.end > t) break;
        stack.pop();
        out.push({
          event: "return",
          node_id: top.name,
          ts_ns: top.end * NS_PER_US,
          thread_id: tid,
          frame_depth: stack.length,
        });
      }
    };
    for (const iv of intervals) {
      closeTo(iv.start);
      out.push({
        event: "call",
        node_id: iv.name,
        ts_ns: iv.start * NS_PER_US,
        thread_id: tid,
        frame_depth: stack.length,
      });
      stack.push(iv);
    }
    while (stack.length > 0) {
      const top = stack.pop();
      if (!top) break;
      out.push({
        event: "return",
        node_id: top.name,
        ts_ns: top.end * NS_PER_US,
        thread_id: tid,
        frame_depth: stack.length,
      });
    }
  }

  return out;
}

/** Convenience: parse a Chrome trace file straight into a `CallTree`. */
export function importChromeTraceTree(file: ChromeTraceFile): CallTree {
  return buildCallTree(parseChromeTrace(file));
}
