import type { TraceEvent } from "@grackle/shared-types";
import { describe, expect, it } from "vitest";
import {
  computeRunsFromCandidates,
  extractEpochSeries,
  scanEpochCandidates,
} from "./epochSeries";

/** Build a `record_epoch`-shaped (or arbitrary) `"return"` event. `ret` is the
 *  raw `values.ret` repr string; `ret_truncated` defaults to absent (not
 *  `false`) to match how the real tracer omits the field when it never
 *  truncates — exactOptionalPropertyTypes forbids assigning `undefined`
 *  explicitly, so this only sets the key when a caller opts in. */
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

/** Build an unrelated (non-record_epoch) event — a `call`, or a `return` for
 *  some other node — to interleave with real candidates. */
function otherEvent(event: string, node_id: string): TraceEvent {
  return { event, node_id, ts_ns: 0, thread_id: 1, frame_depth: 0 };
}

describe("extractEpochSeries — selection + parsing", () => {
  it("parses the real full-precision repr from run-a.jsonl (golden case)", () => {
    const events = [
      retEvent(
        "grackle_nn/metrics.py:record_epoch",
        "(0, 0.7107649687606569, 0.6041666666666666)"
      ),
    ];
    const series = extractEpochSeries(events);
    expect(series.dropped).toBe(0);
    expect(series.runs).toBe(1);
    expect(series.points).toEqual([
      {
        epoch: 0,
        loss: 0.7107649687606569,
        accuracy: 0.6041666666666666,
        eventIndex: 0,
      },
    ]);
  });

  it("parses scientific notation", () => {
    const events = [
      retEvent("grackle_nn/metrics.py:record_epoch", "(5, 1e-05, 0.99)"),
    ];
    const series = extractEpochSeries(events);
    expect(series.points).toHaveLength(1);
    expect(series.points[0]?.loss).toBe(0.00001);
    expect(series.points[0]?.accuracy).toBe(0.99);
  });

  it("drops a point whose loss/accuracy is inf/nan and counts it in dropped", () => {
    const events = [
      retEvent("grackle_nn/metrics.py:record_epoch", "(0, inf, nan)"),
    ];
    const series = extractEpochSeries(events);
    expect(series.points).toEqual([]);
    expect(series.dropped).toBe(1);
  });

  it("silently ignores a 2-tuple ret (wrong shape) — not dropped, not a point", () => {
    const events = [retEvent("grackle_nn/metrics.py:record_epoch", "(0, 0.5)")];
    const series = extractEpochSeries(events);
    expect(series.points).toEqual([]);
    expect(series.dropped).toBe(0);
  });

  it("silently ignores an ndarray repr — not dropped, not a point", () => {
    const events = [
      retEvent(
        "grackle_nn/metrics.py:record_epoch",
        "<ndarray shape=(32, 2) dtype=dtype('float64')>"
      ),
    ];
    const series = extractEpochSeries(events);
    expect(series.points).toEqual([]);
    expect(series.dropped).toBe(0);
  });

  it("rejects a node_id where metrics.py:record_epoch is not exact-or-slash-preceded", () => {
    const events = [retEvent("pkg/mymetrics.py:record_epoch", "(0, 0.5, 0.5)")];
    const series = extractEpochSeries(events);
    expect(series.points).toEqual([]);
    expect(series.dropped).toBe(0);
  });

  it("accepts a slash-prefixed node_id", () => {
    const events = [
      retEvent("grackle_nn/metrics.py:record_epoch", "(0, 0.5, 0.5)"),
    ];
    const series = extractEpochSeries(events);
    expect(series.points).toHaveLength(1);
    expect(series.points[0]?.epoch).toBe(0);
  });

  it("accepts a bare node_id", () => {
    const events = [retEvent("metrics.py:record_epoch", "(0, 0.5, 0.5)")];
    const series = extractEpochSeries(events);
    expect(series.points).toHaveLength(1);
    expect(series.points[0]?.epoch).toBe(0);
  });

  it("skips an event with ret_truncated: true", () => {
    const events = [
      retEvent("grackle_nn/metrics.py:record_epoch", "(0, 0.5, 0.5)", true),
    ];
    const series = extractEpochSeries(events);
    expect(series.points).toEqual([]);
    expect(series.dropped).toBe(0);
  });
});

describe("extractEpochSeries — event indexing", () => {
  it("reports absolute array indices when interleaved with unrelated events", () => {
    const events: TraceEvent[] = [
      otherEvent("call", "grackle_nn/train.py:fit"), // 0
      otherEvent("call", "grackle_nn/metrics.py:record_epoch"), // 1
      retEvent("grackle_nn/metrics.py:record_epoch", "(0, 0.9, 0.1)"), // 2
      otherEvent("return", "grackle_nn/train.py:fit"), // 3
      otherEvent("call", "grackle_nn/train.py:fit"), // 4
      otherEvent("call", "grackle_nn/metrics.py:record_epoch"), // 5
      retEvent("grackle_nn/metrics.py:record_epoch", "(1, 0.8, 0.2)"), // 6
    ];
    const series = extractEpochSeries(events);
    expect(series.points.map((p) => p.eventIndex)).toEqual([2, 6]);
    expect(series.points.map((p) => p.epoch)).toEqual([0, 1]);
  });
});

