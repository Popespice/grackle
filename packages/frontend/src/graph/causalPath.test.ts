import type { ArgValue, TraceEvent, TraceValues } from "@grackle/shared-types";
import { describe, expect, it } from "vitest";
import { ancestorStackAt } from "./ancestorStack";
import {
  causalPathAt,
  type Firing,
  firingsOf,
  MAX_FIRINGS,
  nearestFiring,
} from "./causalPath";

/** Build a trace event. `frame_depth` is the frame's own depth — call and the
 *  matching return carry the SAME depth (mirrors ancestorStack.test.ts). */
function ev(
  event: string,
  node_id: string,
  frame_depth: number,
  ts_ns: number,
  thread_id = 1,
  values?: TraceValues
): TraceEvent {
  const base: TraceEvent = { event, node_id, ts_ns, thread_id, frame_depth };
  return values ? { ...base, values } : base;
}

describe("firingsOf", () => {
  it("returns no firings for a node that never fired", () => {
    const events = [ev("call", "a.py:f", 0, 1), ev("return", "a.py:f", 0, 2)];
    const { firings, capped } = firingsOf(events, "a.py:never_called");
    expect(firings).toEqual([]);
    expect(capped).toBe(false);
  });

  it("finds every call event for a node, in order, with its args", () => {
    const args: ArgValue[] = [{ name: "x", repr: "1" }];
    const events = [
      ev("call", "a.py:main", 0, 1),
      ev("call", "a.py:helper", 1, 2, 1, { args }),
      ev("return", "a.py:helper", 1, 3),
      ev("call", "a.py:helper", 1, 4, 2), // second firing, different thread
    ];
    const { firings, capped } = firingsOf(events, "a.py:helper");
    expect(capped).toBe(false);
    expect(firings).toHaveLength(2);
    expect(firings[0]).toEqual({
      callIndex: 1,
      threadId: 1,
      tsNs: 2,
      args,
    });
    expect(firings[1]).toEqual({ callIndex: 3, threadId: 2, tsNs: 4 });
    expect(firings[1]?.args).toBeUndefined(); // uncaptured
  });

  it("caps enumeration at MAX_FIRINGS and reports capped", () => {
    const events: TraceEvent[] = [];
    for (let i = 0; i < MAX_FIRINGS + 5; i++) {
      events.push(ev("call", "a.py:hot", 0, i));
    }
    const { firings, capped } = firingsOf(events, "a.py:hot");
    expect(firings).toHaveLength(MAX_FIRINGS);
    expect(capped).toBe(true);
    // The collected firings are the FIRST MAX_FIRINGS, not an arbitrary subset.
    expect(firings[0]?.callIndex).toBe(0);
    expect(firings[MAX_FIRINGS - 1]?.callIndex).toBe(MAX_FIRINGS - 1);
  });

  it("is not capped when the count exactly equals MAX_FIRINGS", () => {
    const events: TraceEvent[] = [];
    for (let i = 0; i < MAX_FIRINGS; i++) {
      events.push(ev("call", "a.py:hot", 0, i));
    }
    const { firings, capped } = firingsOf(events, "a.py:hot");
    expect(firings).toHaveLength(MAX_FIRINGS);
    expect(capped).toBe(false);
  });

  it("ignores return/line events and events for other nodes", () => {
    const events = [
      ev("return", "a.py:f", 0, 1),
      ev("line", "a.py:f", 0, 2),
      ev("call", "a.py:g", 0, 3),
    ];
    expect(firingsOf(events, "a.py:f").firings).toEqual([]);
  });
});

describe("nearestFiring", () => {
  const firings: Firing[] = [
    { callIndex: 5, threadId: 1, tsNs: 5 },
    { callIndex: 20, threadId: 1, tsNs: 20 },
    { callIndex: 50, threadId: 1, tsNs: 50 },
  ];

  it("returns the firing exactly at the playhead", () => {
    expect(nearestFiring(firings, 20)).toBe(1);
  });

  it("prefers the earlier (<=) firing when the playhead sits between two", () => {
    expect(nearestFiring(firings, 30)).toBe(1); // between 20 and 50 → 20
  });

  it("falls back to the earliest firing when the playhead precedes all of them", () => {
    expect(nearestFiring(firings, 0)).toBe(0);
  });

  it("returns the last firing when the playhead is past all of them", () => {
    expect(nearestFiring(firings, 999)).toBe(2);
  });

  it("returns -1 for an empty array (defensive; never a supported input)", () => {
    expect(nearestFiring([], 10)).toBe(-1);
  });
});

