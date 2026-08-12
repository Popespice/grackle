/**
 * Model-predicted per-node "heat" overlay (Phase 12.2/12.3) — pure functions,
 * no I/O.
 *
 * The agent (packages/agent/src/grackle/ml_bridge.py, shipped Phase 12.2)
 * injects `{ model_version, scores: [{ node_id, score }] }` into
 * `graph.metadata.predicted_heat` when `serve --model` is active and the
 * checkpoint loads cleanly. Absence of a model / a closed capability gate /
 * a broken model means `metadata` simply lacks the `predicted_heat` key
 * entirely — never a null or empty object under that key — so parsing must
 * be fully defensive about every level of this structure.
 *
 * `scores` is a flat list (not a map) covering every node in the graph, with
 * scores rounded to 4dp in [0, 1]. There is no `max`/`count` key on the wire
 * — `parsePredictedHeat` computes `max` itself so `predictionStatuses` can
 * normalize without a second pass over the graph.
 */

export type PredictionStatus = "over" | "under" | "on-target";

/**
 * Canonical hex colours per prediction status — mirrors diff.ts's
 * DIFF_STATUS_COLORS convention.
 *
 * All values are #rrggbb — never oklch/hsl/CSS-var — because Sigma 3.x
 * parseColor only accepts hex/rgb (ADR-0015). `on-target` is the empty
 * string: it carries no override, so the graph falls through to the node's
 * kind colour, same convention as DiffStatus `"same"`.
 *
 * over  = model predicted this node hot but it ran cold (violet)
 * under = this node ran hot but the model didn't predict it — a "surprise
 *         hotspot" (amber)
 */
export const PREDICTION_STATUS_COLORS: Record<PredictionStatus, string> = {
  over: "#8b5cf6", // violet — over-predicted
  under: "#f59e0b", // amber  — surprise hotspot, under-predicted
  "on-target": "", // empty = fall through to kind colour
};

/**
 * Absolute-difference threshold (in normalized [0, 1] space) below which a
 * node is classified "on-target" rather than "over"/"under". Deliberately
 * coarse — see predictionStatuses doc comment.
 */
export const PREDICTION_THRESHOLD = 0.25;

export interface PredictedHeat {
  modelVersion: number;
  /** node_id -> predicted score, in [0, 1]. */
  scores: Map<string, number>;
  /** Max score across all entries (always > 0 for a non-null result). */
  max: number;
}

/**
 * Defensively parse graph.metadata.predicted_heat into a PredictedHeat, or
 * null if the metadata is missing/malformed/empty.
 *
 * Skips individual malformed entries (non-object, missing/non-string
 * node_id, missing/non-finite score) rather than failing the whole parse —
 * one bad entry must not hide the rest. Returns null if zero valid entries
 * survive, or if the resulting max is <= 0.
 */
export function parsePredictedHeat(
  graph: { metadata?: unknown } | null | undefined
): PredictedHeat | null {
  if (!graph) return null;
  const metadata = graph.metadata;
  if (typeof metadata !== "object" || metadata === null) return null;

  const predictedHeat = (metadata as Record<string, unknown>).predicted_heat;
  if (typeof predictedHeat !== "object" || predictedHeat === null) return null;

  const record = predictedHeat as Record<string, unknown>;
  const modelVersion =
    typeof record.model_version === "number" &&
    Number.isFinite(record.model_version)
      ? record.model_version
      : 0;

  const rawScores = record.scores;
  if (!Array.isArray(rawScores)) return null;

  const scores = new Map<string, number>();
  let max = -Infinity;
  for (const entry of rawScores) {
    if (typeof entry !== "object" || entry === null) continue;
    const { node_id: nodeId, score } = entry as Record<string, unknown>;
    if (typeof nodeId !== "string" || nodeId.length === 0) continue;
    if (typeof score !== "number" || !Number.isFinite(score)) continue;
    scores.set(nodeId, score);
    if (score > max) max = score;
  }

  if (scores.size === 0) return null;
  if (max <= 0) return null;

  return { modelVersion, scores, max };
}

/**
 * Classify every node as `"over"` | `"under"` | `"on-target"` by comparing
 * the model's normalized prediction against actual (already count-weighted,
 * from the caller) hit counts.
 *
 * Normalization mirrors the shipped training-target normalization in
 * packages/nn/src/grackle_nn/ml/labels.py:82-101 (`np.log1p(counts) /
 * np.log1p(max_count)`). Note the frontend uses JS `Math.log1p`, a third
 * log1p implementation distinct from both numpy's and Python's `math.log1p`
 * (which disagreed by 1 ULP and caused a real bug fixed in Phase 12.2's
 * `make_targets` — see CLAUDE.md's Phase 12.2 section if curious). The
 * +/-0.25 threshold is deliberately coarse enough to absorb that class of
 * floating-point noise — do not try to make this exact-equal to the Python
 * side.
 *
 * ```
 * anorm = log1p(actualCount) / log1p(maxActualCount)   (0 when maxActualCount <= 0)
 * pnorm = predictedScore / predicted.max
 * universe = union of (all node_ids in predicted.scores) and
 *            (all node_ids in actual with actual[id] > 0)
 *   — a node absent from predicted.scores gets pnorm = 0; a node absent
 *     from actual gets an actual count of 0.
 * status:
 *   |pnorm - anorm| <= PREDICTION_THRESHOLD -> "on-target"
 *   else pnorm > anorm -> "over"
 *   else -> "under"
 * ```
 *
 * When maxActualCount <= 0 (no actual hits anywhere), anorm is 0 for every
 * node, so the formula above still applies unchanged.
 */
export function predictionStatuses(
  predicted: PredictedHeat,
  actual: Record<string, number>
): Map<string, PredictionStatus> {
  let maxActualCount = 0;
  for (const count of Object.values(actual)) {
    if (count > maxActualCount) maxActualCount = count;
  }
  const logMaxActual = Math.log1p(maxActualCount);

  const universe = new Set<string>(predicted.scores.keys());
  for (const [nodeId, count] of Object.entries(actual)) {
    if (count > 0) universe.add(nodeId);
  }

  const statuses = new Map<string, PredictionStatus>();
  for (const nodeId of universe) {
    const actualCount = actual[nodeId] ?? 0;
    const anorm =
      maxActualCount > 0 ? Math.log1p(actualCount) / logMaxActual : 0;
    const predictedScore = predicted.scores.get(nodeId) ?? 0;
    const pnorm = predictedScore / predicted.max;

    const diff = pnorm - anorm;
    let status: PredictionStatus;
    if (Math.abs(diff) <= PREDICTION_THRESHOLD) {
      status = "on-target";
    } else if (diff > 0) {
      status = "over";
    } else {
      status = "under";
    }
    statuses.set(nodeId, status);
  }
  return statuses;
}
