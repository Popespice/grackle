import { describe, expect, it } from "vitest";
import {
  type EpochPoint,
  layoutLossCurve,
  PADDING_BOTTOM,
  PADDING_LEFT,
  PADDING_RIGHT,
  PADDING_TOP,
} from "./lossCurveLayout";

/** Minimal point factory (eventIndex is irrelevant to layout math). */
function point(
  epoch: number,
  loss: number,
  accuracy: number,
  eventIndex = epoch
): EpochPoint {
  return { epoch, loss, accuracy, eventIndex };
}

// Shared fixture: 3 points, width 240 / height 140, chosen so the padding
// constants (40/40/12/24) produce a plot area of exactly 160x104 and every
// loss/accuracy pixel works out to a whole number.
const WIDTH = 240;
const HEIGHT = 140;
const PLOT_LEFT = PADDING_LEFT; // 40
const PLOT_RIGHT = WIDTH - PADDING_RIGHT; // 200
const PLOT_TOP = PADDING_TOP; // 12
const PLOT_BOTTOM = HEIGHT - PADDING_BOTTOM; // 116

const THREE_POINTS: EpochPoint[] = [
  point(0, 1.0, 0.5),
  point(1, 0.5, 0.75),
  point(2, 0.25, 1.0),
];

describe("layoutLossCurve — known 3-point series", () => {
  const layout = layoutLossCurve(THREE_POINTS, WIDTH, HEIGHT);

  it("returns a non-null layout", () => {
    expect(layout).not.toBeNull();
  });

  it("computes exact lossPoints (left axis, [0, maxLoss=1.0] -> [116, 12])", () => {
    expect(layout?.lossPoints).toEqual([
      { x: 40, y: 12 }, // loss 1.0 -> maxLoss -> top
      { x: 120, y: 64 }, // loss 0.5 -> midpoint
      { x: 200, y: 90 }, // loss 0.25 -> 3/4 down
    ]);
  });

  it("computes exact accPoints (right axis, fixed [0, 1] -> [116, 12])", () => {
    expect(layout?.accPoints).toEqual([
      { x: 40, y: 64 }, // accuracy 0.5 -> midpoint
      { x: 120, y: 38 }, // accuracy 0.75
      { x: 200, y: 12 }, // accuracy 1.0 -> top
    ]);
  });

  it("computes exact xTicks (integer epochs 0/1/2, all within the <=6 budget)", () => {
    expect(layout?.xTicks).toEqual([
      { pos: 40, label: "0" },
      { pos: 120, label: "1" },
      { pos: 200, label: "2" },
    ]);
  });

  it("computes 4 lossTicks spanning [0, maxLoss]", () => {
    const ticks = layout?.lossTicks ?? [];
    expect(ticks).toHaveLength(4);
    // pos values follow the same formula as lossPoints: PLOT_BOTTOM - value*104.
    expect(ticks[0]).toEqual({ pos: PLOT_BOTTOM, label: "0.00" });
    expect(ticks[3]).toEqual({ pos: PLOT_TOP, label: "1.00" });
    expect(ticks[1]?.pos).toBeCloseTo(PLOT_BOTTOM - (1 / 3) * 104, 10);
    expect(ticks[1]?.label).toBe("0.33");
    expect(ticks[2]?.pos).toBeCloseTo(PLOT_BOTTOM - (2 / 3) * 104, 10);
    expect(ticks[2]?.label).toBe("0.67");
  });

  it("computes fixed accTicks covering 0..1", () => {
    expect(layout?.accTicks).toEqual([
      { pos: 116, label: "0%" },
      { pos: 90, label: "25%" },
      { pos: 64, label: "50%" },
      { pos: 38, label: "75%" },
      { pos: 12, label: "100%" },
    ]);
  });

  it("xForEpochIndex returns the lossPoints x for a valid index", () => {
    expect(layout?.xForEpochIndex(0)).toBe(40);
    expect(layout?.xForEpochIndex(1)).toBe(120);
    expect(layout?.xForEpochIndex(2)).toBe(200);
  });

  it("xForEpochIndex clamps out-of-range indices instead of returning NaN", () => {
    expect(layout?.xForEpochIndex(-1)).toBe(40);
    expect(layout?.xForEpochIndex(99)).toBe(200);
  });
});

describe("layoutLossCurve — hitTest", () => {
  const layout = layoutLossCurve(THREE_POINTS, WIDTH, HEIGHT);

  it("returns the exact index at each point's x-coordinate", () => {
    expect(layout?.hitTest(40)).toBe(0);
    expect(layout?.hitTest(120)).toBe(1);
    expect(layout?.hitTest(200)).toBe(2);
  });

  it("snaps to the nearer point off-exact", () => {
    expect(layout?.hitTest(100)).toBe(1); // closer to 120 than 40
    expect(layout?.hitTest(50)).toBe(0); // closer to 40 than 120
    expect(layout?.hitTest(190)).toBe(2); // closer to 200 than 120
  });

  it("resolves an exact midpoint tie to the earlier index", () => {
    expect(layout?.hitTest(80)).toBe(0); // exactly between x=40 and x=120
    expect(layout?.hitTest(160)).toBe(1); // exactly between x=120 and x=200
  });

  it("clamps to the first point left of the plot area", () => {
    expect(layout?.hitTest(0)).toBe(0);
    expect(layout?.hitTest(-1000)).toBe(0);
  });

  it("clamps to the last point right of the plot area", () => {
    expect(layout?.hitTest(WIDTH)).toBe(2);
    expect(layout?.hitTest(10_000)).toBe(2);
  });
});

