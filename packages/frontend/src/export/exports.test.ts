import type { TraceEvent } from "@grackle/shared-types";
import { describe, expect, it } from "vitest";
import { buildCallTree, type CallFrame } from "../graph/callTree";
import {
  type ChromeTraceFile,
  exportChromeTrace,
  importChromeTraceTree,
  parseChromeTrace,
} from "./chromeTrace";
import {
  exportSpeedscope,
  importSpeedscopeTree,
  parseSpeedscope,
} from "./speedscope";

function ev(
  event: string,
  node_id: string,
  frame_depth: number,
  ts_ns: number,
  thread_id = 1
): TraceEvent {
  return { event, node_id, ts_ns, thread_id, frame_depth };
}

/** Recursive (nodeId, totalNs) signature of a forest, for round-trip equality. */
function shape(frames: CallFrame[]): unknown {
  return frames.map((f) => ({
    id: f.nodeId,
    total: f.totalNs,
    self: f.selfNs,
    children: shape(f.children),
  }));
}

// f [0..100] { g [20..60], h [70..90] }, single thread.
const EVENTS: TraceEvent[] = [
  ev("call", "a.py:f", 0, 0),
  ev("call", "a.py:g", 1, 20),
  ev("return", "a.py:g", 1, 60),
  ev("call", "a.py:h", 1, 70),
  ev("return", "a.py:h", 1, 90),
  ev("return", "a.py:f", 0, 100),
];

// Chrome trace is microsecond-resolution, so use ns that are multiples of 1000
// to round-trip exactly.
const US_EVENTS: TraceEvent[] = [
  ev("call", "a.py:f", 0, 0),
  ev("call", "a.py:g", 1, 20_000),
  ev("return", "a.py:g", 1, 60_000),
  ev("call", "a.py:h", 1, 70_000),
  ev("return", "a.py:h", 1, 90_000),
  ev("return", "a.py:f", 0, 100_000),
];

describe("exportSpeedscope", () => {
  it("emits a valid evented file with interned frames", () => {
    const file = exportSpeedscope(buildCallTree(EVENTS));
    expect(file.$schema).toBe(
      "https://www.speedscope.app/file-format-schema.json"
    );
    expect(file.exporter).toBe("grackle");
    expect(file.shared.frames.map((f) => f.name)).toEqual([
      "a.py:f",
      "a.py:g",
      "a.py:h",
    ]);
    expect(file.profiles).toHaveLength(1);
    const p = file.profiles[0];
    expect(p?.type).toBe("evented");
    expect(p?.unit).toBe("nanoseconds");
    expect(p?.startValue).toBe(0);
    expect(p?.endValue).toBe(100);
    // Open/close events are balanced.
    const opens = p?.events.filter((e) => e.type === "O").length;
    const closes = p?.events.filter((e) => e.type === "C").length;
    expect(opens).toBe(3);
    expect(closes).toBe(3);
    // `at` is non-decreasing in emission order (valid nesting).
    const ats = (p?.events ?? []).map((e) => e.at);
    expect([...ats].sort((a, b) => a - b)).toEqual(ats);
  });

  it("emits one profile per thread", () => {
    const file = exportSpeedscope(
      buildCallTree([
        ev("call", "a.py:f", 0, 0, 1),
        ev("return", "a.py:f", 0, 10, 1),
        ev("call", "a.py:p", 0, 5, 2),
        ev("return", "a.py:p", 0, 15, 2),
      ])
    );
    expect(file.profiles.map((p) => p.name)).toEqual(["thread 1", "thread 2"]);
  });

  it("produces an empty file for an empty tree", () => {
    const file = exportSpeedscope(buildCallTree([]));
    expect(file.profiles).toEqual([]);
    expect(file.shared.frames).toEqual([]);
  });

  it("round-trips through parseSpeedscope preserving structure and timing", () => {
    const tree1 = buildCallTree(EVENTS);
    const file = exportSpeedscope(tree1);
    const events2 = parseSpeedscope(file);
    const tree2 = buildCallTree(events2);
    expect(shape(tree2.roots)).toEqual(shape(tree1.roots));
  });

  it("round-trips a multi-thread tree", () => {
    const original = buildCallTree([
      ev("call", "a.py:f", 0, 0, 1),
      ev("call", "a.py:g", 1, 5, 1),
      ev("return", "a.py:g", 1, 8, 1),
      ev("return", "a.py:f", 0, 10, 1),
      ev("call", "a.py:p", 0, 2, 7),
      ev("return", "a.py:p", 0, 20, 7),
    ]);
    const reimported = importSpeedscopeTree(exportSpeedscope(original));
    expect(reimported.threads).toEqual([1, 7]);
    expect(shape(reimported.roots)).toEqual(shape(original.roots));
  });
});

