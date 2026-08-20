import type { TraceEvent } from "@grackle/shared-types";
import { describe, expect, it } from "vitest";
import { computeRunsFromCandidates } from "./epochSeries";
import {
  type LayerStatsPoint,
  maxima,
  scanLayerStatsCandidates,
  statsAtPlayhead,
} from "./layerStats";

function retEvent(
  node_id: string,
  ret: string,
  ret_truncated?: boolean
): TraceEvent {
  const values: NonNullable<TraceEvent["values"]> =
    ret_truncated === undefined ? { ret } : { ret, ret_truncated };
  return {
    event: "return",
    node_id,
    ts_ns: 0,
    thread_id: 1,
    frame_depth: 0,
    values,
  };
}

const NODE = "grackle_nn/metrics.py:record_layer_stats";

const CHUNK = 2;

/**
 * Runs the events through BOTH a single pass and a chunked incremental scan,
 * asserts the two agree, and returns the single-pass result. Every test below
 * therefore exercises the composition the panels actually ship — the
 * incremental scanner accumulated across appends — rather than a one-shot
 * convenience wrapper no production code calls.
 */
function statsOf(
  events: readonly TraceEvent[],
  linearCount: number
): LayerStatsPoint[] {
  const single = scanLayerStatsCandidates(events, linearCount, 0, undefined);
  let chunked: LayerStatsPoint[] = [];
  let carry: number | undefined;
  for (let start = 0; start < events.length; start += CHUNK) {
    const end = Math.min(events.length, start + CHUNK);
    const step = scanLayerStatsCandidates(
      events.slice(0, end),
      linearCount,
      start,
      carry
    );
    chunked = chunked.concat(step.items);
    carry = step.carry;
  }
  expect(chunked).toEqual(single.items);
  expect(carry ?? 0).toBe(single.carry);
  return computeRunsFromCandidates(single.items).points;
}

/** The non-finite drop count the panel surfaces, threaded through `carry`. */
function droppedOf(events: readonly TraceEvent[], linearCount: number): number {
  return scanLayerStatsCandidates(events, linearCount, 0, undefined).carry;
}

describe("scanLayerStatsCandidates", () => {
  it("parses a real 3-linear-layer 7-tuple", () => {
    const events = [
      retEvent(NODE, "(0, 0.123, 0.0456, 0.789, 0.0123, 1.11, 0.222)"),
    ];
    const series = statsOf(events, 3);
    expect(series).toEqual([
      {
        epoch: 0,
        eventIndex: 0,
        perLinear: [
          { wRms: 0.123, dwRms: 0.0456 },
          { wRms: 0.789, dwRms: 0.0123 },
          { wRms: 1.11, dwRms: 0.222 },
        ],
      },
    ]);
  });

  it("parses scientific notation", () => {
    const events = [retEvent(NODE, "(2, 1e-05, 2.5e+02)")];
    const series = statsOf(events, 1);
    expect(series[0]?.perLinear).toEqual([{ wRms: 0.00001, dwRms: 250 }]);
  });

  it("drops a point containing inf/nan", () => {
    const events = [retEvent(NODE, "(0, inf, nan)")];
    expect(statsOf(events, 1)).toEqual([]);
  });

  it("ignores a tuple whose arity doesn't match linearCount (wrong net)", () => {
    // A 5-element (2-linear) tuple parsed against linearCount=3 (expects 7).
    const events = [retEvent(NODE, "(0, 0.1, 0.2, 0.3, 0.4)")];
    expect(statsOf(events, 3)).toEqual([]);
  });

  it("rejects a node_id where metrics.py:record_layer_stats is not exact-or-slash-preceded", () => {
    const events = [
      retEvent("pkg/mymetrics.py:record_layer_stats", "(0, 0.1, 0.2)"),
    ];
    expect(statsOf(events, 1)).toEqual([]);
  });

  it("skips an event with ret_truncated: true", () => {
    const events = [retEvent(NODE, "(0, 0.1, 0.2)", true)];
    expect(statsOf(events, 1)).toEqual([]);
  });

  it("keeps only the last run on a restart (non-increasing epoch)", () => {
    const events = [
      retEvent(NODE, "(0, 0.1, 0.2)"), // run 1
      retEvent(NODE, "(1, 0.15, 0.15)"), // run 1
      retEvent(NODE, "(0, 0.05, 0.4)"), // run 2 (restart)
    ];
    const series = statsOf(events, 1);
    expect(series.map((p) => p.epoch)).toEqual([0]);
    expect(series[0]?.perLinear).toEqual([{ wRms: 0.05, dwRms: 0.4 }]);
  });

  it("reports absolute event indices when interleaved with unrelated events", () => {
    const events: TraceEvent[] = [
      {
        event: "call",
        node_id: "grackle_nn/train.py:fit",
        ts_ns: 0,
        thread_id: 1,
        frame_depth: 0,
      },
      retEvent(NODE, "(0, 0.1, 0.2)"),
      {
        event: "call",
        node_id: "grackle_nn/train.py:fit",
        ts_ns: 0,
        thread_id: 1,
        frame_depth: 0,
      },
      retEvent(NODE, "(1, 0.15, 0.15)"),
    ];
    const series = statsOf(events, 1);
    expect(series.map((p) => p.eventIndex)).toEqual([1, 3]);
  });
});

