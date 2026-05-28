import type { TraceEvent } from "@grackle/shared-types";
import {
  buildCallTree,
  type CallFrame,
  type CallTree,
} from "../graph/callTree";

/**
 * Speedscope interchange (Phase 8.2, ADR-0019).
 *
 * Exports a reconstructed call tree to the speedscope "evented" file format
 * (https://www.speedscope.app/file-format-schema.json) so a grackle trace
 * opens directly in speedscope.app, and parses that format back into a
 * `TraceEvent[]` for re-import. Frame names ARE static-graph node ids, so a
 * round-trip preserves node identity (best-effort node-id matching per the
 * Phase 8.2 plan); third-party speedscope files import with whatever frame
 * names they carry — those simply won't resolve to graph nodes.
 *
 * One profile is emitted per `thread_id`. Timestamps are session-relative
 * nanoseconds (the global earliest event is the origin), since `ts_ns` is only
 * meaningful as a difference (ADR-0013).
 */

interface SpeedscopeFrame {
  name: string;
}

type SpeedscopeEventType = "O" | "C";

interface SpeedscopeEvent {
  type: SpeedscopeEventType;
  frame: number;
  at: number;
}

interface SpeedscopeProfile {
  type: "evented";
  name: string;
  unit: "nanoseconds";
  startValue: number;
  endValue: number;
  events: SpeedscopeEvent[];
}

export interface SpeedscopeFile {
  $schema: "https://www.speedscope.app/file-format-schema.json";
  name: string;
  exporter: string;
  activeProfileIndex: number;
  shared: { frames: SpeedscopeFrame[] };
  profiles: SpeedscopeProfile[];
}

/** Group a flat forest by thread id, preserving first-seen thread order. */
function rootsByThread(tree: CallTree): Map<number, CallFrame[]> {
  const byThread = new Map<number, CallFrame[]>();
  for (const tid of tree.threads) byThread.set(tid, []);
  for (const root of tree.roots) {
    const list = byThread.get(root.threadId);
    if (list) list.push(root);
    else byThread.set(root.threadId, [root]);
  }
  return byThread;
}

function globalOrigin(tree: CallTree): number {
  let origin = Number.POSITIVE_INFINITY;
  for (const root of tree.roots) {
    if (root.startNs < origin) origin = root.startNs;
  }
  return Number.isFinite(origin) ? origin : 0;
}

/** Serialise a call tree to a speedscope evented file. */
export function exportSpeedscope(
  tree: CallTree,
  name = "grackle trace"
): SpeedscopeFile {
  const frames: SpeedscopeFrame[] = [];
  const frameIndex = new Map<string, number>();
  const intern = (nodeId: string): number => {
    const existing = frameIndex.get(nodeId);
    if (existing !== undefined) return existing;
    const idx = frames.length;
    frames.push({ name: nodeId });
    frameIndex.set(nodeId, idx);
    return idx;
  };

  const origin = globalOrigin(tree);
  const profiles: SpeedscopeProfile[] = [];

  for (const [tid, roots] of rootsByThread(tree)) {
    if (roots.length === 0) continue;
    const events: SpeedscopeEvent[] = [];
    let startValue = Number.POSITIVE_INFINITY;
    let endValue = Number.NEGATIVE_INFINITY;

    const dfs = (frame: CallFrame): void => {
      const at = frame.startNs - origin;
      const end = frame.endNs - origin;
      if (at < startValue) startValue = at;
      if (end > endValue) endValue = end;
      const idx = intern(frame.nodeId);
      events.push({ type: "O", frame: idx, at });
      for (const child of frame.children) dfs(child);
      events.push({ type: "C", frame: idx, at: end });
    };
    for (const root of roots) dfs(root);

    profiles.push({
      type: "evented",
      name: `thread ${tid}`,
      unit: "nanoseconds",
      startValue: Number.isFinite(startValue) ? startValue : 0,
      endValue: Number.isFinite(endValue) ? endValue : 0,
      events,
    });
  }

  return {
    $schema: "https://www.speedscope.app/file-format-schema.json",
    name,
    exporter: "grackle",
    activeProfileIndex: 0,
    shared: { frames },
    profiles,
  };
}

/** Extract a numeric thread id from a `"thread <n>"` profile name; falls back
 *  to `fallback` for names from third-party exporters. */
function parseThreadId(name: string, fallback: number): number {
  const m = name.match(/(\d+)\s*$/);
  return m?.[1] !== undefined ? Number(m[1]) : fallback;
}

/**
 * Parse a speedscope evented file back into `TraceEvent[]`, producing
 * `call`/`return` events with reconstructed `frame_depth` (matching the
 * tracer's convention that a call and its return carry the frame's own depth).
 * Non-evented profiles are skipped. Resulting events feed `buildCallTree`.
 */
export function parseSpeedscope(file: SpeedscopeFile): TraceEvent[] {
  const out: TraceEvent[] = [];
  const frames = file.shared?.frames ?? [];
  file.profiles?.forEach((profile, index) => {
    if (profile.type !== "evented") return;
    const threadId = parseThreadId(profile.name ?? "", index);
    let depth = 0;
    for (const e of profile.events) {
      const name = frames[e.frame]?.name ?? "<unresolved>";
      if (e.type === "O") {
        out.push({
          event: "call",
          node_id: name,
          ts_ns: e.at,
          thread_id: threadId,
          frame_depth: depth,
        });
        depth += 1;
      } else {
        depth = Math.max(0, depth - 1);
        out.push({
          event: "return",
          node_id: name,
          ts_ns: e.at,
          thread_id: threadId,
          frame_depth: depth,
        });
      }
    }
  });
  return out;
}

/** Convenience: parse a speedscope file straight into a `CallTree`. */
export function importSpeedscopeTree(file: SpeedscopeFile): CallTree {
  return buildCallTree(parseSpeedscope(file));
}
