import type { TraceEvent } from "@grackle/shared-types";
import { describe, expect, it } from "vitest";
import {
  aggregateCallTree,
  buildCallTree,
  type CallFrame,
  frameLabel,
  hotPath,
} from "./callTree";

/** Build a trace event. `frame_depth` is the frame's own depth (call and the
 *  matching return carry the SAME depth — see callTree.ts). */
function ev(
  event: string,
  node_id: string,
  frame_depth: number,
  ts_ns: number,
  thread_id = 1
): TraceEvent {
  return { event, node_id, ts_ns, thread_id, frame_depth };
}

/** Collect every node id in the forest, depth-first. */
function ids(frames: CallFrame[]): string[] {
  const out: string[] = [];
  const walk = (fs: CallFrame[]): void => {
    for (const f of fs) {
      out.push(f.nodeId);
      walk(f.children);
    }
  };
  walk(frames);
  return out;
}

/** Assert every parent fully contains its children in time and self >= 0. */
function assertWellFormed(frames: CallFrame[]): void {
  const walk = (fs: CallFrame[]): void => {
    for (const f of fs) {
      expect(f.endNs).toBeGreaterThanOrEqual(f.startNs);
      expect(f.totalNs).toBe(Math.max(0, f.endNs - f.startNs));
      expect(f.selfNs).toBeGreaterThanOrEqual(0);
      let childTotal = 0;
      for (const c of f.children) {
        expect(c.startNs).toBeGreaterThanOrEqual(f.startNs);
        expect(c.endNs).toBeLessThanOrEqual(f.endNs);
        childTotal += c.totalNs;
      }
      expect(f.selfNs).toBe(Math.max(0, f.totalNs - childTotal));
      walk(f.children);
    }
  };
  walk(frames);
}

describe("frameLabel", () => {
  it("takes the qualname after the last colon", () => {
    expect(frameLabel("services/auth.py:AuthManager.verify")).toBe(
      "AuthManager.verify"
    );
  });
  it("takes the basename for file-level (colonless) ids", () => {
    expect(frameLabel("services/auth.py")).toBe("auth.py");
  });
  it("handles the unresolved sentinel", () => {
    expect(frameLabel("<unresolved>")).toBe("<unresolved>");
  });
});

describe("buildCallTree — basics", () => {
  it("returns an empty forest for no events", () => {
    const t = buildCallTree([]);
    expect(t.roots).toEqual([]);
    expect(t.frameCount).toBe(0);
    expect(t.totalNs).toBe(0);
    expect(t.hadSynthetic).toBe(false);
    expect(t.threads).toEqual([]);
  });

  it("pairs a single call/return into one frame", () => {
    const t = buildCallTree([
      ev("call", "a.py:f", 0, 100),
      ev("return", "a.py:f", 0, 160),
    ]);
    expect(t.roots).toHaveLength(1);
    expect(t.roots[0]?.nodeId).toBe("a.py:f");
    expect(t.roots[0]?.totalNs).toBe(60);
    expect(t.roots[0]?.selfNs).toBe(60);
    expect(t.roots[0]?.synthetic).toBe(false);
    expect(t.frameCount).toBe(1);
    expect(t.totalNs).toBe(60);
    assertWellFormed(t.roots);
  });

  it("nests a child and computes self vs total", () => {
    // f [0..100] contains g [20..60]
    const t = buildCallTree([
      ev("call", "a.py:f", 0, 0),
      ev("call", "a.py:g", 1, 20),
      ev("return", "a.py:g", 1, 60),
      ev("return", "a.py:f", 0, 100),
    ]);
    expect(t.roots).toHaveLength(1);
    const f = t.roots[0];
    expect(f?.totalNs).toBe(100);
    expect(f?.children).toHaveLength(1);
    expect(f?.children[0]?.nodeId).toBe("a.py:g");
    expect(f?.children[0]?.totalNs).toBe(40);
    expect(f?.selfNs).toBe(60); // 100 - 40
    assertWellFormed(t.roots);
  });

  it("attributes self time across sequential children", () => {
    // f [0..100], g [10..30], h [40..70] → self = 100 - 20 - 30 = 50
    const t = buildCallTree([
      ev("call", "a.py:f", 0, 0),
      ev("call", "a.py:g", 1, 10),
      ev("return", "a.py:g", 1, 30),
      ev("call", "a.py:h", 1, 40),
      ev("return", "a.py:h", 1, 70),
      ev("return", "a.py:f", 0, 100),
    ]);
    expect(t.roots[0]?.children).toHaveLength(2);
    expect(t.roots[0]?.selfNs).toBe(50);
    assertWellFormed(t.roots);
  });
});

