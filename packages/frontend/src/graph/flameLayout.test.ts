import { describe, expect, it } from "vitest";
import type { CallFrame } from "./callTree";
import { frameColor, hitTest, layoutFlame, maxDepth } from "./flameLayout";

/** Minimal frame factory for layout tests (timings are what layout reads). */
function frame(
  nodeId: string,
  totalNs: number,
  children: CallFrame[] = []
): CallFrame {
  return {
    nodeId,
    label: nodeId,
    threadId: 1,
    depth: 0,
    startNs: 0,
    endNs: totalNs,
    totalNs,
    selfNs: totalNs,
    count: 1,
    synthetic: false,
    raised: false,
    children,
  };
}

describe("layoutFlame", () => {
  it("returns nothing for an empty forest or zero width", () => {
    expect(layoutFlame([], { width: 100, rowHeight: 16 })).toEqual([]);
    expect(layoutFlame([frame("a", 10)], { width: 0, rowHeight: 16 })).toEqual(
      []
    );
  });

  it("spreads roots across the full width proportional to total time", () => {
    const rects = layoutFlame([frame("a", 30), frame("b", 10)], {
      width: 100,
      rowHeight: 16,
    });
    expect(rects).toHaveLength(2);
    expect(rects[0]?.x).toBe(0);
    expect(rects[0]?.w).toBe(75); // 30/40 * 100
    expect(rects[1]?.x).toBe(75);
    expect(rects[1]?.w).toBe(25); // 10/40 * 100
    // Roots fill the whole width.
    expect((rects[0]?.w ?? 0) + (rects[1]?.w ?? 0)).toBe(100);
  });

  it("places a child within its parent's slice, leaving self-time on the right", () => {
    // parent total 100 with one child total 40 → child occupies 40% of parent.
    const rects = layoutFlame([frame("p", 100, [frame("c", 40)])], {
      width: 200,
      rowHeight: 16,
    });
    const parent = rects.find((r) => r.frame.nodeId === "p");
    const child = rects.find((r) => r.frame.nodeId === "c");
    expect(parent?.w).toBe(200);
    expect(parent?.depth).toBe(0);
    expect(parent?.y).toBe(0);
    expect(child?.w).toBe(80); // 40/100 * 200
    expect(child?.x).toBe(0); // left-aligned within parent
    expect(child?.depth).toBe(1);
    expect(child?.y).toBe(16);
  });

  it("uses tree depth for the row, not frame_depth", () => {
    const deep = frame("deep", 10);
    deep.depth = 7; // a windowed forest root with non-zero frame_depth
    const rects = layoutFlame([deep], { width: 100, rowHeight: 20 });
    expect(rects[0]?.depth).toBe(0);
    expect(rects[0]?.y).toBe(0);
  });

  it("drops sub-minWidth rectangles", () => {
    const rects = layoutFlame([frame("big", 999), frame("tiny", 1)], {
      width: 100,
      rowHeight: 16,
      minWidth: 1,
    });
    expect(rects.map((r) => r.frame.nodeId)).toEqual(["big"]);
  });

  it("falls back to equal root slices when total time is zero", () => {
    const rects = layoutFlame([frame("a", 0), frame("b", 0)], {
      width: 100,
      rowHeight: 16,
    });
    expect(rects).toHaveLength(2);
    expect(rects[0]?.w).toBe(50);
    expect(rects[1]?.w).toBe(50);
  });

  it("renders ALL depths (not just the root row) when total time is zero", () => {
    // A nested zero-duration tree (e.g. every event shares one ts_ns on a
    // coarse clock) must still show its full structure via equal slicing.
    const tree = [frame("a", 0, [frame("b", 0, [frame("c", 0)])])];
    const rects = layoutFlame(tree, { width: 120, rowHeight: 16 });
    expect(rects.map((r) => r.frame.nodeId)).toEqual(["a", "b", "c"]);
    expect(rects[2]?.depth).toBe(2);
    // A lone child inherits its parent's full slice.
    expect(rects[1]?.w).toBe(120);
  });
});

describe("maxDepth", () => {
  it("is -1 for an empty forest", () => {
    expect(maxDepth([])).toBe(-1);
  });
  it("counts the deepest chain (root = 0)", () => {
    const forest = [frame("a", 10, [frame("b", 5, [frame("c", 2)])])];
    expect(maxDepth(forest)).toBe(2);
  });
});

describe("hitTest", () => {
  const rects = layoutFlame([frame("p", 100, [frame("c", 40)])], {
    width: 200,
    rowHeight: 16,
  });

  it("finds the rectangle containing a point", () => {
    expect(hitTest(rects, 10, 4)?.frame.nodeId).toBe("p"); // row 0
    expect(hitTest(rects, 10, 20)?.frame.nodeId).toBe("c"); // row 1, within child
  });

  it("returns null outside any rectangle", () => {
    expect(hitTest(rects, 199, 40)).toBeNull(); // below all rows
    expect(hitTest(rects, 150, 20)).toBeNull(); // row 1 but past child's width
  });

  it("treats the right/bottom edge as exclusive", () => {
    // child spans x [0,80) on row 1 (y [16,32)).
    expect(hitTest(rects, 80, 16)).toBeNull();
    expect(hitTest(rects, 79, 16)?.frame.nodeId).toBe("c");
  });
});

describe("frameColor", () => {
  it("is deterministic for the same id", () => {
    expect(frameColor("a.py:f")).toBe(frameColor("a.py:f"));
  });
  it("returns a canvas-safe hsl() string in the warm band", () => {
    const c = frameColor("a.py:f");
    const m = c.match(/^hsl\((\d+), \d+%, \d+%\)$/);
    expect(m).not.toBeNull();
    const hue = Number(m?.[1]);
    expect(hue).toBeGreaterThanOrEqual(18);
    expect(hue).toBeLessThanOrEqual(54);
  });
  it("dims to a desaturated variant", () => {
    expect(frameColor("a.py:f", true)).not.toBe(frameColor("a.py:f", false));
  });
});