describe("exportChromeTrace", () => {
  it("emits complete (X) events in microseconds", () => {
    const file = exportChromeTrace(buildCallTree(US_EVENTS));
    expect(file.displayTimeUnit).toBe("ns");
    expect(file.traceEvents).toHaveLength(3);
    const f = file.traceEvents.find((e) => e.name === "a.py:f");
    expect(f?.ph).toBe("X");
    expect(f?.ts).toBe(0);
    expect(f?.dur).toBe(100); // 100_000 ns / 1000
    expect(f?.pid).toBe(1);
    expect(f?.tid).toBe(1);
    const g = file.traceEvents.find((e) => e.name === "a.py:g");
    expect(g?.ts).toBe(20);
    expect(g?.dur).toBe(40);
  });

  it("round-trips through parseChromeTrace preserving structure and timing", () => {
    const tree1 = buildCallTree(US_EVENTS);
    const file = exportChromeTrace(tree1);
    const tree2 = buildCallTree(parseChromeTrace(file));
    expect(shape(tree2.roots)).toEqual(shape(tree1.roots));
  });

  it("imports begin/end (B/E) events from a third-party trace", () => {
    const file: ChromeTraceFile = {
      displayTimeUnit: "ns",
      traceEvents: [
        {
          name: "ext:outer",
          cat: "fn",
          ph: "B",
          ts: 0,
          dur: 0,
          pid: 1,
          tid: 4,
        },
        {
          name: "ext:inner",
          cat: "fn",
          ph: "B",
          ts: 1,
          dur: 0,
          pid: 1,
          tid: 4,
        },
        {
          name: "ext:inner",
          cat: "fn",
          ph: "E",
          ts: 5,
          dur: 0,
          pid: 1,
          tid: 4,
        },
        {
          name: "ext:outer",
          cat: "fn",
          ph: "E",
          ts: 9,
          dur: 0,
          pid: 1,
          tid: 4,
        },
      ],
    };
    const tree = importChromeTraceTree(file);
    expect(tree.roots[0]?.nodeId).toBe("ext:outer");
    expect(tree.roots[0]?.children[0]?.nodeId).toBe("ext:inner");
    expect(tree.roots[0]?.threadId).toBe(4);
  });

  it("rebuilds nesting from overlapping X events via the containment stack", () => {
    const tree = importChromeTraceTree({
      displayTimeUnit: "ns",
      traceEvents: [
        { name: "p", cat: "fn", ph: "X", ts: 0, dur: 100, pid: 1, tid: 1 },
        { name: "c", cat: "fn", ph: "X", ts: 10, dur: 30, pid: 1, tid: 1 },
      ],
    });
    expect(tree.roots).toHaveLength(1);
    expect(tree.roots[0]?.nodeId).toBe("p");
    expect(tree.roots[0]?.children[0]?.nodeId).toBe("c");
  });

  it("nests mixed complete (X) and begin/end (B/E) events on one thread", () => {
    // An X frame containing a B/E frame must reconstruct as outer { inner } —
    // a single time-ordered tree, not two disjoint phase passes.
    const tree = importChromeTraceTree({
      displayTimeUnit: "ns",
      traceEvents: [
        { name: "outer", cat: "fn", ph: "X", ts: 0, dur: 100, pid: 1, tid: 1 },
        { name: "inner", cat: "fn", ph: "B", ts: 10, pid: 1, tid: 1 },
        { name: "inner", cat: "fn", ph: "E", ts: 20, pid: 1, tid: 1 },
      ],
    });
    expect(tree.roots).toHaveLength(1);
    expect(tree.roots[0]?.nodeId).toBe("outer");
    expect(tree.roots[0]?.children[0]?.nodeId).toBe("inner");
  });
});