describe("buildCallTree — recursion", () => {
  it("keeps recursive frames nested (depth disambiguates)", () => {
    const t = buildCallTree([
      ev("call", "a.py:fib", 0, 0),
      ev("call", "a.py:fib", 1, 5),
      ev("call", "a.py:fib", 2, 10),
      ev("return", "a.py:fib", 2, 15),
      ev("return", "a.py:fib", 1, 20),
      ev("return", "a.py:fib", 0, 30),
    ]);
    expect(ids(t.roots)).toEqual(["a.py:fib", "a.py:fib", "a.py:fib"]);
    // Chain: each fib has exactly one fib child.
    expect(t.roots[0]?.children).toHaveLength(1);
    expect(t.roots[0]?.children[0]?.children).toHaveLength(1);
    expect(t.frameCount).toBe(3);
    assertWellFormed(t.roots);
  });
});

describe("buildCallTree — exceptions (no unwind event)", () => {
  it("implicitly closes an exception-unwound frame when a sibling is later called", () => {
    // f calls g; g raises and unwinds (NO return event); f catches, calls h.
    const t = buildCallTree([
      ev("call", "a.py:f", 0, 0),
      ev("call", "a.py:g", 1, 10),
      ev("exception", "a.py:g", 1, 15),
      ev("call", "a.py:h", 1, 20), // g must have unwound by now
      ev("return", "a.py:h", 1, 30),
      ev("return", "a.py:f", 0, 40),
    ]);
    const f = t.roots[0];
    expect(f?.children).toHaveLength(2);
    const g = f?.children[0];
    const h = f?.children[1];
    expect(g?.nodeId).toBe("a.py:g");
    expect(g?.synthetic).toBe(true); // closed implicitly
    expect(g?.raised).toBe(true);
    expect(g?.endNs).toBe(20); // stamped at h's call ts
    expect(h?.synthetic).toBe(false);
    expect(t.hadSynthetic).toBe(true);
    assertWellFormed(t.roots);
  });

  it("closes a chain of frames that unwind to the top (uncaught) at stream end", () => {
    // f → g → (raise) → both unwind, no returns, stream ends.
    const t = buildCallTree([
      ev("call", "a.py:f", 0, 0),
      ev("call", "a.py:g", 1, 10),
      ev("exception", "a.py:g", 1, 20),
    ]);
    const f = t.roots[0];
    expect(f?.synthetic).toBe(true);
    expect(f?.raised).toBe(false); // exception was raised in g, not f
    expect(f?.children[0]?.nodeId).toBe("a.py:g");
    expect(f?.children[0]?.synthetic).toBe(true);
    expect(f?.children[0]?.raised).toBe(true);
    expect(t.hadSynthetic).toBe(true);
    // Both close at the last seen ts (20).
    expect(f?.endNs).toBe(20);
    assertWellFormed(t.roots);
  });

  it("tolerates an exception with an empty stack", () => {
    const t = buildCallTree([ev("exception", "a.py:f", 0, 5)]);
    expect(t.roots).toEqual([]);
    expect(t.frameCount).toBe(0);
  });

  it("attributes each exception to its own frame on re-raise / propagation", () => {
    // f → g → h; h raises, g re-raises, f catches and returns. h and g unwind
    // with NO return events, so both are still on the stack when their
    // exception events arrive — attribution must follow node_id, not the top.
    const t = buildCallTree([
      ev("call", "a.py:f", 0, 0),
      ev("call", "a.py:g", 1, 10),
      ev("call", "a.py:h", 2, 20),
      ev("exception", "a.py:h", 2, 25),
      ev("exception", "a.py:g", 1, 30), // g re-raises while h is still on top
      ev("return", "a.py:f", 0, 40),
    ]);
    const f = t.roots[0];
    const g = f?.children[0];
    const h = g?.children[0];
    expect(h?.raised).toBe(true);
    expect(g?.raised).toBe(true); // not dumped onto h
    expect(f?.raised).toBe(false);
    assertWellFormed(t.roots);
  });
});

describe("buildCallTree — truncation & windowing", () => {
  it("synthesizes closes for a stream truncated mid-frame (--max-events)", () => {
    const t = buildCallTree([
      ev("call", "a.py:f", 0, 0),
      ev("call", "a.py:g", 1, 10),
      // cut here — no returns
    ]);
    expect(t.roots).toHaveLength(1);
    expect(t.roots[0]?.synthetic).toBe(true);
    expect(t.roots[0]?.children[0]?.synthetic).toBe(true);
    expect(t.orphanReturns).toBe(0);
    expect(t.hadSynthetic).toBe(true);
    assertWellFormed(t.roots);
  });

  it("counts orphan returns for frames opened before a seekable window", () => {
    // Window begins mid-stack: a return at depth 1 with nothing open.
    const t = buildCallTree([
      ev("return", "a.py:g", 1, 5), // orphan — opened before the window
      ev("call", "a.py:h", 0, 10),
      ev("return", "a.py:h", 0, 20),
    ]);
    expect(t.orphanReturns).toBe(1);
    expect(ids(t.roots)).toEqual(["a.py:h"]);
    expect(t.roots[0]?.synthetic).toBe(false);
    assertWellFormed(t.roots);
  });

  it("makes a deep frame a forest root when its parent is outside the window", () => {
    // First event is a call already at depth 3.
    const t = buildCallTree([
      ev("call", "a.py:deep", 3, 0),
      ev("return", "a.py:deep", 3, 10),
    ]);
    expect(t.roots).toHaveLength(1);
    expect(t.roots[0]?.nodeId).toBe("a.py:deep");
    expect(t.roots[0]?.depth).toBe(3); // original frame_depth retained
    assertWellFormed(t.roots);
  });
});

