/**
 * Build a node_id -> hit-count map from a trace-event array. Shared by
 * DiffPanel (trace-vs-trace baseline counting) and PredictedHeatPanel
 * (vs-actual classification) — both previously carried an identical local
 * copy; extracted here so the known unweighted-vs-count-weighted gap
 * documented below only needs fixing once.
 *
 * Deliberately unweighted (+1 per event, ignoring `metadata.count`) —
 * unlike the count-weighted `agentHeat` (server-computed) and the training
 * targets in `packages/nn/.../ml/labels.py`. Identical to count-weighted for
 * Python traces (no `metadata.count` there); for Go/Rust coverage traces the
 * count-weighted `agentHeat` is preferred anyway, so this unweighted
 * fallback only runs when `agentHeat` is unavailable.
 */
export function countEvents(
  events: Array<{ node_id: string }>
): Record<string, number> {
  const counts: Record<string, number> = {};
  for (const ev of events) {
    counts[ev.node_id] = (counts[ev.node_id] ?? 0) + 1;
  }
  return counts;
}
