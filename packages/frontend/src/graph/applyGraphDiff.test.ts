import type { Graph } from "@grackle/shared-types";
import { MultiDirectedGraph } from "graphology";
import { describe, expect, it, vi } from "vitest";
import { applyGraphDiff, isEmptyDiff } from "./applyGraphDiff";
import { buildGraphology, type GrackleMultiGraph } from "./buildGraphology";

const BASE_GRAPH: Graph = {
  version: 1,
  language: "python",
  nodes: [
    {
      id: "a.py:A",
      kind: "class",
      name: "A",
      path: "a.py",
      line: 5,
      metadata: { foo: "bar" },
    },
    { id: "b.py:B", kind: "function", name: "B", path: "b.py" },
  ],
  edges: [
    { source: "a.py:A", target: "b.py:B", kind: "call", metadata: { line: 5 } },
  ],
};

/** Deterministic rng producing a fixed sequence, cycling once exhausted. */
function sequenceRng(values: number[]): () => number {
  let i = 0;
  return () => values[i++ % values.length] as number;
}

function attachSpies(g: GrackleMultiGraph) {
  const spies = {
    nodeAdded: vi.fn(),
    nodeDropped: vi.fn(),
    nodeAttributesUpdated: vi.fn(),
    edgeAdded: vi.fn(),
    edgeDropped: vi.fn(),
  };
  for (const [event, spy] of Object.entries(spies)) {
    g.on(event as keyof typeof spies, spy);
  }
  return spies;
}

describe("applyGraphDiff — survivor attribute merge", () => {
  it("never touches x/y/size/color/hidden for a surviving node", () => {
    const live = buildGraphology(BASE_GRAPH);
    const before = { ...live.getNodeAttributes("a.py:A") };

    const incoming: Graph = {
      ...BASE_GRAPH,
      nodes: [
        {
          id: "a.py:A",
          kind: "class",
          name: "A",
          path: "a.py",
          line: 5,
          metadata: { foo: "bar" },
        },
        BASE_GRAPH.nodes[1] as Graph["nodes"][number],
      ],
    };
    applyGraphDiff(live, incoming);

    const after = live.getNodeAttributes("a.py:A");
    expect(after.x).toBe(before.x);
    expect(after.y).toBe(before.y);
    expect(after.size).toBe(before.size);
    expect(after.color).toBe(before.color);
    expect(after.hidden).toBe(before.hidden);
  });

  it("merges changed wire attrs (line, metadata) without flagging unrelated nodes", () => {
    const live = buildGraphology(BASE_GRAPH);
    const incoming: Graph = {
      ...BASE_GRAPH,
      nodes: [
        {
          id: "a.py:A",
          kind: "class",
          name: "A",
          path: "a.py",
          line: 8,
          metadata: { foo: "baz" },
        },
        BASE_GRAPH.nodes[1] as Graph["nodes"][number],
      ],
    };
    const result = applyGraphDiff(live, incoming);

    expect(live.getNodeAttribute("a.py:A", "line")).toBe(8);
    expect(live.getNodeAttribute("a.py:A", "metadata")).toEqual({ foo: "baz" });
    expect(result.attrChangedNodes).toEqual(["a.py:A"]);
  });

  it("removes line/metadata attributes that disappeared from the wire", () => {
    const live = buildGraphology(BASE_GRAPH);
    const incoming: Graph = {
      ...BASE_GRAPH,
      nodes: [
        { id: "a.py:A", kind: "class", name: "A", path: "a.py" },
        BASE_GRAPH.nodes[1] as Graph["nodes"][number],
      ],
    };
    applyGraphDiff(live, incoming);

    expect(live.hasNodeAttribute("a.py:A", "line")).toBe(false);
    expect(live.hasNodeAttribute("a.py:A", "metadata")).toBe(false);
  });
});

