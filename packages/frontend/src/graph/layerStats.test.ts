import type { TraceEvent } from "@grackle/shared-types";
import { describe, expect, it } from "vitest";
import {
  extractLayerStats,
  type LayerStatsPoint,
  maxima,
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

describe("extractLayerStats", () => {
  it("parses a real 3-linear-layer 7-tuple", () => {
    const events = [
      retEvent(NODE, "(0, 0.123, 0.0456, 0.789, 0.0123, 1.11, 0.222)"),
    ];
    const series = extractLayerStats(events, 3);
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
    const series = extractLayerStats(events, 1);
    expect(series[0]?.perLinear).toEqual([{ wRms: 0.00001, dwRms: 250 }]);
  });

  it("drops a point containing inf/nan", () => {
    const events = [retEvent(NODE, "(0, inf, nan)")];
    expect(extractLayerStats(events, 1)).toEqual([]);
  });

  it("ignores a tuple whose arity doesn't match linearCount (wrong net)", () => {
    // A 5-element (2-linear) tuple parsed against linearCount=3 (expects 7).
    const events = [retEvent(NODE, "(0, 0.1, 0.2, 0.3, 0.4)")];
    expect(extractLayerStats(events, 3)).toEqual([]);
  });

  it("rejects a node_id where metrics.py:record_layer_stats is not exact-or-slash-preceded", () => {
    const events = [
      retEvent("pkg/mymetrics.py:record_layer_stats", "(0, 0.1, 0.2)"),
    ];
    expect(extractLayerStats(events, 1)).toEqual([]);
  });

  it("skips an event with ret_truncated: true", () => {
    const events = [retEvent(NODE, "(0, 0.1, 0.2)", true)];
    expect(extractLayerStats(events, 1)).toEqual([]);
  });

  it("keeps only the last run on a restart (non-increasing epoch)", () => {
    const events = [
      retEvent(NODE, "(0, 0.1, 0.2)"), // run 1
      retEvent(NODE, "(1, 0.15, 0.15)"), // run 1
      retEvent(NODE, "(0, 0.05, 0.4)"), // run 2 (restart)
    ];
    const series = extractLayerStats(events, 1);
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
    const series = extractLayerStats(events, 1);
    expect(series.map((p) => p.eventIndex)).toEqual([1, 3]);
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
