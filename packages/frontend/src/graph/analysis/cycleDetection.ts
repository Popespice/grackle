import type { Graph } from "@grackle/shared-types";

export interface CycleEntry {
  /** Stable identity: sorted node IDs joined with "|". */
  id: string;
  nodes: string[];
  size: number;
  /** Distinct edge kinds whose source AND target are both in this SCC. */
  edge_kinds: string[];
}

/**
 * Detect strongly-connected components using Tarjan's algorithm (iterative,
 * O(V+E)). Returns only actual cycles: SCCs with size > 1, plus self-loops
 * (size = 1 where the node has an edge to itself). Sorted by size descending.
 */
export function cycleDetection(graph: Graph): CycleEntry[] {
  // adjacency list: source → [{target, kind}]
  const adj = new Map<string, { target: string; kind: string }[]>();
  for (const node of graph.nodes) adj.set(node.id, []);
  for (const edge of graph.edges) {
    adj.get(edge.source)?.push({ target: edge.target, kind: edge.kind });
  }

  const indices = new Map<string, number>();
  const lowlinks = new Map<string, number>();
  const onStack = new Set<string>();
  const sccStack: string[] = [];
  const sccs: string[][] = [];
  let counter = 0;

  for (const startNode of graph.nodes) {
    if (indices.has(startNode.id)) continue;

    type Frame = { v: string; wi: number };
    const callStack: Frame[] = [];

    indices.set(startNode.id, counter);
    lowlinks.set(startNode.id, counter);
    counter++;
    sccStack.push(startNode.id);
    onStack.add(startNode.id);
    callStack.push({ v: startNode.id, wi: 0 });

    while (callStack.length > 0) {
      const frame = callStack.at(-1);
      if (frame === undefined) break; // invariant: never reached

      const { v } = frame;
      const neighbors = adj.get(v) ?? [];

      if (frame.wi < neighbors.length) {
        const neighbor = neighbors[frame.wi];
        frame.wi++;
        if (neighbor === undefined) continue; // invariant: never reached

        const w = neighbor.target;
        if (!indices.has(w)) {
          indices.set(w, counter);
          lowlinks.set(w, counter);
          counter++;
          sccStack.push(w);
          onStack.add(w);
          callStack.push({ v: w, wi: 0 });
        } else if (onStack.has(w)) {
          lowlinks.set(v, Math.min(lowlinks.get(v) ?? 0, indices.get(w) ?? 0));
        }
      } else {
        callStack.pop();
        const parentFrame = callStack.at(-1);
        if (parentFrame !== undefined) {
          lowlinks.set(
            parentFrame.v,
            Math.min(lowlinks.get(parentFrame.v) ?? 0, lowlinks.get(v) ?? 0)
          );
        }
        if ((lowlinks.get(v) ?? -1) === (indices.get(v) ?? -2)) {
          const scc: string[] = [];
          while (sccStack.length > 0) {
            const w = sccStack.pop();
            if (w === undefined) break; // invariant: never reached
            onStack.delete(w);
            scc.push(w);
            if (w === v) break;
          }
          sccs.push(scc);
        }
      }
    }
  }

  // Identify self-loop nodes
  const selfLoops = new Set<string>();
  for (const edge of graph.edges) {
    if (edge.source === edge.target) selfLoops.add(edge.source);
  }

  return sccs
    .filter(
      (scc) =>
        scc.length > 1 || (scc.length === 1 && selfLoops.has(scc[0] ?? ""))
    )
    .map((scc) => {
      const nodeSet = new Set(scc);
      const kinds = new Set<string>();
      for (const edge of graph.edges) {
        if (nodeSet.has(edge.source) && nodeSet.has(edge.target)) {
          kinds.add(edge.kind);
        }
      }
      return {
        id: [...scc].sort().join("|"),
        nodes: scc,
        size: scc.length,
        edge_kinds: [...kinds].sort(),
      };
    })
    .sort((a, b) => b.size - a.size);
}
