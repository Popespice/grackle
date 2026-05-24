import type { TraceEvent } from "@grackle/shared-types";
import { describe, expect, it } from "vitest";
import { computeHeat } from "./heatmap";

function ev(node_id: string, event = "call"): TraceEvent {
  return { event, node_id, ts_ns: 0, thread_id: 1, frame_depth: 0 };
}

const EVENTS: TraceEvent[] = [
  ev("a", "call"),
  ev("b", "call"),
  ev("a", "call"),
  ev("c", "return"),
  ev("a", "call"),
];

describe("computeHeat", () => {
  it("cumulative: counts all events up to playhead", () => {
    const { heat, maxHeat } = computeHeat(
      EVENTS,
      5,
      new Set(),
      "cumulative",
      200
    );
    expect(heat.get("a")).toBe(3);
    expect(heat.get("b")).toBe(1);
    expect(heat.get("c")).toBe(1);
    expect(maxHeat).toBe(3);
  });

  it("cumulative: playhead 0 returns empty heat", () => {
    const { heat, maxHeat } = computeHeat(
      EVENTS,
      0,
      new Set(),
      "cumulative",
      200
    );
    expect(heat.size).toBe(0);
    expect(maxHeat).toBe(0);
  });

  it("cumulative: partial playhead counts only visible events", () => {
    const { heat } = computeHeat(EVENTS, 2, new Set(), "cumulative", 200);
    expect(heat.get("a")).toBe(1);
    expect(heat.get("b")).toBe(1);
    expect(heat.has("c")).toBe(false);
  });

  it("sliding: uses only the look-back window", () => {
    // window=2: only events[3..4] → c, a
    const { heat } = computeHeat(EVENTS, 5, new Set(), "sliding", 2);
    expect(heat.get("a")).toBe(1);
    expect(heat.get("c")).toBe(1);
    expect(heat.has("b")).toBe(false);
  });

  it("sliding: window larger than event count behaves like cumulative", () => {
    const cumul = computeHeat(EVENTS, 5, new Set(), "cumulative", 200);
    const slide = computeHeat(EVENTS, 5, new Set(), "sliding", 1000);
    expect(slide.heat).toEqual(cumul.heat);
    expect(slide.maxHeat).toEqual(cumul.maxHeat);
  });

  it("filter: skips events not in the filter set", () => {
    const { heat } = computeHeat(
      EVENTS,
      5,
      new Set(["call"]),
      "cumulative",
      200
    );
    expect(heat.has("c")).toBe(false); // "return" filtered out
    expect(heat.get("a")).toBe(3);
  });

  it("filter: empty set counts all event types", () => {
    const { heat } = computeHeat(EVENTS, 5, new Set(), "cumulative", 200);
    expect(heat.has("c")).toBe(true);
  });

  it("playhead clamped above event length returns full heat", () => {
    const overshot = computeHeat(EVENTS, 999, new Set(), "cumulative", 200);
    const full = computeHeat(
      EVENTS,
      EVENTS.length,
      new Set(),
      "cumulative",
      200
    );
    expect(overshot.heat).toEqual(full.heat);
  });
});
