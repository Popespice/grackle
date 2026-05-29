import { describe, expect, it } from "vitest";
import { analyses } from "./index";

describe("analysis registry (index)", () => {
  it("registers exactly 5 analyses", () => {
    expect(analyses.getAll()).toHaveLength(5);
  });

  it("registers count-by-kind", () => {
    expect(analyses.get("count-by-kind")).toBeDefined();
  });

  it("registers top-in-degree", () => {
    expect(analyses.get("top-in-degree")).toBeDefined();
  });

  it("registers orphans", () => {
    expect(analyses.get("orphans")).toBeDefined();
  });

  it("registers hub-score", () => {
    expect(analyses.get("hub-score")).toBeDefined();
  });

  it("registers cycles", () => {
    expect(analyses.get("cycles")).toBeDefined();
  });

  it("count-by-kind returns an array", () => {
    const graph = {
      version: 1,
      language: "python",
      nodes: [{ id: "a", kind: "file", name: "a", path: "a" }],
      edges: [],
    };
    const result = analyses.computeCached<unknown[]>(graph, "count-by-kind");
    expect(Array.isArray(result)).toBe(true);
  });

  it("hub-score results round-trip through cache", () => {
    const graph = {
      version: 1,
      language: "python",
      nodes: [
        { id: "x", kind: "file", name: "x", path: "x" },
        { id: "y", kind: "class", name: "Y", path: "x" },
      ],
      edges: [{ source: "x", target: "y", kind: "import" }],
    };
    const r1 = analyses.computeCached(graph, "hub-score");
    const r2 = analyses.computeCached(graph, "hub-score");
    expect(r1).toBe(r2); // same reference = cached
  });

  it("hub-score rehydrates agent metadata {node_id, score} → {node, score}", () => {
    // Regression: the agent emits {node_id, score}; StatsPanel reads entry.node.
    // The transform must produce full GraphNode objects, not pass node_id through.
    const graph = {
      version: 1,
      language: "python",
      nodes: [
        { id: "x", kind: "file", name: "X", path: "x" },
        { id: "y", kind: "class", name: "Y", path: "x" },
      ],
      edges: [{ source: "x", target: "y", kind: "import" }],
      metadata: {
        hub_score: [
          { node_id: "y", score: 1 },
          { node_id: "x", score: -1 },
        ],
      },
    };
    const result =
      analyses.computeCached<
        Array<{ node: { id: string; name: string }; score: number }>
      >(graph, "hub-score") ?? [];
    expect(result).toHaveLength(2);
    const first = result[0];
    expect(first?.node.id).toBe("y");
    expect(first?.node.name).toBe("Y");
    expect(first?.score).toBe(1);
  });

  it("hub-score falls back to local compute when metadata is absent", () => {
    const graph = {
      version: 1,
      language: "python",
      nodes: [
        { id: "x", kind: "file", name: "X", path: "x" },
        { id: "y", kind: "class", name: "Y", path: "x" },
      ],
      edges: [{ source: "x", target: "y", kind: "import" }],
    };
    const result =
      analyses.computeCached<Array<{ node: { id: string }; score: number }>>(
        graph,
        "hub-score"
      ) ?? [];
    // Local hubScore also returns {node, score}, so consumers stay consistent.
    const first = result[0];
    expect(first?.node).toBeDefined();
    expect(typeof first?.score).toBe("number");
  });

  it("cycles uses agent metadata directly (shape already matches CycleEntry)", () => {
    const graph = {
      version: 1,
      language: "python",
      nodes: [
        { id: "a", kind: "file", name: "a", path: "a" },
        { id: "b", kind: "file", name: "b", path: "b" },
      ],
      edges: [
        { source: "a", target: "b", kind: "call" },
        { source: "b", target: "a", kind: "call" },
      ],
      metadata: {
        cycles: [
          { id: "a|b", nodes: ["a", "b"], size: 2, edge_kinds: ["call"] },
        ],
      },
    };
    const result =
      analyses.computeCached<Array<{ id: string; size: number }>>(
        graph,
        "cycles"
      ) ?? [];
    expect(result).toHaveLength(1);
    const first = result[0];
    expect(first?.id).toBe("a|b");
    expect(first?.size).toBe(2);
  });
});
