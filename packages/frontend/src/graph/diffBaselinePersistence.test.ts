import type { Graph } from "@grackle/shared-types";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { graphCacheKey } from "./analysis/cacheKey";
import { persistBaseline, restoreBaseline } from "./diffBaselinePersistence";

const GRAPH_A: Graph = {
  version: 1,
  language: "python",
  nodes: [
    { id: "a", kind: "file", name: "a", path: "a" },
    { id: "b", kind: "class", name: "B", path: "a" },
  ],
  edges: [{ source: "a", target: "b", kind: "import" }],
};

const GRAPH_B: Graph = {
  version: 1,
  language: "python",
  nodes: [{ id: "c", kind: "file", name: "c", path: "c" }],
  edges: [],
};

beforeEach(() => {
  sessionStorage.clear();
});

afterEach(() => {
  sessionStorage.clear();
});

describe("diffBaselinePersistence", () => {
  it("persist then restore round-trips for the same graph", async () => {
    const baseline = { a: 3, b: 1 };
    await persistBaseline(GRAPH_A, baseline);
    const restored = await restoreBaseline(GRAPH_A);
    expect(restored).toEqual(baseline);
  });

  it("restore returns null for a different graph (hash miss)", async () => {
    await persistBaseline(GRAPH_A, { a: 3 });
    const restored = await restoreBaseline(GRAPH_B);
    expect(restored).toBeNull();
  });

  it("persist(null) removes the key", async () => {
    await persistBaseline(GRAPH_A, { a: 3 });
    expect(await restoreBaseline(GRAPH_A)).not.toBeNull();
    await persistBaseline(GRAPH_A, null);
    expect(await restoreBaseline(GRAPH_A)).toBeNull();
  });

  it("restore returns null on malformed JSON", async () => {
    const key = `grackle:diff-baseline:${await graphCacheKey(GRAPH_A)}`;
    sessionStorage.setItem(key, "{not valid json");
    expect(await restoreBaseline(GRAPH_A)).toBeNull();
  });

  it("persist is a no-op when sessionStorage throws", async () => {
    const original = sessionStorage.setItem.bind(sessionStorage);
    sessionStorage.setItem = () => {
      throw new DOMException("quota exceeded");
    };
    try {
      await expect(persistBaseline(GRAPH_A, { a: 1 })).resolves.toBeUndefined();
    } finally {
      sessionStorage.setItem = original;
    }
  });

  it("restore returns null when sessionStorage throws", async () => {
    const original = sessionStorage.getItem.bind(sessionStorage);
    sessionStorage.getItem = () => {
      throw new DOMException("blocked");
    };
    try {
      expect(await restoreBaseline(GRAPH_A)).toBeNull();
    } finally {
      sessionStorage.getItem = original;
    }
  });

  it("uses the grackle:diff-baseline:<hash> key format", async () => {
    const baseline = { a: 1 };
    await persistBaseline(GRAPH_A, baseline);
    const hash = await graphCacheKey(GRAPH_A);
    expect(sessionStorage.getItem(`grackle:diff-baseline:${hash}`)).toBe(
      JSON.stringify(baseline)
    );
  });

  it.each([
    ["an array", "[1,2,3]"],
    ["a number", "42"],
    ["a string", '"oops"'],
    ["null", "null"],
    ["an object with non-number values", '{"a":"x"}'],
    ["an object with a NaN-producing value", '{"a":null}'],
  ])("restore rejects valid-but-wrong-shape JSON (%s)", async (_label, json) => {
    const key = `grackle:diff-baseline:${await graphCacheKey(GRAPH_A)}`;
    sessionStorage.setItem(key, json);
    expect(await restoreBaseline(GRAPH_A)).toBeNull();
  });

  it("restore accepts an empty object as a valid (empty) baseline", async () => {
    const key = `grackle:diff-baseline:${await graphCacheKey(GRAPH_A)}`;
    sessionStorage.setItem(key, "{}");
    expect(await restoreBaseline(GRAPH_A)).toEqual({});
  });
});