describe("layoutLossCurve — accPoints clamping", () => {
  // record_epoch's third field is "accuracy" (in [0,1]) for train.py's
  // classification loop, but a raw, unbounded validation loss for
  // heat_model.py's regression loop — both share this beacon/axis. An
  // out-of-range value must draw pinned to the plot boundary rather than
  // rendering off-canvas.
  it("clamps an accuracy value above 1 to the plot top", () => {
    const points = [point(0, 1, 0.5), point(1, 0.5, 2.5)];
    const layout = layoutLossCurve(points, WIDTH, HEIGHT);
    expect(layout?.accPoints[1]).toEqual({ x: 200, y: PLOT_TOP });
  });

  it("clamps a negative accuracy value to the plot bottom", () => {
    const points = [point(0, 1, 0.5), point(1, 0.5, -1)];
    const layout = layoutLossCurve(points, WIDTH, HEIGHT);
    expect(layout?.accPoints[1]).toEqual({ x: 200, y: PLOT_BOTTOM });
  });
});

describe("layoutLossCurve — degenerate inputs", () => {
  it("returns null for zero points", () => {
    expect(layoutLossCurve([], WIDTH, HEIGHT)).toBeNull();
  });

  it("returns null for a single point", () => {
    expect(layoutLossCurve([point(0, 1, 0.5)], WIDTH, HEIGHT)).toBeNull();
  });

  it("returns null for zero width", () => {
    expect(layoutLossCurve(THREE_POINTS, 0, HEIGHT)).toBeNull();
  });

  it("returns null for zero height", () => {
    expect(layoutLossCurve(THREE_POINTS, WIDTH, 0)).toBeNull();
  });

  it("returns null for a too-small-but-positive width", () => {
    expect(layoutLossCurve(THREE_POINTS, 20, HEIGHT)).toBeNull();
  });

  it("returns null for a too-small-but-positive height", () => {
    expect(layoutLossCurve(THREE_POINTS, WIDTH, 20)).toBeNull();
  });
});

describe("layoutLossCurve — xTicks stay integers on an uneven epoch range", () => {
  it("produces at most 6 integer-epoch ticks for an 8-point (0..7) run", () => {
    const points = Array.from({ length: 8 }, (_, i) => point(i, 1, 0.5));
    const layout = layoutLossCurve(points, 300, HEIGHT);
    const ticks = layout?.xTicks ?? [];

    expect(ticks.length).toBeLessThanOrEqual(6);
    for (const tick of ticks) {
      expect(Number.isInteger(Number(tick.label))).toBe(true);
      expect(tick.label).toBe(String(Math.trunc(Number(tick.label))));
    }
    // 8 candidate epochs (0..7) squeezed into a 6-tick budget: not every
    // integer epoch is kept, but the ones kept must still be exact integers,
    // strictly increasing, and land inside the plotted epoch range.
    expect(ticks).toEqual([
      { pos: expect.any(Number), label: "0" },
      { pos: expect.any(Number), label: "1" },
      { pos: expect.any(Number), label: "3" },
      { pos: expect.any(Number), label: "4" },
      { pos: expect.any(Number), label: "6" },
      { pos: expect.any(Number), label: "7" },
    ]);
  });
});

describe("layoutLossCurve — flat baseline when maxLoss <= 0", () => {
  it("pins every lossPoint.y to the bottom of the plot area", () => {
    const points = [point(0, 0, 0.1), point(1, 0, 0.4), point(2, 0, 0.9)];
    const layout = layoutLossCurve(points, WIDTH, HEIGHT);

    expect(layout?.lossPoints.every((p) => p.y === PLOT_BOTTOM)).toBe(true);
    expect(layout?.lossTicks).toEqual([{ pos: PLOT_BOTTOM, label: "0" }]);
  });

  it("also applies when every loss is negative", () => {
    const points = [point(0, -1, 0.1), point(1, -0.5, 0.4)];
    const layout = layoutLossCurve(points, WIDTH, HEIGHT);

    expect(layout?.lossPoints.every((p) => p.y === PLOT_BOTTOM)).toBe(true);
  });

  it("does not affect the independent accuracy (right) axis", () => {
    const points = [point(0, 0, 0.0), point(1, 0, 1.0)];
    const layout = layoutLossCurve(points, WIDTH, HEIGHT);

    expect(layout?.accPoints).toEqual([
      { x: PLOT_LEFT, y: PLOT_BOTTOM },
      { x: PLOT_RIGHT, y: PLOT_TOP },
    ]);
  });
});

describe("layoutLossCurve — degenerate epoch span", () => {
  it("centers all x coordinates when every point shares one epoch value", () => {
    // Not expected from epochSeries (which yields strictly increasing
    // epochs), but guarded against directly regardless.
    const points = [point(3, 1, 0.5), point(3, 0.5, 0.6)];
    const layout = layoutLossCurve(points, WIDTH, HEIGHT);
    const center = (PLOT_LEFT + PLOT_RIGHT) / 2;

    expect(layout?.lossPoints[0]?.x).toBe(center);
    expect(layout?.lossPoints[1]?.x).toBe(center);
    expect(layout?.accPoints[0]?.x).toBe(center);
  });
});