describe("scanLayerStatsCandidates — non-finite drop count", () => {
  it("counts a dropped point instead of discarding it silently", () => {
    // A diverged run: without the count the panel would freeze its bundle
    // widths at the last finite epoch with nothing to say about why.
    const events = [
      retEvent(NODE, "(0, 0.1, 0.2)"),
      retEvent(NODE, "(1, nan, 0.2)"),
      retEvent(NODE, "(2, 0.3, inf)"),
    ];
    expect(droppedOf(events, 1)).toBe(2);
    expect(statsOf(events, 1).map((p) => p.epoch)).toEqual([0]);
  });

  it("is zero when every point is finite", () => {
    expect(droppedOf([retEvent(NODE, "(0, 0.1, 0.2)")], 1)).toBe(0);
  });

  it("does not count a shape mismatch as a drop", () => {
    // Wrong arity is a different net, not a diverged one.
    expect(droppedOf([retEvent(NODE, "(0, 0.1, 0.2, 0.3, 0.4)")], 1)).toBe(0);
  });

  it("accumulates across an incremental resume", () => {
    const events = [
      retEvent(NODE, "(0, nan, 0.2)"),
      retEvent(NODE, "(1, 0.1, inf)"),
    ];
    const first = scanLayerStatsCandidates(events.slice(0, 1), 1, 0, undefined);
    const second = scanLayerStatsCandidates(events, 1, 1, first.carry);
    expect(second.carry).toBe(droppedOf(events, 1));
    expect(second.carry).toBe(2);
  });
});

describe("statsAtPlayhead", () => {
  const series: LayerStatsPoint[] = [
    { epoch: 0, eventIndex: 30, perLinear: [{ wRms: 0.1, dwRms: 0.5 }] },
    { epoch: 1, eventIndex: 60, perLinear: [{ wRms: 0.2, dwRms: 0.3 }] },
    { epoch: 2, eventIndex: 90, perLinear: [{ wRms: 0.3, dwRms: 0.1 }] },
  ];

  it("returns null before the first epoch's stats have fired", () => {
    expect(statsAtPlayhead(series, 0)).toBeNull();
    expect(statsAtPlayhead(series, 29)).toBeNull();
  });

  it("returns the last point at or before the playhead", () => {
    // playheadLookup.ts's shared INCLUSIVE convention: the event AT the
    // playhead has happened, matching what ValueInspectorPanel displays and
    // where a LossCurvePanel click seeks to.
    expect(statsAtPlayhead(series, 30)?.epoch).toBe(0); // boundary included
    expect(statsAtPlayhead(series, 31)?.epoch).toBe(0);
    expect(statsAtPlayhead(series, 59)?.epoch).toBe(0);
    expect(statsAtPlayhead(series, 60)?.epoch).toBe(1); // boundary included
    expect(statsAtPlayhead(series, 1000)?.epoch).toBe(2);
  });

  it("returns null for an empty series", () => {
    expect(statsAtPlayhead([], 100)).toBeNull();
  });
});

describe("maxima", () => {
  it("computes per-linear-index maxima across the series", () => {
    const series: LayerStatsPoint[] = [
      {
        epoch: 0,
        eventIndex: 0,
        perLinear: [
          { wRms: 0.1, dwRms: 0.9 },
          { wRms: 5.0, dwRms: 0.05 },
        ],
      },
      {
        epoch: 1,
        eventIndex: 1,
        perLinear: [
          { wRms: 0.4, dwRms: 0.2 },
          { wRms: 3.0, dwRms: 0.1 },
        ],
      },
    ];
    expect(maxima(series)).toEqual({
      wRms: [0.4, 5.0],
      dwRms: [0.9, 0.1],
    });
  });

  it("returns empty arrays for an empty series", () => {
    expect(maxima([])).toEqual({ wRms: [], dwRms: [] });
  });
});

