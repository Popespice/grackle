import type { Graph, TraceEvent } from "@grackle/shared-types";
import { describe, expect, it } from "vitest";
import { runtimeCoverage } from "./runtimeCoverage";

function makeGraph(nodeIds: string[]): Graph {
  return {
    version: 1,
    language: "python",
    nodes: nodeIds.map((id) => ({
      id,
      kind: "function",
      name: id,
      path: `${id}.py`,
    })),
    edges: [],
  };
}

function ev(node_id: string, count = 1): TraceEvent[] {
  return Array.from({ length: count }, () => ({
    event: "call",
    node_id,
    ts_ns: 0,
    thread_id: 1,
    frame_depth: 0,
  }));
}

describe("runtimeCoverage", () => {
  it("all nodes cold when no events", () => {
    const g = makeGraph(["a", "b", "c"]);
    const r = runtimeCoverage(g, []);
    expect(r.touchedCount).toBe(0);
    expect(r.coldCount).toBe(3);
    expect(r.hotCount).toBe(0);
    expect(r.cold).toContain("a");
  });

  it("touched contains nodes with events, cold the rest", () => {
    const g = makeGraph(["a", "b", "c"]);
    const r = runtimeCoverage(g, [...ev("a"), ...ev("b")]);
    expect(r.touched).toContain("a");
    expect(r.touched).toContain("b");
    expect(r.cold).toContain("c");
    expect(r.cold).not.toContain("a");
  });

  it("excludes events for nodes not in the graph (stdlib frames)", () => {
    const g = makeGraph(["a"]);
    const stdlibEv: TraceEvent = {
      event: "call",
      node_id: "os.path.join",
      ts_ns: 0,
      thread_id: 1,
      frame_depth: 0,
    };
    const r = runtimeCoverage(g, [...ev("a"), stdlibEv]);
    expect(r.touched).toContain("a");
    expect(r.touched).not.toContain("os.path.join");
    expect(r.touchedCount).toBe(1);
  });

  it("hot set contains top-quartile nodes", () => {
    const g = makeGraph(["a", "b", "c", "d"]);
    // a=10 (hot), b=1, c=1, d=1 — top quartile threshold at 75th percentile
    const events = [
      ...ev("a", 10),
      ...ev("b", 1),
      ...ev("c", 1),
      ...ev("d", 1),
    ];
    const r = runtimeCoverage(g, events);
    expect(r.hot).toContain("a");
    // b/c/d are at the 25th percentile — may or may not be hot depending on threshold
    // but a is definitely hot
    expect(r.hotCount).toBeGreaterThanOrEqual(1);
  });

  it("hot set respects HOT_FLOOR — single-call nodes are not hot", () => {
    // All nodes have count=1 which is below the floor of 2
    const g = makeGraph(["a", "b"]);
    const r = runtimeCoverage(g, [...ev("a", 1), ...ev("b", 1)]);
    expect(r.hotCount).toBe(0);
  });

  it("returns zero counts on empty graph", () => {
    const r = runtimeCoverage(makeGraph([]), [...ev("a")]);
    expect(r.touchedCount).toBe(0);
    expect(r.coldCount).toBe(0);
    expect(r.hotCount).toBe(0);
  });
});