describe("causalPathAt", () => {
  it("returns a single-hop path for a depth-0 root firing", () => {
    const events = [ev("call", "a.py:main", 0, 1)];
    const path = causalPathAt(events, 0, 1);
    expect(path).toHaveLength(1);
    expect(path[0]?.nodeId).toBe("a.py:main");
    expect(path[0]?.callIndex).toBe(0);
  });

  it("returns the full ancestor chain root-first, THIS last, with per-hop args", () => {
    const events = [
      ev("call", "a.py:main", 0, 1, 1, {
        args: [{ name: "argv", repr: "[]" }],
      }),
      ev("call", "a.py:handle", 1, 2, 1, {
        args: [{ name: "req", repr: "<R>" }],
      }),
      ev("call", "a.py:validate", 2, 3, 1, {
        args: [{ name: "email", repr: "'a@b'" }],
      }),
    ];
    // Firing = the validate call at index 2.
    const path = causalPathAt(events, 2, 1);
    expect(path.map((f) => f.nodeId)).toEqual([
      "a.py:main",
      "a.py:handle",
      "a.py:validate",
    ]);
    expect(path.map((f) => f.depth)).toEqual([0, 1, 2]);
    expect(path[0]?.args).toEqual([{ name: "argv", repr: "[]" }]);
    expect(path[1]?.args).toEqual([{ name: "req", repr: "<R>" }]);
    expect(path[2]?.args).toEqual([{ name: "email", repr: "'a@b'" }]);
    // THIS is the last element and matches the firing itself.
    expect(path[path.length - 1]?.nodeId).toBe("a.py:validate");
    expect(path[path.length - 1]?.callIndex).toBe(2);
  });

  it("keeps recursive invocations distinct by callIndex with monotonic depths", () => {
    const events = [
      ev("call", "a.py:f", 0, 1),
      ev("call", "a.py:f", 1, 2), // recursive call
      ev("call", "a.py:f", 2, 3), // recursive call again
    ];
    const path = causalPathAt(events, 2, 1);
    expect(path.map((f) => f.nodeId)).toEqual(["a.py:f", "a.py:f", "a.py:f"]);
    expect(path.map((f) => f.callIndex)).toEqual([0, 1, 2]);
    expect(path.map((f) => f.depth)).toEqual([0, 1, 2]);
    // Every hop has a distinct callIndex, so React keys on callIndex never collide.
    expect(new Set(path.map((f) => f.callIndex)).size).toBe(3);
  });

  it("returns [] for a thread with no frames (defensive — unreachable for a real Firing)", () => {
    const events = [ev("call", "a.py:main", 0, 1, 1)];
    expect(causalPathAt(events, 0, 999)).toEqual([]);
  });

  it("agrees with ancestorStackAt directly (drift tripwire)", () => {
    const events = [
      ev("call", "a.py:main", 0, 1),
      ev("call", "a.py:handle", 1, 2),
      ev("call", "a.py:validate", 2, 3),
    ];
    const deepestCallIndex = 2;
    const thread = 1;
    const viaCausalPath = causalPathAt(events, deepestCallIndex, thread);
    const viaAncestorStack =
      ancestorStackAt(events, deepestCallIndex).byThread.get(thread) ?? [];
    expect(viaCausalPath).toEqual(viaAncestorStack);
  });

  it("is correct for a callIndex within a truncated (prefix) trace", () => {
    // The prefix [0, callIndex] is identical whether or not events exist past
    // it — truncation past callIndex must never change the reconstructed path.
    const fullEvents = [
      ev("call", "a.py:main", 0, 1),
      ev("call", "a.py:handle", 1, 2),
      ev("call", "a.py:validate", 2, 3),
      ev("call", "a.py:later", 3, 4), // exists only in the "untruncated" run
    ];
    const truncatedPrefix = fullEvents.slice(0, 3); // events[0..2], as if events[3] were never paged

    const pathFromTruncated = causalPathAt(truncatedPrefix, 2, 1);
    const pathFromFull = causalPathAt(fullEvents, 2, 1);
    expect(pathFromTruncated).toEqual(pathFromFull);
  });
});
