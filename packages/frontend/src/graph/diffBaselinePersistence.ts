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
    return JSON.parse(raw) as Record<string, number>;
  } catch {
    return null;
  }
}