describe("buildCallTree — threads", () => {
  it("reconstructs interleaved threads independently", () => {
    const t = buildCallTree([
      ev("call", "a.py:f", 0, 0, 1),
      ev("call", "a.py:p", 0, 1, 2),
      ev("call", "a.py:g", 1, 2, 1),
      ev("return", "a.py:g", 1, 3, 1),
      ev("return", "a.py:p", 0, 4, 2),
      ev("return", "a.py:f", 0, 5, 1),
    ]);
    expect(t.threads).toEqual([1, 2]);
    expect(t.roots).toHaveLength(2);
    const t1 = t.roots.find((r) => r.threadId === 1);
    const t2 = t.roots.find((r) => r.threadId === 2);
    expect(t1?.nodeId).toBe("a.py:f");
    expect(t1?.children[0]?.nodeId).toBe("a.py:g");
    expect(t2?.nodeId).toBe("a.py:p");
    expect(t2?.children).toHaveLength(0);
    assertWellFormed(t.roots);
  });
});

describe("buildCallTree — non-structural events", () => {
  it("ignores line and unknown event kinds", () => {
    const t = buildCallTree([
      ev("call", "a.py:f", 0, 0),
      ev("line", "a.py:f", 0, 5),
      ev("weird-future-kind", "a.py:f", 0, 6),
      ev("return", "a.py:f", 0, 10),
    ]);
    expect(t.roots).toHaveLength(1);
    expect(t.roots[0]?.children).toHaveLength(0);
    expect(t.frameCount).toBe(1);
    assertWellFormed(t.roots);
  });
});

describe("aggregateCallTree", () => {
  it("merges sibling frames with the same node id and sums counts/time", () => {
    // f calls g twice (sequential).
    const t = buildCallTree([
      ev("call", "a.py:f", 0, 0),
      ev("call", "a.py:g", 1, 10),
      ev("return", "a.py:g", 1, 30),
      ev("call", "a.py:g", 1, 40),
      ev("return", "a.py:g", 1, 70),
      ev("return", "a.py:f", 0, 100),
    ]);
    const agg = aggregateCallTree(t.roots);
    expect(agg).toHaveLength(1);
    expect(agg[0]?.children).toHaveLength(1); // two g's merged into one
    expect(agg[0]?.children[0]?.nodeId).toBe("a.py:g");
    expect(agg[0]?.children[0]?.count).toBe(2);
    expect(agg[0]?.children[0]?.totalNs).toBe(50); // 20 + 30
  });

  it("orders children by total time descending", () => {
    const t = buildCallTree([
      ev("call", "a.py:f", 0, 0),
      ev("call", "a.py:small", 1, 5),
      ev("return", "a.py:small", 1, 6),
      ev("call", "a.py:big", 1, 10),
      ev("return", "a.py:big", 1, 90),
      ev("return", "a.py:f", 0, 100),
    ]);
    const agg = aggregateCallTree(t.roots);
    expect(agg[0]?.children[0]?.nodeId).toBe("a.py:big");
    expect(agg[0]?.children[1]?.nodeId).toBe("a.py:small");
  });

  it("does not merge ancestors into descendants (recursion stays a chain)", () => {
    const t = buildCallTree([
      ev("call", "a.py:r", 0, 0),
      ev("call", "a.py:r", 1, 5),
      ev("return", "a.py:r", 1, 10),
      ev("return", "a.py:r", 0, 20),
    ]);
    const agg = aggregateCallTree(t.roots);
    expect(agg).toHaveLength(1);
    expect(agg[0]?.children).toHaveLength(1);
    expect(agg[0]?.children[0]?.nodeId).toBe("a.py:r");
    expect(agg[0]?.count).toBe(1);
  });
});

describe("hotPath", () => {
  it("follows the heaviest child chain", () => {
    const t = buildCallTree([
      ev("call", "a.py:f", 0, 0),
      ev("call", "a.py:cold", 1, 5),
      ev("return", "a.py:cold", 1, 6),
      ev("call", "a.py:hot", 1, 10),
      ev("call", "a.py:hotter", 2, 12),
      ev("return", "a.py:hotter", 2, 88),
      ev("return", "a.py:hot", 1, 90),
      ev("return", "a.py:f", 0, 100),
    ]);
    const agg = aggregateCallTree(t.roots);
    const path = hotPath(agg);
    const onPath = [...path].map((f) => f.nodeId).sort();
    expect(onPath).toEqual(["a.py:f", "a.py:hot", "a.py:hotter"]);
  });

  it("returns an empty set for an empty forest", () => {
    expect(hotPath([]).size).toBe(0);
  });
});
