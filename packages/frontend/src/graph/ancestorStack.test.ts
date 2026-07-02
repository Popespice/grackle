import type { ArgValue, TraceEvent, TraceValues } from "@grackle/shared-types";
import { describe, expect, it } from "vitest";
import {
  ancestorStackAt,
  nextCallBoundary,
  prevCallBoundary,
} from "./ancestorStack";
import { buildCallTree, type CallFrame } from "./callTree";

/** Build a trace event. `frame_depth` is the frame's own depth — call and the
 *  matching return carry the SAME depth (mirrors callTree.test.ts). */
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

/** nodeIds of a thread's open stack, outermost-first (push order). */
function openIds(events: TraceEvent[], index: number, thread = 1): string[] {
  const { byThread } = ancestorStackAt(events, index);
  return (byThread.get(thread) ?? []).map((f) => f.nodeId);
}

/** Collect every frame in a call forest that was closed implicitly. */
function syntheticIds(frames: CallFrame[]): Set<string> {
  const out = new Set<string>();
  const walk = (fs: CallFrame[]): void => {
    for (const f of fs) {
      if (f.synthetic) out.add(f.nodeId);
      walk(f.children);
    }
  };
  walk(frames);
  return out;
}

describe("ancestorStackAt", () => {
  it("returns an empty result for no events", () => {
    const { byThread, activeThreadId } = ancestorStackAt([], 0);
    expect(byThread.size).toBe(0);
    expect(activeThreadId).toBeNull();
  });

  it("holds a single open frame with its captured args", () => {
    const args: ArgValue[] = [{ name: "x", repr: "42" }];
    const events = [ev("call", "a.py:f", 0, 1, 1, { args })];
    const { byThread, activeThreadId } = ancestorStackAt(events, 0);
    expect(activeThreadId).toBe(1);
    const stack = byThread.get(1) ?? [];
    expect(stack).toHaveLength(1);
    expect(stack[0]?.nodeId).toBe("a.py:f");
    expect(stack[0]?.label).toBe("f");
    expect(stack[0]?.callIndex).toBe(0);
    expect(stack[0]?.args).toEqual(args);
  });

  it("pops a frame once its matching return is replayed", () => {
    const events = [
      ev("call", "a.py:f", 0, 1),
      ev("return", "a.py:f", 0, 2, 1, { ret: "None" }),
    ];
    expect(openIds(events, 0)).toEqual(["a.py:f"]); // before the return
    expect(openIds(events, 1)).toEqual([]); // after the return
  });

  it("reconstructs a nested stack innermost-last with per-frame args", () => {
    const events = [
      ev("call", "a.py:main", 0, 1, 1, {
        args: [{ name: "argv", repr: "[]" }],
      }),
      ev("call", "a.py:handle", 1, 2, 1, {
        args: [{ name: "req", repr: "<R>" }],
      }),
      ev("call", "a.py:validate", 2, 3, 1),
    ];
    const { byThread } = ancestorStackAt(events, 2);
    const stack = byThread.get(1) ?? [];
    expect(stack.map((f) => f.nodeId)).toEqual([
      "a.py:main",
      "a.py:handle",
      "a.py:validate",
    ]);
    expect(stack[0]?.args).toEqual([{ name: "argv", repr: "[]" }]);
    expect(stack[1]?.args).toEqual([{ name: "req", repr: "<R>" }]);
    expect(stack[2]?.args).toBeUndefined(); // uncaptured frame
  });

  it("silently unwinds frames left open by an exception (call at shallower depth)", () => {
    // main → a → b, then a sibling call at depth 1 closes a and b implicitly.
    const events = [
      ev("call", "a.py:main", 0, 1),
      ev("call", "a.py:a", 1, 2),
      ev("call", "a.py:b", 2, 3),
      ev("call", "a.py:c", 1, 4), // depth 1 → pops b(2) and a(1)
    ];
    expect(openIds(events, 3)).toEqual(["a.py:main", "a.py:c"]);
  });

  it("ignores an orphan return with no matching open frame", () => {
    const events = [
      ev("return", "a.py:gone", 0, 1), // opened before this window
      ev("call", "a.py:f", 0, 2),
    ];
    expect(() => ancestorStackAt(events, 1)).not.toThrow();
    expect(openIds(events, 1)).toEqual(["a.py:f"]);
  });

  it("reconstructs interleaved threads independently", () => {
    const events = [
      ev("call", "a.py:main", 0, 1, 1),
      ev("call", "w.py:work", 0, 2, 2),
      ev("call", "w.py:step", 1, 3, 2),
      ev("call", "a.py:handle", 1, 4, 1),
    ];
    const { byThread, activeThreadId } = ancestorStackAt(events, 3);
    expect(activeThreadId).toBe(1); // thread of events[3]
    expect((byThread.get(1) ?? []).map((f) => f.nodeId)).toEqual([
      "a.py:main",
      "a.py:handle",
    ]);
    expect((byThread.get(2) ?? []).map((f) => f.nodeId)).toEqual([
      "w.py:work",
      "w.py:step",
    ]);
  });

  it("clamps an out-of-range index without throwing", () => {
    const events = [ev("call", "a.py:f", 0, 1)];
    expect(() => ancestorStackAt(events, 999)).not.toThrow();
    expect(() => ancestorStackAt(events, -5)).not.toThrow();
    expect(openIds(events, 999)).toEqual(["a.py:f"]);
    expect(openIds(events, -5)).toEqual(["a.py:f"]);
  });

  it("reconstructs the stack even when no values were captured", () => {
    const events = [ev("call", "a.py:f", 0, 1), ev("call", "a.py:g", 1, 2)];
    const { byThread } = ancestorStackAt(events, 1);
    const stack = byThread.get(1) ?? [];
    expect(stack.map((f) => f.nodeId)).toEqual(["a.py:f", "a.py:g"]);
    expect(stack.every((f) => f.args === undefined)).toBe(true);
  });

  it("agrees with buildCallTree on which frames stay open at the end (drift tripwire)", () => {
    // A clean run (no exception unwind) truncated mid-stack: b returns, a and
    // main stay open. buildCallTree marks exactly the still-open frames
    // synthetic at stream end — they must equal ancestorStackAt's open set.
    const events = [
      ev("call", "a.py:main", 0, 1),
      ev("call", "a.py:a", 1, 2),
      ev("call", "a.py:b", 2, 3),
      ev("return", "a.py:b", 2, 4),
    ];
    const tree = buildCallTree(events);
    const synthetic = syntheticIds(tree.roots);

    const { byThread } = ancestorStackAt(events, events.length - 1);
    const open = new Set<string>();
    for (const stack of byThread.values()) {
      for (const f of stack) open.add(f.nodeId);
    }
    expect(open).toEqual(synthetic);
    expect(open).toEqual(new Set(["a.py:main", "a.py:a"]));
  });
});

describe("call/return boundary helpers", () => {
  const events = [
    ev("call", "a.py:f", 0, 1),
    ev("line", "a.py:f", 0, 2),
    ev("line", "a.py:f", 0, 3),
    ev("return", "a.py:f", 0, 4),
    ev("line", "a.py:g", 0, 5),
    ev("call", "a.py:g", 0, 6),
  ];

  it("nextCallBoundary skips line/exception and lands on call/return", () => {
    expect(nextCallBoundary(events, 0)).toBe(3); // past the two lines to return
    expect(nextCallBoundary(events, 3)).toBe(5); // past the line at 4 to the call
  });

  it("prevCallBoundary scans backwards to the nearest structural event", () => {
    expect(prevCallBoundary(events, 3)).toBe(0);
    expect(prevCallBoundary(events, 6)).toBe(5);
  });

  it("returns null at the ends", () => {
    expect(nextCallBoundary(events, 5)).toBeNull();
    expect(prevCallBoundary(events, 0)).toBeNull();
  });
});
