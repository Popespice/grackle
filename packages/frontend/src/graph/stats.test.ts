import type { Graph } from "@grackle/shared-types";
import { describe, expect, it } from "vitest";
import { countByKind, orphans, topByInDegree } from "./stats";

const GRAPH: Graph = {
  version: 1,
  language: "python",
  nodes: [
    { id: "main.py:main", kind: "function", name: "main", path: "main.py" },
    { id: "main.py:helper", kind: "function", name: "helper", path: "main.py" },
    { id: "models.py:User", kind: "class", name: "User", path: "models.py" },
    { id: "models.py:Admin", kind: "class", name: "Admin", path: "models.py" },
    { id: "utils.py:hash", kind: "function", name: "hash", path: "utils.py" },
    { id: "main.py", kind: "file", name: "main.py", path: "main.py" },
    { id: "models.py", kind: "file", name: "models.py", path: "models.py" },
    { id: "utils.py", kind: "file", name: "utils.py", path: "utils.py" },
  ],
  edges: [
    // import edges (don't count toward non-import in-degree)
    { source: "main.py", target: "models.py", kind: "import" },
    { source: "main.py", target: "utils.py", kind: "import" },
    // call edges — hash is called by both main and helper
    { source: "main.py:main", target: "utils.py:hash", kind: "call" },
    { source: "main.py:helper", target: "utils.py:hash", kind: "call" },
    // inherit edge
    { source: "models.py:Admin", target: "models.py:User", kind: "inherit" },
    // call from main to helper
    { source: "main.py:main", target: "main.py:helper", kind: "call" },
  ],
};

describe("countByKind", () => {
  it("returns counts sorted by count descending", () => {
    const result = countByKind(GRAPH);
    const kindNames = result.map((r) => r.kind);
    expect(kindNames).toContain("function");
    expect(kindNames).toContain("class");
    expect(kindNames).toContain("file");
    // function has 3, class has 2, file has 3 (tie)
    const fn = result.find((r) => r.kind === "function");
    const cls = result.find((r) => r.kind === "class");
    expect(fn?.count).toBe(3);
    expect(cls?.count).toBe(2);
  });

  it("handles empty graph", () => {
    const empty: Graph = {
      version: 1,
      language: "python",
      nodes: [],
      edges: [],
    };
    expect(countByKind(empty)).toEqual([]);
  });
});

describe("topByInDegree", () => {
  it("returns nodes sorted by total in-degree", () => {
    const result = topByInDegree(GRAPH, 3);
    expect(result.length).toBe(3);
    // hash receives 2 call edges — should be at or near the top among non-file nodes
    const hashEntry = result.find((e) => e.node.id === "utils.py:hash");
    expect(hashEntry).toBeDefined();
    expect(hashEntry?.inDegree).toBe(2);
  });

  it("respects the n limit", () => {
    expect(topByInDegree(GRAPH, 2).length).toBe(2);
  });

  it("counts ALL edge types for in-degree, including import", () => {
    const result = topByInDegree(GRAPH);
    // models.py has 1 import edge pointing to it
    const modelsFile = result.find((e) => e.node.id === "models.py");
    expect(modelsFile?.inDegree).toBeGreaterThanOrEqual(1);
  });
});

describe("orphans", () => {
  it("identifies nodes with no non-import inbound edges", () => {
    const result = orphans(GRAPH);
    const ids = result.map((n) => n.id);
    // main.py:main is not called by anything (only imports exist pointing to files)
    expect(ids).toContain("main.py:main");
    // hash has call edges pointing to it — NOT an orphan
    expect(ids).not.toContain("utils.py:hash");
  });

  it("returns all nodes when graph has only import edges", () => {
    const importOnly: Graph = {
      version: 1,
      language: "python",
      nodes: [
        { id: "a.py", kind: "file", name: "a.py", path: "a.py" },
        { id: "b.py", kind: "file", name: "b.py", path: "b.py" },
      ],
      edges: [{ source: "a.py", target: "b.py", kind: "import" }],
    };
    // Both nodes are orphans since no non-import edges exist
    expect(orphans(importOnly).length).toBe(2);
  });

  it("returns empty list when all nodes have non-import inbound edges", () => {
    const dense: Graph = {
      version: 1,
      language: "python",
      nodes: [
        { id: "a.py:f", kind: "function", name: "f", path: "a.py" },
        { id: "b.py:g", kind: "function", name: "g", path: "b.py" },
      ],
      edges: [
        { source: "a.py:f", target: "b.py:g", kind: "call" },
        { source: "b.py:g", target: "a.py:f", kind: "call" },
      ],
    };
    expect(orphans(dense)).toEqual([]);
  });
});