describe("scanLayerStatsCandidates — float grammar", () => {
  it("parses negative values", () => {
    // wRms/dwRms are magnitudes today, but the grammar is shared with
    // record_epoch's loss, so a sign must not silently drop the whole point.
    const series = statsOf([retEvent(NODE, "(0, -0.5, 0.25)")], 1);
    expect(series[0]?.perLinear).toEqual([{ wRms: -0.5, dwRms: 0.25 }]);
  });

  it("parses an uppercase exponent and a bare-dot float", () => {
    const series = statsOf([retEvent(NODE, "(0, 1.5E-3, .25)")], 1);
    expect(series[0]?.perLinear).toEqual([{ wRms: 0.0015, dwRms: 0.25 }]);
  });

  it("drops -inf as non-finite", () => {
    const events = [retEvent(NODE, "(0, -inf, 0.2)")];
    expect(statsOf(events, 1)).toEqual([]);
    expect(droppedOf(events, 1)).toBe(1);
  });

  it("ignores an explicit + sign (not repr's grammar) without counting a drop", () => {
    const events = [retEvent(NODE, "(0, +1.5, 0.2)")];
    expect(statsOf(events, 1)).toEqual([]);
    expect(droppedOf(events, 1)).toBe(0);
  });

  it("requires exactly 1 + 2L elements", () => {
    const five = "(0, 0.1, 0.2, 0.3, 0.4)";
    expect(statsOf([retEvent(NODE, five)], 2)).toHaveLength(1);
    expect(statsOf([retEvent(NODE, five)], 1)).toEqual([]);
    expect(statsOf([retEvent(NODE, five)], 3)).toEqual([]);
  });

  it("rejects a negative epoch (the epoch group is unsigned)", () => {
    expect(statsOf([retEvent(NODE, "(-1, 0.1, 0.2)")], 1)).toEqual([]);
  });
});

describe("scanLayerStatsCandidates — series ordering and maxima", () => {
  it("reports candidates in ascending eventIndex order", () => {
    const events = Array.from({ length: 5 }, (_, i) =>
      retEvent(NODE, `(${i}, 0.${i + 1}, 0.0${i + 1})`)
    );
    const indices = statsOf(events, 1).map((p) => p.eventIndex);
    expect(indices).toEqual([0, 1, 2, 3, 4]);
  });

  it("counts drops across a restart, while the series keeps only the last run", () => {
    // Documented asymmetry: the panel's "N epochs dropped" warning is a
    // whole-trace count, the bundles it annotates are last-run only.
    const events = [
      retEvent(NODE, "(0, nan, 0.2)"), // run 1, dropped
      retEvent(NODE, "(1, 0.1, 0.2)"), // run 1
      retEvent(NODE, "(0, 0.3, 0.4)"), // run 2 (restart)
      retEvent(NODE, "(1, inf, 0.4)"), // run 2, dropped
    ];
    expect(droppedOf(events, 1)).toBe(2);
    expect(statsOf(events, 1).map((p) => p.epoch)).toEqual([0]);
  });

  it("keeps per-index maxima independent when a later point has fewer layers", () => {
    const series: LayerStatsPoint[] = [
      {
        epoch: 0,
        eventIndex: 0,
        perLinear: [
          { wRms: 1, dwRms: 2 },
          { wRms: 3, dwRms: 4 },
        ],
      },
      { epoch: 1, eventIndex: 1, perLinear: [{ wRms: 9, dwRms: 0.5 }] },
    ];
    expect(maxima(series)).toEqual({ wRms: [9, 3], dwRms: [2, 4] });
  });

  it("reports zero maxima for an all-zero layer (the panel's divide guard)", () => {
    // paintNetwork normalizes wRms/max; a zero max must stay zero here so the
    // `max > 0` guard there is the only thing standing between it and NaN.
    const series: LayerStatsPoint[] = [
      { epoch: 0, eventIndex: 0, perLinear: [{ wRms: 0, dwRms: 0 }] },
    ];
    expect(maxima(series)).toEqual({ wRms: [0], dwRms: [0] });
  });
});
