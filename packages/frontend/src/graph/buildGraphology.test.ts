import { describe, expect, it } from "vitest";
import { buildGraphology } from "./buildGraphology";

const SIMPLE_GRAPH = {
  version: 1,
  language: "python",
  nodes: [
    { id: "a.py:App", kind: "class", name: "App", path: "a.py", line: 5 },
    { id: "b.py:main", kind: "function", name: "main", path: "b.py" },
    { id: "c.py:util", kind: "function", name: "util", path: "c.py" },
  ],
  edges: [
    { source: "a.py:App", target: "b.py:main", kind: "call" },
    { source: "b.py:main", target: "c.py:util", kind: "call" },
    { source: "a.py:App", target: "c.py:util", kind: "call" },
  ],
};

describe("buildGraphology", () => {
  it("creates a graph with the correct node count", () => {
    const g = buildGraphology(SIMPLE_GRAPH);
    expect(g.order).toBe(3);
  });

  it("creates a graph with the correct edge count", () => {
    const g = buildGraphology(SIMPLE_GRAPH);
    expect(g.size).toBe(3);
  });

  it("stores node attributes correctly", () => {
    const g = buildGraphology(SIMPLE_GRAPH);
    const attrs = g.getNodeAttributes("a.py:App");
    expect(attrs.kind).toBe("class");
    expect(attrs.name).toBe("App");
    expect(attrs.path).toBe("a.py");
    expect(attrs.line).toBe(5);
    expect(attrs.label).toBe("App");
  });

  it("stores edge kind attribute", () => {
    const g = buildGraphology(SIMPLE_GRAPH);
    const edges = g.edges("a.py:App", "b.py:main");
    expect(edges.length).toBeGreaterThan(0);
    const attrs = g.getEdgeAttributes(edges[0] as string);
    expect(attrs.kind).toBe("call");
  });

  it("initialises nodes with finite x/y positions", () => {
    const g = buildGraphology(SIMPLE_GRAPH);
    for (const node of g.nodes()) {
      const { x, y } = g.getNodeAttributes(node);
      expect(Number.isFinite(x)).toBe(true);
      expect(Number.isFinite(y)).toBe(true);
    }
  });

  it("handles parallel edges (MultiDirectedGraph)", () => {
    const graph = {
      version: 1,
      language: "python",
      nodes: [
        { id: "a", kind: "file", name: "a", path: "a.py" },
        { id: "b", kind: "file", name: "b", path: "b.py" },
      ],
      edges: [
        { source: "a", target: "b", kind: "import" },
        { source: "a", target: "b", kind: "call" },
      ],
    };
    const g = buildGraphology(graph);
    expect(g.size).toBe(2);
    expect(g.edges("a", "b").length).toBe(2);
  });

  it("skips duplicate node ids instead of throwing", () => {
    // Regression guard: adapters can legitimately emit a duplicate ID (e.g.
    // a Python @property getter/setter pair sharing a name) — graphology's
    // addNode throws on a repeat, which used to crash GraphCanvas with no
    // recovery path.
    const graph = {
      version: 1,
      language: "python",
      nodes: [
        { id: "a.py:Foo.bar", kind: "method", name: "bar", path: "a.py" },
        { id: "a.py:Foo.bar", kind: "method", name: "bar", path: "a.py" },
      ],
      edges: [],
    };
    expect(() => buildGraphology(graph)).not.toThrow();
    const g = buildGraphology(graph);
    expect(g.order).toBe(1);
  });

  it("skips edges whose source node is missing", () => {
    const graph = {
      version: 1,
      language: "python",
      nodes: [{ id: "b", kind: "file", name: "b", path: "b.py" }],
      edges: [{ source: "ghost", target: "b", kind: "import" }],
    };
    const g = buildGraphology(graph);
    expect(g.size).toBe(0);
  });

  it("skips edges whose target node is missing", () => {
    const graph = {
      version: 1,
      language: "python",
      nodes: [{ id: "a", kind: "file", name: "a", path: "a.py" }],
      edges: [{ source: "a", target: "ghost", kind: "import" }],
    };
    const g = buildGraphology(graph);
    expect(g.size).toBe(0);
  });

  it("computes in-degree correctly via the graphology API", () => {
    const g = buildGraphology(SIMPLE_GRAPH);
    // c.py:util is targeted by both a.py:App and b.py:main
    expect(g.inDegree("c.py:util")).toBe(2);
    // a.py:App is never a target
    expect(g.inDegree("a.py:App")).toBe(0);
  });

  it("returns an empty graph for an empty input", () => {
    const g = buildGraphology({
      version: 1,
      language: "python",
      nodes: [],
      edges: [],
    });
    expect(g.order).toBe(0);
    expect(g.size).toBe(0);
  });
});
