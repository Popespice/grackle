import { describe, expect, it } from "vitest";
import { cycleDetection } from "./cycleDetection";

function graph(
  nodes: string[],
  edges: { source: string; target: string; kind?: string }[]
) {
  return {
    version: 1 as const,
    language: "typescript",
    nodes: nodes.map((id) => ({ id, kind: "function", name: id, path: "x" })),
    edges: edges.map((e) => ({
      source: e.source,
      target: e.target,
      kind: e.kind ?? "call",
    })),
  };
}

describe("cycleDetection", () => {
  it("returns empty for an empty graph", () => {
    expect(cycleDetection(graph([], []))).toEqual([]);
  });

  it("returns empty for a chain (no cycles)", () => {
    const g = graph(
      ["a", "b", "c"],
      [
        { source: "a", target: "b" },
        { source: "b", target: "c" },
      ]
    );
    expect(cycleDetection(g)).toEqual([]);
  });

  it("returns empty for a single node with no self-loop", () => {
    expect(cycleDetection(graph(["a"], []))).toEqual([]);
  });

  it("detects a self-loop", () => {
    const result = cycleDetection(graph(["a"], [{ source: "a", target: "a" }]));
    expect(result).toHaveLength(1);
    expect(result.at(0)?.nodes).toEqual(["a"]);
    expect(result.at(0)?.size).toBe(1);
    expect(result.at(0)?.edge_kinds).toEqual(["call"]);
  });

  it("detects a simple 3-node cycle", () => {
    const g = graph(
      ["a", "b", "c"],
      [
        { source: "a", target: "b" },
        { source: "b", target: "c" },
        { source: "c", target: "a" },
      ]
    );
    const result = cycleDetection(g);
    expect(result).toHaveLength(1);
    expect(result.at(0)?.size).toBe(3);
    expect(new Set(result.at(0)?.nodes)).toEqual(new Set(["a", "b", "c"]));
    expect(result.at(0)?.edge_kinds).toEqual(["call"]);
  });

  it("stable id is sorted node IDs joined with |", () => {
    const g = graph(
      ["a", "b", "c"],
      [
        { source: "a", target: "b" },
        { source: "b", target: "c" },
        { source: "c", target: "a" },
      ]
    );
    const result = cycleDetection(g);
    expect(result).toHaveLength(1);
    expect(result.at(0)?.id).toBe("a|b|c");
  });

  it("detects two disjoint cycles", () => {
    const g = graph(
      ["a", "b", "c", "d", "e"],
      [
        { source: "a", target: "b" },
        { source: "b", target: "a" },
        { source: "d", target: "e" },
        { source: "e", target: "d" },
      ]
    );
    const result = cycleDetection(g);
    expect(result).toHaveLength(2);
    const sizes = result.map((r) => r.size).sort();
    expect(sizes).toEqual([2, 2]);
  });

  it("sorts cycles by size descending", () => {
    const g = graph(
      ["a", "b", "c", "d", "e"],
      [
        { source: "a", target: "b" },
        { source: "b", target: "a" },
        { source: "c", target: "d" },
        { source: "d", target: "e" },
        { source: "e", target: "c" },
      ]
    );
    const result = cycleDetection(g);
    expect(result.at(0)?.size ?? 0).toBeGreaterThanOrEqual(
      result.at(1)?.size ?? 0
    );
  });

  it("non-cycle nodes are excluded even when they point into a cycle", () => {
    const g = graph(
      ["a", "b", "c", "entry"],
      [
        { source: "a", target: "b" },
        { source: "b", target: "c" },
        { source: "c", target: "a" },
        { source: "entry", target: "a" },
      ]
    );
    const result = cycleDetection(g);
    expect(result).toHaveLength(1);
    expect(result.at(0)?.nodes).not.toContain("entry");
  });

  it("collects edge_kinds from all edge kinds within the SCC", () => {
    const g = graph(
      ["a", "b"],
      [
        { source: "a", target: "b", kind: "import" },
        { source: "b", target: "a", kind: "call" },
      ]
    );
    const result = cycleDetection(g);
    expect(result).toHaveLength(1);
    expect(result.at(0)?.edge_kinds).toEqual(["call", "import"]);
  });

  it("excludes edges between different SCCs from edge_kinds", () => {
    const g = graph(
      ["a", "b", "c", "d"],
      [
        { source: "a", target: "b", kind: "call" },
        { source: "b", target: "a", kind: "call" },
        { source: "c", target: "d", kind: "import" },
        { source: "a", target: "c", kind: "inherit" }, // cross-SCC
      ]
    );
    const cycles = cycleDetection(g);
    const ab = cycles.find(
      (c) => new Set(c.nodes).has("a") && new Set(c.nodes).has("b")
    );
    expect(ab).toBeDefined();
    expect(ab?.edge_kinds).toEqual(["call"]);
  });
});