describe("applyGraphDiff — new node seeding", () => {
  it("seeds a new node near its surviving neighbor, at the jitter radius", () => {
    const base: Graph = {
      version: 1,
      language: "python",
      nodes: [
        { id: "a", kind: "function", name: "a", path: "a.py" },
        { id: "b", kind: "function", name: "b", path: "b.py" },
      ],
      edges: [],
    };
    const live = buildGraphology(base);
    live.setNodeAttribute("a", "x", 0);
    live.setNodeAttribute("a", "y", 0);
    live.setNodeAttribute("b", "x", 10);
    live.setNodeAttribute("b", "y", 0);
    // bbox diagonal = 10 -> radius = 0.05 * 10 = 0.5

    const incoming: Graph = {
      ...base,
      nodes: [
        ...base.nodes,
        { id: "c", kind: "function", name: "c", path: "c.py" },
      ],
      edges: [{ source: "c", target: "a", kind: "call" }],
    };
    const result = applyGraphDiff(live, incoming, { rng: sequenceRng([0]) });

    expect(result.addedNodes).toEqual(["c"]);
    const c = live.getNodeAttributes("c");
    expect(c.x).toBeCloseTo(0.5, 10);
    expect(c.y).toBeCloseTo(0, 10);
    expect(Number.isFinite(c.x)).toBe(true);
    expect(Number.isFinite(c.y)).toBe(true);
  });

  it("consumes exactly one rng draw per new node seeded near a neighbor", () => {
    // A neighbor-seeded node draws rng() exactly once (the jitter angle).
    // sequenceRng([0]) can't detect draw-count drift because it returns 0 for
    // every call; a counting rng pins the count so a change in rng-draw order
    // (which would desync deterministic replay in real use) fails this test.
    const base: Graph = {
      version: 1,
      language: "python",
      nodes: [{ id: "a", kind: "function", name: "a", path: "a.py" }],
      edges: [],
    };
    const live = buildGraphology(base);
    live.setNodeAttribute("a", "x", 0);
    live.setNodeAttribute("a", "y", 0);

    let rngCalls = 0;
    const rng = () => {
      rngCalls++;
      return 0;
    };
    const incoming: Graph = {
      ...base,
      nodes: [
        ...base.nodes,
        { id: "c", kind: "function", name: "c", path: "c.py" },
      ],
      edges: [{ source: "c", target: "a", kind: "call" }],
    };
    applyGraphDiff(live, incoming, { rng });

    expect(rngCalls).toBe(1);
  });

  it("chains a new node off another new node added earlier this apply", () => {
    const base: Graph = {
      version: 1,
      language: "python",
      nodes: [
        { id: "a", kind: "function", name: "a", path: "a.py" },
        { id: "b", kind: "function", name: "b", path: "b.py" },
      ],
      edges: [],
    };
    const live = buildGraphology(base);
    live.setNodeAttribute("a", "x", 0);
    live.setNodeAttribute("a", "y", 0);
    live.setNodeAttribute("b", "x", 10);
    live.setNodeAttribute("b", "y", 0);
    // bbox diagonal = 10 -> radius = 0.5, fixed for the whole apply

    const incoming: Graph = {
      ...base,
      nodes: [
        ...base.nodes,
        { id: "c", kind: "function", name: "c", path: "c.py" },
        { id: "d", kind: "function", name: "d", path: "d.py" },
      ],
      // c connects to survivor a; d connects ONLY to new node c (chain).
      edges: [
        { source: "c", target: "a", kind: "call" },
        { source: "d", target: "c", kind: "call" },
      ],
    };
    const result = applyGraphDiff(live, incoming, { rng: sequenceRng([0]) });

    expect(new Set(result.addedNodes)).toEqual(new Set(["c", "d"]));
    const c = live.getNodeAttributes("c");
    const d = live.getNodeAttributes("d");
    expect(c.x).toBeCloseTo(0.5, 10);
    expect(c.y).toBeCloseTo(0, 10);
    // d chains off c's position (0.5, 0), same fixed radius 0.5.
    expect(d.x).toBeCloseTo(1.0, 10);
    expect(d.y).toBeCloseTo(0, 10);
  });

  it("falls back to the centroid of the live graph for an unconnected new node", () => {
    const base: Graph = {
      version: 1,
      language: "python",
      nodes: [
        { id: "a", kind: "function", name: "a", path: "a.py" },
        { id: "b", kind: "function", name: "b", path: "b.py" },
      ],
      edges: [],
    };
    const live = buildGraphology(base);
    live.setNodeAttribute("a", "x", 0);
    live.setNodeAttribute("a", "y", 0);
    live.setNodeAttribute("b", "x", 10);
    live.setNodeAttribute("b", "y", 0);
    // centroid = (5, 0), bbox diagonal = 10 -> radius = 0.5

    const incoming: Graph = {
      ...base,
      nodes: [
        ...base.nodes,
        { id: "e", kind: "function", name: "e", path: "e.py" },
      ],
      edges: [], // e has no edges at all
    };
    applyGraphDiff(live, incoming, { rng: sequenceRng([0]) });

    const e = live.getNodeAttributes("e");
    expect(e.x).toBeCloseTo(5.5, 10);
    expect(e.y).toBeCloseTo(0, 10);
  });

  it("falls back to rng-random placement when the live graph is empty", () => {
    const live = new MultiDirectedGraph() as GrackleMultiGraph;
    const incoming: Graph = {
      version: 1,
      language: "python",
      nodes: [{ id: "f", kind: "function", name: "f", path: "f.py" }],
      edges: [],
    };
    applyGraphDiff(live, incoming, { rng: sequenceRng([0.3, 0.7]) });

    const f = live.getNodeAttributes("f");
    expect(f.x).toBe(0.3);
    expect(f.y).toBe(0.7);
  });
});

