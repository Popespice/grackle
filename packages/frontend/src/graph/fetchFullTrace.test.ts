import type { TraceEvent, TraceWindowMessage } from "@grackle/shared-types";
import { describe, expect, it, vi } from "vitest";
import { fetchFullTrace } from "./fetchFullTrace";

function mkEvents(n: number): TraceEvent[] {
  return Array.from({ length: n }, (_, i) => ({
    event: "call",
    node_id: `a.py:f${i}`,
    ts_ns: i,
    thread_id: 1,
    frame_depth: 0,
  }));
}

/** A requester that serves windows from a backing array, echoing start_index. */
function makeRequester(all: TraceEvent[], total = all.length) {
  return vi.fn((sessionId: string, start: number, count: number) =>
    Promise.resolve({
      id: "x",
      type: "trace_window",
      payload: {
        session_id: sessionId,
        start_index: start,
        events: all.slice(start, start + count),
        total,
      },
    } as TraceWindowMessage)
  );
}

describe("fetchFullTrace", () => {
  it("pages the whole trace in chunks", async () => {
    const all = mkEvents(5);
    const req = makeRequester(all);
    const res = await fetchFullTrace(req, "s", 5, { chunk: 2 });
    expect(res.events).toHaveLength(5);
    expect(res.events.map((e) => e.node_id)).toEqual(all.map((e) => e.node_id));
    expect(res.truncated).toBe(false);
    expect(req).toHaveBeenCalledTimes(3); // 2 + 2 + 1
  });

  it("stops at the cap and reports truncation", async () => {
    const all = mkEvents(10);
    const req = makeRequester(all);
    const res = await fetchFullTrace(req, "s", 10, { chunk: 3, cap: 4 });
    expect(res.events).toHaveLength(4);
    expect(res.truncated).toBe(true);
  });

  it("returns nothing for an empty trace", async () => {
    const req = makeRequester([], 0);
    const res = await fetchFullTrace(req, "s", 0);
    expect(res.events).toEqual([]);
    expect(req).not.toHaveBeenCalled();
  });

  it("bails out if the server returns an empty window early", async () => {
    const req = vi.fn((sessionId: string, start: number) =>
      Promise.resolve({
        id: "x",
        type: "trace_window",
        payload: {
          session_id: sessionId,
          start_index: start,
          events: [],
          total: 100,
        },
      } as TraceWindowMessage)
    );
    const res = await fetchFullTrace(req, "s", 100, { chunk: 10 });
    expect(res.events).toEqual([]);
    expect(req).toHaveBeenCalledTimes(1); // gave up after the empty window
  });
});
