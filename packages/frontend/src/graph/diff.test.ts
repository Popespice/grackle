import type { Graph } from "@grackle/shared-types";
import { describe, expect, it } from "vitest";
import {
  diffCounts,
  diffToOverlay,
  diffTraceVsStatic,
  diffTraceVsTrace,
  hasRegression,
} from "./diff";
import type { RuntimeCoverage } from "./runtimeCoverage";

// ---------------------------------------------------------------------------
// Helpers
// ---------------------------------------------------------------------------

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

function makeCoverage(
  touched: string[],
  cold: string[],
  hot: string[] = []
): RuntimeCoverage {
  return {
    touched: new Set(touched),
    cold: new Set(cold),
    hot: new Set(hot),
    touchedCount: touched.length,
    coldCount: cold.length,
    hotCount: hot.length,
  };
}

// ---------------------------------------------------------------------------
// diffTraceVsStatic
// ---------------------------------------------------------------------------

describe("diffTraceVsStatic", () => {
  it("classifies all nodes as cold when coverage is empty", () => {
    const g = makeGraph(["a", "b", "c"]);
    const cov = makeCoverage([], ["a", "b", "c"]);
    const entries = diffTraceVsStatic(g, cov);
    expect(entries).toHaveLength(3);
    expect(entries.every((e) => e.status === "cold")).toBe(true);
  });

  it("classifies touched nodes correctly", () => {
    const g = makeGraph(["a", "b", "c"]);
    const cov = makeCoverage(["a", "b"], ["c"]);
    const byId = Object.fromEntries(
      diffTraceVsStatic(g, cov).map((e) => [e.nodeId, e])
    );
    expect(byId.a?.status).toBe("touched");
    expect(byId.b?.status).toBe("touched");
    expect(byId.c?.status).toBe("cold");
  });

  it("cold nodes sort before touched", () => {
    const g = makeGraph(["a", "b", "c"]);
    const cov = makeCoverage(["a"], ["b", "c"]);
    const statuses = diffTraceVsStatic(g, cov).map((e) => e.status);
    const firstTouched = statuses.indexOf("touched");
    const lastCold = statuses.lastIndexOf("cold");
    expect(lastCold).toBeLessThan(firstTouched);
  });

  it("countB and delta are always 0 for vs-static", () => {
    const g = makeGraph(["a"]);
    const cov = makeCoverage(["a"], []);
    const entry = diffTraceVsStatic(g, cov)[0];
    expect(entry?.countB).toBe(0);
    expect(entry?.delta).toBe(0);
  });

  it("returns empty array for empty graph", () => {
    const g = makeGraph([]);
    const cov = makeCoverage([], []);
    expect(diffTraceVsStatic(g, cov)).toEqual([]);
  });
});

// ---------------------------------------------------------------------------
// diffTraceVsTrace
// ---------------------------------------------------------------------------

describe("diffTraceVsTrace", () => {
  it("classifies hotter when B has more hits", () => {
    const entries = diffTraceVsTrace({ x: 1 }, { x: 5 });
    expect(entries[0]?.status).toBe("hotter");
    expect(entries[0]?.delta).toBe(4);
  });

  it("classifies colder when B has fewer hits", () => {
    const entries = diffTraceVsTrace({ x: 5 }, { x: 2 });
    expect(entries[0]?.status).toBe("colder");
    expect(entries[0]?.delta).toBe(-3);
  });

  it("classifies new when only in B", () => {
    const entries = diffTraceVsTrace({}, { y: 3 });
    expect(entries[0]?.status).toBe("new");
    expect(entries[0]?.countA).toBe(0);
    expect(entries[0]?.countB).toBe(3);
  });

  it("classifies gone when only in A", () => {
    const entries = diffTraceVsTrace({ z: 2 }, {});
    expect(entries[0]?.status).toBe("gone");
    expect(entries[0]?.countB).toBe(0);
  });

  it("classifies same when equal counts", () => {
    const entries = diffTraceVsTrace({ x: 3 }, { x: 3 });
    expect(entries[0]?.status).toBe("same");
    expect(entries[0]?.delta).toBe(0);
  });

  it("classifies same when both zero (from graphNodeIds)", () => {
    const entries = diffTraceVsTrace({}, {}, ["phantom"]);
    const entry = entries.find((e) => e.nodeId === "phantom");
    expect(entry?.status).toBe("same");
  });

  it("includes graphNodeIds not in either session", () => {
    const entries = diffTraceVsTrace({ x: 1 }, { x: 1 }, ["x", "extra"]);
    const ids = new Set(entries.map((e) => e.nodeId));
    expect(ids.has("extra")).toBe(true);
  });

  it("severity sort: hotter before new before gone", () => {
    const entries = diffTraceVsTrace(
      { hot: 1, gone: 2 },
      { hot: 5, newNode: 1 }
    );
    const statuses = entries.map((e) => e.status);
    expect(statuses.indexOf("hotter")).toBeLessThan(statuses.indexOf("new"));
    expect(statuses.indexOf("new")).toBeLessThan(statuses.indexOf("gone"));
  });

  it("returns empty array when both sessions empty and no graphNodeIds", () => {
    expect(diffTraceVsTrace({}, {})).toEqual([]);
  });

  it("stable sort by nodeId within same status", () => {
    // Both a and b have more hits in B — both are "hotter".
    // Alphabetically a < b, so a should appear first.
    const entries = diffTraceVsTrace({ a: 1, b: 1 }, { a: 3, b: 5 });
    const hotterEntries = entries.filter((e) => e.status === "hotter");
    expect(hotterEntries[0]?.nodeId).toBe("a");
    expect(hotterEntries[1]?.nodeId).toBe("b");
  });
});

// ---------------------------------------------------------------------------
// hasRegression
// ---------------------------------------------------------------------------

describe("hasRegression", () => {
  it("returns true when hotter entry present", () => {
    const entries = diffTraceVsTrace({ x: 1 }, { x: 5 });
    expect(hasRegression(entries)).toBe(true);
  });

  it("returns false when no hotter entry", () => {
    const entries = diffTraceVsTrace({ x: 5 }, { x: 2 });
    expect(hasRegression(entries)).toBe(false);
  });

  it("returns false on empty", () => {
    expect(hasRegression([])).toBe(false);
  });
});

// ---------------------------------------------------------------------------
// diffToOverlay
// ---------------------------------------------------------------------------

describe("diffToOverlay", () => {
  it("builds a Map from nodeId to DiffStatus", () => {
    const entries = diffTraceVsTrace({ a: 1 }, { a: 3, b: 1 });
    const overlay = diffToOverlay(entries);
    expect(overlay.get("a")).toBe("hotter");
    expect(overlay.get("b")).toBe("new");
  });

  it("returns empty map for empty entries", () => {
    expect(diffToOverlay([])).toEqual(new Map());
  });
});

// ---------------------------------------------------------------------------
// diffCounts
// ---------------------------------------------------------------------------

describe("diffCounts", () => {
  it("counts entries per status", () => {
    const entries = diffTraceVsTrace({ a: 1, b: 3 }, { a: 5, c: 1 });
    const counts = diffCounts(entries);
    expect(counts.hotter).toBe(1); // a
    expect(counts.gone).toBe(1); // b
    expect(counts.new).toBe(1); // c
  });

  it("all zeros on empty", () => {
    const counts = diffCounts([]);
    expect(Object.values(counts).every((v) => v === 0)).toBe(true);
  });
});
