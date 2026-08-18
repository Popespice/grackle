import { describe, expect, it } from "vitest";
import { countEvents } from "./eventCounts";

describe("countEvents", () => {
  it("returns an empty object for no events", () => {
    expect(countEvents([])).toEqual({});
  });

  it("counts one hit per event, unweighted", () => {
    const counts = countEvents([
      { node_id: "a" },
      { node_id: "b" },
      { node_id: "a" },
      { node_id: "a" },
    ]);
    expect(counts).toEqual({ a: 3, b: 1 });
  });

  it("does not include node_ids with zero events", () => {
    const counts = countEvents([{ node_id: "a" }]);
    expect(counts.b).toBeUndefined();
  });
});
