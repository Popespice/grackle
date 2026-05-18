import type { Graph } from "@grackle/shared-types";
import { describe, expect, it } from "vitest";
import { graphCacheKey } from "./cacheKey";

const BASE_GRAPH: Graph = {
  version: 1,
  language: "python",
  nodes: [
    { id: "a", kind: "file", name: "a", path: "a" },
    { id: "b", kind: "class", name: "B", path: "a" },
  ],
  edges: [
    { source: "a", target: "b", kind: "import" },
    { source: "b", target: "a", kind: "call" },
  ],
};

describe("graphCacheKey", () => {
  it("returns a 64-character hex string", async () => {
    const key = await graphCacheKey(BASE_GRAPH);
    expect(key).toMatch(/^[0-9a-f]{64}$/);
  });

  it("is stable for the same graph", async () => {
    const k1 = await graphCacheKey(BASE_GRAPH);
    const k2 = await graphCacheKey(BASE_GRAPH);
    expect(k1).toBe(k2);
  });

  it("is stable regardless of edge array order", async () => {
    const [e0, e1] = BASE_GRAPH.edges as [Graph["edges"][0], Graph["edges"][0]];
    const shuffled: Graph = {
      ...BASE_GRAPH,
      edges: [e1, e0],
    };
    const k1 = await graphCacheKey(BASE_GRAPH);
    const k2 = await graphCacheKey(shuffled);
    expect(k1).toBe(k2);
  });

  it("differs when a node is added", async () => {
    const k1 = await graphCacheKey(BASE_GRAPH);
    const bigger: Graph = {
      ...BASE_GRAPH,
      nodes: [
        ...BASE_GRAPH.nodes,
        { id: "c", kind: "file", name: "c", path: "c" },
      ],
    };
    const k2 = await graphCacheKey(bigger);
    expect(k1).not.toBe(k2);
  });

  it("differs when an edge kind changes", async () => {
    const k1 = await graphCacheKey(BASE_GRAPH);
    const [e0, e1] = BASE_GRAPH.edges as [Graph["edges"][0], Graph["edges"][0]];
    const modified: Graph = {
      ...BASE_GRAPH,
      edges: [e0, { ...e1, kind: "inherit" }],
    };
    const k2 = await graphCacheKey(modified);
    expect(k1).not.toBe(k2);
  });
});