describe("applyGraphDiff — parallel-edge multiset matching", () => {
  const nodesOnly: Graph["nodes"] = [
    { id: "a", kind: "function", name: "a", path: "a.py" },
    { id: "b", kind: "function", name: "b", path: "b.py" },
  ];

  it("drops exactly one parallel edge when the incoming count decreases", () => {
    const live = buildGraphology({
      version: 1,
      language: "python",
      nodes: nodesOnly,
      edges: [
        { source: "a", target: "b", kind: "call", metadata: { line: 5 } },
        { source: "a", target: "b", kind: "call", metadata: { line: 5 } },
      ],
    });
    expect(live.edges("a", "b").length).toBe(2);

    const result = applyGraphDiff(live, {
      version: 1,
      language: "python",
      nodes: nodesOnly,
      edges: [
        { source: "a", target: "b", kind: "call", metadata: { line: 5 } },
      ],
    });

    expect(result.removedEdgeCount).toBe(1);
    expect(result.addedEdges).toEqual([]);
    expect(live.edges("a", "b").length).toBe(1);
  });

  it("adds the delta of parallel edges when the incoming count increases", () => {
    const live = buildGraphology({
      version: 1,
      language: "python",
      nodes: nodesOnly,
      edges: [
        { source: "a", target: "b", kind: "call", metadata: { line: 5 } },
      ],
    });

    const result = applyGraphDiff(live, {
      version: 1,
      language: "python",
      nodes: nodesOnly,
      edges: [
        { source: "a", target: "b", kind: "call", metadata: { line: 5 } },
        { source: "a", target: "b", kind: "call", metadata: { line: 5 } },
        { source: "a", target: "b", kind: "call", metadata: { line: 5 } },
      ],
    });

    expect(result.addedEdges.length).toBe(2);
    expect(result.removedEdgeCount).toBe(0);
    expect(live.edges("a", "b").length).toBe(3);
  });

  it("keeps parallel edges with different evidence lines as independent buckets", () => {
    const graph: Graph = {
      version: 1,
      language: "python",
      nodes: nodesOnly,
      edges: [
        { source: "a", target: "b", kind: "call", metadata: { line: 5 } },
        { source: "a", target: "b", kind: "call", metadata: { line: 9 } },
      ],
    };
    const live = buildGraphology(graph);

    const result = applyGraphDiff(live, graph);

    expect(result.addedEdges).toEqual([]);
    expect(result.removedEdgeCount).toBe(0);
    expect(live.edges("a", "b").length).toBe(2);
  });

  it("treats a line-only change as drop+add, not an in-place update", () => {
    const live = buildGraphology({
      version: 1,
      language: "python",
      nodes: nodesOnly,
      edges: [
        { source: "a", target: "b", kind: "call", metadata: { line: 5 } },
      ],
    });

    const result = applyGraphDiff(live, {
      version: 1,
      language: "python",
      nodes: nodesOnly,
      edges: [
        { source: "a", target: "b", kind: "call", metadata: { line: 9 } },
      ],
    });

    expect(result.removedEdgeCount).toBe(1);
    expect(result.addedEdges.length).toBe(1);
    const remaining = live.edges("a", "b");
    expect(remaining.length).toBe(1);
    expect(live.getEdgeAttribute(remaining[0] as string, "line")).toBe(9);
  });

  it("buckets a line-less edge separately from a line-bearing one between the same nodes", () => {
    const graph: Graph = {
      version: 1,
      language: "python",
      nodes: nodesOnly,
      edges: [
        { source: "a", target: "b", kind: "call" },
        { source: "a", target: "b", kind: "call", metadata: { line: 5 } },
      ],
    };
    const live = buildGraphology(graph);

    const result = applyGraphDiff(live, graph);

    expect(isEmptyDiff(result)).toBe(true);
    expect(live.edges("a", "b").length).toBe(2);
  });
});

