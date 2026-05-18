import type { Graph } from "@grackle/shared-types";
import { describe, expect, it } from "vitest";
import { hubScore } from "./hubScore";

const GRAPH: Graph = {
  version: 1,
  language: "python",
  nodes: [
    { id: "a", kind: "file", name: "a", path: "a" },
    { id: "b", kind: "class", name: "B", path: "a" },
    { id: "c", kind: "function", name: "c", path: "a" },
  ],
  edges: [
    { source: "a", target: "b", kind: "import" },
    { source: "c", target: "b", kind: "call" },
    { source: "a", target: "c", kind: "call" },
  ],
};

describe("hubScore", () => {
  it("returns an entry for each node", () => {
    expect(hubScore(GRAPH)).toHaveLength(3);
  });

  it("sorts descending by score", () => {
    const scores = hubScore(GRAPH).map((e) => e.score);
    for (let i = 0; i + 1 < scores.length; i++) {
      const curr = scores[i] ?? 0;
      const next = scores[i + 1] ?? 0;
      expect(curr).toBeGreaterThanOrEqual(next);
    }
  });

  it("b has the highest score (2 in, 0 out = +2)", () => {
    const [top] = hubScore(GRAPH);
    expect(top).toBeDefined();
    expect(top?.node.id).toBe("b");
    expect(top?.score).toBe(2);
  });

  it("a has score -1 (1 in from nowhere but 2 out)", () => {
    const entries = hubScore(GRAPH);
    const aEntry = entries.find((e) => e.node.id === "a");
    // a: inDegree=0 (nothing points to a), outDegree=2 → score = -2
    expect(aEntry).toBeDefined();
    expect(aEntry?.score).toBe(-2);
  });

  it("handles empty graph", () => {
    const empty: Graph = {
      version: 1,
      language: "python",
      nodes: [],
      edges: [],
    };
    expect(hubScore(empty)).toEqual([]);
  });
});
