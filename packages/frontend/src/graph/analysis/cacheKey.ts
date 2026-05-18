import type { Graph } from "@grackle/shared-types";

/**
 * Compute a SHA-256 content hash of a graph.
 *
 * The canonical form is: sorted node IDs joined by newline, then a separator,
 * then edge tuples (source|target|kind) sorted lexicographically.
 *
 * Returns a hex string. Stable across render cycles for the same logical graph
 * content regardless of array ordering.
 */
export async function graphCacheKey(graph: Graph): Promise<string> {
  const nodeIds = [...graph.nodes.map((n) => n.id)].sort().join("\n");
  const edgeTuples = [
    ...graph.edges.map((e) => `${e.source}|${e.target}|${e.kind}`),
  ]
    .sort()
    .join("\n");
  const canonical = `${nodeIds}\n---\n${edgeTuples}`;
  const encoded = new TextEncoder().encode(canonical);
  const buffer = await crypto.subtle.digest("SHA-256", encoded);
  return Array.from(new Uint8Array(buffer))
    .map((b) => b.toString(16).padStart(2, "0"))
    .join("");
}