describe("applyGraphDiff — duplicate and missing-endpoint guards", () => {
  it("skips a duplicate node id in the incoming graph (first wins), warning once", () => {
    const warnSpy = vi.spyOn(console, "warn").mockImplementation(() => {});
    const live = new MultiDirectedGraph() as GrackleMultiGraph;

    const incoming: Graph = {
      version: 1,
      language: "python",
      nodes: [
        { id: "a", kind: "function", name: "first", path: "a.py" },
        { id: "a", kind: "function", name: "second", path: "a.py" },
      ],
      edges: [],
    };
    applyGraphDiff(live, incoming);

    expect(live.getNodeAttribute("a", "name")).toBe("first");
    expect(warnSpy).toHaveBeenCalledTimes(1);
    expect(warnSpy.mock.calls[0]?.[0]).toContain("a");
    warnSpy.mockRestore();
  });

  it("skips an edge whose endpoint is absent from the incoming graph", () => {
    const live = new MultiDirectedGraph() as GrackleMultiGraph;
    const incoming: Graph = {
      version: 1,
      language: "python",
      nodes: [{ id: "a", kind: "function", name: "a", path: "a.py" }],
      edges: [{ source: "a", target: "ghost", kind: "call" }],
    };
    const result = applyGraphDiff(live, incoming);

    expect(result.addedEdges).toEqual([]);
    expect(live.size).toBe(0);
  });
});

describe("applyGraphDiff — removal contract", () => {
  it("reports removed nodes without dropping them, and drops their edges immediately", () => {
    const live = buildGraphology(BASE_GRAPH);
    expect(live.hasNode("b.py:B")).toBe(true);

    const incoming: Graph = {
      version: 1,
      language: "python",
      nodes: [BASE_GRAPH.nodes[0] as Graph["nodes"][number]],
      edges: [],
    };
    const result = applyGraphDiff(live, incoming);

    expect(result.removedNodes).toEqual(["b.py:B"]);
    expect(live.hasNode("b.py:B")).toBe(true); // still present — caller fades, then drops
    expect(result.removedEdgeCount).toBe(1);
    expect(live.edges("a.py:A", "b.py:B").length).toBe(0);
  });
});

describe("applyGraphDiff — no-op detection", () => {
  it("returns an empty diff and fires zero graphology events for an identical re-push", () => {
    const live = buildGraphology(BASE_GRAPH);
    const spies = attachSpies(live);

    // Deep-clone to mimic a real re-push: same content, fresh object identity.
    const incoming: Graph = JSON.parse(JSON.stringify(BASE_GRAPH));
    const result = applyGraphDiff(live, incoming);

    expect(isEmptyDiff(result)).toBe(true);
    expect(result.attrChangedNodes).toEqual([]);
    for (const spy of Object.values(spies)) {
      expect(spy).not.toHaveBeenCalled();
    }
  });

  it("is idempotent: applying the same incoming graph twice yields an empty second result", () => {
    const live = buildGraphology(BASE_GRAPH);
    const incoming: Graph = {
      ...BASE_GRAPH,
      nodes: [
        ...BASE_GRAPH.nodes,
        { id: "c.py:C", kind: "function", name: "C", path: "c.py" },
      ],
      edges: [
        ...BASE_GRAPH.edges,
        { source: "a.py:A", target: "c.py:C", kind: "call" },
      ],
    };

    const first = applyGraphDiff(live, incoming);
    expect(isEmptyDiff(first)).toBe(false);

    const second = applyGraphDiff(live, incoming);
    expect(isEmptyDiff(second)).toBe(true);
    expect(second.attrChangedNodes).toEqual([]);
  });
});