describe("extractEpochSeries — multi-run rule", () => {
  it("returns only the last run's points and counts total runs", () => {
    const events = [
      retEvent("grackle_nn/metrics.py:record_epoch", "(0, 1.0, 0.1)"), // run 1
      retEvent("grackle_nn/metrics.py:record_epoch", "(1, 0.9, 0.2)"), // run 1
      retEvent("grackle_nn/metrics.py:record_epoch", "(2, 0.8, 0.3)"), // run 1
      retEvent("grackle_nn/metrics.py:record_epoch", "(0, 0.7, 0.4)"), // run 2 (restart)
      retEvent("grackle_nn/metrics.py:record_epoch", "(1, 0.6, 0.5)"), // run 2
    ];
    const series = extractEpochSeries(events);
    expect(series.runs).toBe(2);
    expect(series.points.map((p) => p.epoch)).toEqual([0, 1]);
    expect(series.points.map((p) => p.loss)).toEqual([0.7, 0.6]);
  });

  it("treats a single point that never restarts as one run", () => {
    const events = [
      retEvent("grackle_nn/metrics.py:record_epoch", "(0, 1.0, 0.1)"),
    ];
    const series = extractEpochSeries(events);
    expect(series.runs).toBe(1);
    expect(series.points).toHaveLength(1);
  });

  it("reports zero runs and empty points for no candidates", () => {
    const series = extractEpochSeries([]);
    expect(series.runs).toBe(0);
    expect(series.points).toEqual([]);
    expect(series.dropped).toBe(0);
  });

  it("treats an equal (non-increasing) epoch as a restart", () => {
    const events = [
      retEvent("grackle_nn/metrics.py:record_epoch", "(0, 1.0, 0.1)"), // run 1
      retEvent("grackle_nn/metrics.py:record_epoch", "(0, 0.9, 0.2)"), // run 2 (0 <= 0)
    ];
    const series = extractEpochSeries(events);
    expect(series.runs).toBe(2);
    expect(series.points).toHaveLength(1);
    expect(series.points[0]?.loss).toBe(0.9);
  });
});

describe("scanEpochCandidates — startIndex (incremental-scan support)", () => {
  const events = [
    otherEvent("call", "grackle_nn/train.py:fit"), // 0
    retEvent("grackle_nn/metrics.py:record_epoch", "(0, 1.0, 0.1)"), // 1
    otherEvent("call", "grackle_nn/train.py:fit"), // 2
    retEvent("grackle_nn/metrics.py:record_epoch", "(1, 0.5, 0.5)"), // 3
  ];

  it("defaults to scanning from index 0", () => {
    const { candidates, dropped } = scanEpochCandidates(events);
    expect(candidates.map((p) => p.eventIndex)).toEqual([1, 3]);
    expect(dropped).toBe(0);
  });

  it("scans only the tail starting at startIndex, keeping absolute eventIndex", () => {
    const { candidates } = scanEpochCandidates(events, 2);
    expect(candidates.map((p) => p.eventIndex)).toEqual([3]);
    expect(candidates[0]?.epoch).toBe(1);
  });

  it("returns nothing when startIndex is past the end", () => {
    const { candidates, dropped } = scanEpochCandidates(events, events.length);
    expect(candidates).toEqual([]);
    expect(dropped).toBe(0);
  });

  it("splitting a scan at any boundary and concatenating matches one full scan", () => {
    // Mirrors LossCurvePanel's incremental cache: a prefix scan (indices
    // 0..split-1, via a truncated array — slicing from 0 never shifts
    // earlier indices) plus a tail scan of the ORIGINAL array starting at
    // `split` (correct absolute indices by construction) must together
    // equal one full scan, for every possible split point.
    const full = scanEpochCandidates(events, 0);
    for (let split = 0; split <= events.length; split++) {
      const first = scanEpochCandidates(events.slice(0, split), 0);
      const second = scanEpochCandidates(events, split);
      const combined = first.candidates.concat(second.candidates);
      expect(combined).toEqual(full.candidates);
      expect(first.dropped + second.dropped).toBe(full.dropped);
    }
  });
});

describe("computeRunsFromCandidates", () => {
  it("mirrors extractEpochSeries' multi-run rule directly", () => {
    const events = [
      retEvent("grackle_nn/metrics.py:record_epoch", "(0, 1.0, 0.1)"),
      retEvent("grackle_nn/metrics.py:record_epoch", "(1, 0.9, 0.2)"),
      retEvent("grackle_nn/metrics.py:record_epoch", "(0, 0.7, 0.4)"), // restart
    ];
    const { candidates } = scanEpochCandidates(events);
    const { points, runs } = computeRunsFromCandidates(candidates);
    expect(runs).toBe(2);
    expect(points.map((p) => p.epoch)).toEqual([0]);
    expect(points[0]?.loss).toBe(0.7);
  });

  it("returns zero runs and empty points for no candidates", () => {
    const { points, runs } = computeRunsFromCandidates([]);
    expect(points).toEqual([]);
    expect(runs).toBe(0);
  });
});
