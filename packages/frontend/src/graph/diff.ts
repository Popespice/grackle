/**
 * Differential analysis — pure functions, no I/O.
 *
 * Two diff modes:
 *
 * 1. **trace-vs-static** (`diffTraceVsStatic`): for each node in the graph,
 *    classify as `"touched"` (≥1 hit) or `"cold"` (0 hits) using runtime
 *    coverage data.
 *
 * 2. **trace-vs-trace** (`diffTraceVsTrace`): compare two node→count maps
 *    (e.g. from two trace sessions) and classify each node as `"new"`,
 *    `"gone"`, `"hotter"`, `"colder"`, or `"same"`.
 *
 * Both functions return `DiffEntry[]` sorted by severity (most actionable
 * first); consumers can display the list directly or build a `Map` for O(1)
 * lookup by node ID.
 */

import type { Graph } from "@grackle/shared-types";
import type { RuntimeCoverage } from "./runtimeCoverage";

export type DiffStatus =
  | "touched"
  | "cold"
  | "new"
  | "gone"
  | "hotter"
  | "colder"
  | "same";

export interface DiffEntry {
  nodeId: string;
  status: DiffStatus;
  /** Hit count in the baseline (session A, or current session for vs-static). */
  countA: number;
  /** Hit count in the comparison (session B); 0 for trace-vs-static. */
  countB: number;
  /** countB − countA (0 for trace-vs-static). */
  delta: number;
}

/**
 * Canonical hex colours per diff status — the single source of truth shared by
 * the graph overlay (GraphCanvas) and the panel chips (DiffPanel).
 *
 * All values are #rrggbb — never oklch/hsl/CSS-var — because Sigma 3.x
 * parseColor only accepts hex/rgb (ADR-0015).  `same` is the empty string: it
 * carries no override, so the graph falls through to the node's kind colour.
 */
export const DIFF_STATUS_COLORS: Record<DiffStatus, string> = {
  hotter: "#ef4444", // red    — regression
  new: "#22c55e", // green  — new coverage
  colder: "#3b82f6", // blue   — reduced calls
  gone: "#6b7280", // gray   — no longer called
  cold: "#f59e0b", // amber  — never called
  touched: "#10b981", // emerald — covered
  same: "", // empty = fall through to kind colour
};

/** Sort key per status — lower index = more actionable = shown first. */
const STATUS_ORDER: Record<DiffStatus, number> = {
  hotter: 0,
  new: 1,
  gone: 2,
  colder: 3,
  same: 4,
  cold: 5,
  touched: 6,
};

function byStatus(a: DiffEntry, b: DiffEntry): number {
  const sa = STATUS_ORDER[a.status] ?? 9;
  const sb = STATUS_ORDER[b.status] ?? 9;
  if (sa !== sb) return sa - sb;
  return a.nodeId < b.nodeId ? -1 : a.nodeId > b.nodeId ? 1 : 0;
}

/**
 * Classify every node in `graph` as `"touched"` or `"cold"` using the
 * session's runtime coverage data.
 *
 * Cold nodes (zero hits) sort first because they are the actionable ones —
 * potential dead code or untested paths.
 */
export function diffTraceVsStatic(
  graph: Graph,
  coverage: RuntimeCoverage
): DiffEntry[] {
  const entries: DiffEntry[] = graph.nodes.map((node) => {
    // RuntimeCoverage exposes only set membership (touched/cold/hot), not
    // per-node hit counts, so countA carries no quantitative value here — the
    // status field is the signal.  We deliberately set countA/countB/delta to
    // 0 rather than a synthetic 1/2: a non-zero count would read as a real hit
    // count to any consumer that renders it (e.g. a future vs-static table).
    // Callers wanting exact counts should use diffTraceVsTrace with a raw
    // counts map.
    const status: DiffStatus = coverage.touched.has(node.id)
      ? "touched"
      : "cold";
    return { nodeId: node.id, status, countA: 0, countB: 0, delta: 0 };
  });
  return entries.sort(byStatus);
}

/**
 * Build a `DiffEntry[]` from two node→count maps.
 *
 * The universe of nodes is the union of keys in `countsA` and `countsB`,
 * plus any IDs in `graphNodeIds` (so static-graph-only nodes appear as
 * `"same"` even if both sessions never touched them).
 *
 * @param countsA  Baseline session: node_id → hit count.
 * @param countsB  Comparison session: node_id → hit count.
 * @param graphNodeIds  Optional extra node IDs from the static graph.
 */
export function diffTraceVsTrace(
  countsA: Record<string, number>,
  countsB: Record<string, number>,
  graphNodeIds: string[] = []
): DiffEntry[] {
  const allIds = new Set<string>([
    ...Object.keys(countsA),
    ...Object.keys(countsB),
    ...graphNodeIds,
  ]);

  const entries: DiffEntry[] = [];
  for (const nodeId of allIds) {
    const ca = countsA[nodeId] ?? 0;
    const cb = countsB[nodeId] ?? 0;
    const delta = cb - ca;
    let status: DiffStatus;
    if (ca === 0 && cb > 0) {
      status = "new";
    } else if (ca > 0 && cb === 0) {
      status = "gone";
    } else if (delta > 0) {
      status = "hotter";
    } else if (delta < 0) {
      status = "colder";
    } else {
      status = "same";
    }
    entries.push({ nodeId, status, countA: ca, countB: cb, delta });
  }
  return entries.sort(byStatus);
}

/** Return `true` if any entry is classified as `"hotter"` (regression). */
export function hasRegression(entries: DiffEntry[]): boolean {
  return entries.some((e) => e.status === "hotter");
}

/**
 * Build a `Map<nodeId, DiffStatus>` from a `DiffEntry[]` for O(1) lookup
 * (e.g. used by GraphCanvas to colour nodes).
 */
export function diffToOverlay(entries: DiffEntry[]): Map<string, DiffStatus> {
  return new Map(entries.map((e) => [e.nodeId, e.status]));
}

/** Count entries per status. */
export function diffCounts(entries: DiffEntry[]): Record<DiffStatus, number> {
  const counts: Record<DiffStatus, number> = {
    hotter: 0,
    new: 0,
    gone: 0,
    colder: 0,
    same: 0,
    cold: 0,
    touched: 0,
  };
  for (const e of entries) counts[e.status] = (counts[e.status] ?? 0) + 1;
  return counts;
}
