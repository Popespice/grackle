import { describe, expect, it } from "vitest";
import { analyses } from "./index";

describe("analysis registry (index)", () => {
  it("registers exactly 4 analyses", () => {
    expect(analyses.getAll()).toHaveLength(4);
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
});
