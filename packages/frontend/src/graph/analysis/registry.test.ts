import type { Graph } from "@grackle/shared-types";
import { describe, expect, it, vi } from "vitest";
import { AnalysisRegistry } from "./registry";

const EMPTY_GRAPH: Graph = {
  version: 1,
  language: "python",
  nodes: [],
  edges: [],
};

const SMALL_GRAPH: Graph = {
  version: 1,
  language: "python",
  nodes: [
    { id: "a", kind: "file", name: "a", path: "a" },
    { id: "b", kind: "file", name: "b", path: "b" },
  ],
  edges: [{ source: "a", target: "b", kind: "import" }],
};

describe("AnalysisRegistry", () => {
  it("registers and retrieves an analysis", () => {
    const reg = new AnalysisRegistry();
    reg.register({ id: "test", compute: () => 42, cacheKey: () => "k" });
    expect(reg.get("test")).toBeDefined();
  });

  it("throws on duplicate id", () => {
    const reg = new AnalysisRegistry();
    reg.register({ id: "dup", compute: () => 1, cacheKey: () => "k" });
    expect(() =>
      reg.register({ id: "dup", compute: () => 2, cacheKey: () => "k" })
    ).toThrow("already registered");
  });

  it("getAll returns all registered analyses", () => {
    const reg = new AnalysisRegistry();
    reg.register({ id: "a1", compute: () => 1, cacheKey: () => "k" });
    reg.register({ id: "a2", compute: () => 2, cacheKey: () => "k" });
    expect(reg.getAll()).toHaveLength(2);
  });

  it("computeCached returns null for unknown id", () => {
    const reg = new AnalysisRegistry();
    expect(reg.computeCached(EMPTY_GRAPH, "nope")).toBeNull();
  });

  it("computeCached calls compute on first access", () => {
    const reg = new AnalysisRegistry();
    const compute = vi.fn((g: Graph) => g.nodes.length);
    reg.register({ id: "count", compute, cacheKey: () => "k" });
    expect(reg.computeCached<number>(SMALL_GRAPH, "count")).toBe(2);
    expect(compute).toHaveBeenCalledOnce();
  });

  it("computeCached returns cached result for same graph reference", () => {
    const reg = new AnalysisRegistry();
    const compute = vi.fn((g: Graph) => g.nodes.length);
    reg.register({ id: "count", compute, cacheKey: () => "k" });
    reg.computeCached(SMALL_GRAPH, "count");
    reg.computeCached(SMALL_GRAPH, "count");
    expect(compute).toHaveBeenCalledOnce();
  });

  it("computeCached recomputes for different graph reference", () => {
    const reg = new AnalysisRegistry();
    const compute = vi.fn((g: Graph) => g.nodes.length);
    reg.register({ id: "count", compute, cacheKey: () => "k" });
    const graph2: Graph = { ...SMALL_GRAPH, nodes: [...SMALL_GRAPH.nodes] };
    reg.computeCached(SMALL_GRAPH, "count");
    reg.computeCached(graph2, "count");
    expect(compute).toHaveBeenCalledTimes(2);
  });
});
