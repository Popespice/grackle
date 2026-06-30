import type { Graph } from "@grackle/shared-types";
import { graphCacheKey } from "./analysis/cacheKey";

/**
 * Persist / restore the DiffPanel baseline (ADR-0021 amendment, Phase 9.3).
 *
 * Keyed by `graphCacheKey` so a baseline only restores onto the same logical
 * project (same node/edge content), never a different one. Persistence is
 * driven from explicit user actions (Set/Clear baseline) in DiffPanel, never
 * from a store subscriber — `setGraph` clears `diffBaseline` on every
 * `static_graph` push (graph-scoped invariant), so a blind subscriber would
 * delete the stored value before the restore effect could read it back.
 */

const KEY_PREFIX = "grackle:diff-baseline:";

function keyFor(graph: Graph): Promise<string> {
  return graphCacheKey(graph).then((hash) => KEY_PREFIX + hash);
}

/**
 * True if `value` is a non-empty plain object mapping node ids to
 * non-negative finite counts. Empty objects are rejected: a `{}` baseline
 * would classify every current node as `hotter` (0 → N) and show a phantom
 * regression, so a degenerate empty snapshot must not round-trip.
 */
function isBaseline(value: unknown): value is Record<string, number> {
  if (typeof value !== "object" || value === null || Array.isArray(value)) {
    return false;
  }
  const values = Object.values(value);
  if (values.length === 0) return false;
  return values.every(
    (v) => typeof v === "number" && Number.isFinite(v) && v >= 0
  );
}

export async function persistBaseline(
  graph: Graph,
  baseline: Record<string, number> | null
): Promise<void> {
  if (typeof sessionStorage === "undefined") return;
  try {
    const key = await keyFor(graph);
    if (baseline === null) {
      sessionStorage.removeItem(key);
    } else {
      sessionStorage.setItem(key, JSON.stringify(baseline));
    }
  } catch {
    // Private-mode / quota / serialization errors — persistence is
    // best-effort, never fatal to the diff feature itself.
  }
}

export async function restoreBaseline(
  graph: Graph
): Promise<Record<string, number> | null> {
  if (typeof sessionStorage === "undefined") return null;
  try {
    const key = await keyFor(graph);
    const raw = sessionStorage.getItem(key);
    if (raw === null) return null;
    const parsed: unknown = JSON.parse(raw);
    return isBaseline(parsed) ? parsed : null;
  } catch {
    return null;
  }
}
