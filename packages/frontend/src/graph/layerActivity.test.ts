import type { TraceEvent } from "@grackle/shared-types";
import { describe, expect, it } from "vitest";
import {
  type ActivitySegment,
  activityAt,
  buildActivityIndex,
} from "./layerActivity";

/**
 * The golden 34-event `train_step` call shape, transcribed verbatim from
 * `packages/nn/tests/test_traceability.py::_GOLDEN_34` (Phase 11.1/11.H).
 * Drift in either the plan's golden or this module fails this test — that
 * IS the point: layerActivity.ts leans on the golden's structural
 * invariants (no recursion, 3 Linear + 2 ReLU layers) rather than
 * re-deriving them.
 */
const GOLDEN_34: [string, string][] = [
  ["call", "grackle_nn/train.py:train_step"],
  ["call", "grackle_nn/model.py:Sequential.forward"],
  ["call", "grackle_nn/layers.py:Linear.forward"],
  ["return", "grackle_nn/layers.py:Linear.forward"],
  ["call", "grackle_nn/layers.py:ReLU.forward"],
  ["return", "grackle_nn/layers.py:ReLU.forward"],
  ["call", "grackle_nn/layers.py:Linear.forward"],
  ["return", "grackle_nn/layers.py:Linear.forward"],
  ["call", "grackle_nn/layers.py:ReLU.forward"],
  ["return", "grackle_nn/layers.py:ReLU.forward"],
  ["call", "grackle_nn/layers.py:Linear.forward"],
  ["return", "grackle_nn/layers.py:Linear.forward"],
  ["return", "grackle_nn/model.py:Sequential.forward"],
  ["call", "grackle_nn/losses.py:SoftmaxCrossEntropy.forward"],
  ["return", "grackle_nn/losses.py:SoftmaxCrossEntropy.forward"],
  ["call", "grackle_nn/losses.py:SoftmaxCrossEntropy.backward"],
  ["return", "grackle_nn/losses.py:SoftmaxCrossEntropy.backward"],
  ["call", "grackle_nn/model.py:Sequential.backward"],
  ["call", "grackle_nn/layers.py:Linear.backward"],
  ["return", "grackle_nn/layers.py:Linear.backward"],
  ["call", "grackle_nn/layers.py:ReLU.backward"],
  ["return", "grackle_nn/layers.py:ReLU.backward"],
  ["call", "grackle_nn/layers.py:Linear.backward"],
  ["return", "grackle_nn/layers.py:Linear.backward"],
  ["call", "grackle_nn/layers.py:ReLU.backward"],
  ["return", "grackle_nn/layers.py:ReLU.backward"],
  ["call", "grackle_nn/layers.py:Linear.backward"],
  ["return", "grackle_nn/layers.py:Linear.backward"],
  ["return", "grackle_nn/model.py:Sequential.backward"],
  ["call", "grackle_nn/optim.py:SGD.step"],
  ["return", "grackle_nn/optim.py:SGD.step"],
  ["call", "grackle_nn/model.py:Sequential.zero_grad"],
  ["return", "grackle_nn/model.py:Sequential.zero_grad"],
  ["return", "grackle_nn/train.py:train_step"],
];

function toEvents(pairs: readonly [string, string][]): TraceEvent[] {
  return pairs.map(([event, node_id]) => ({
    event,
    node_id,
    ts_ns: 0,
    thread_id: 1,
    frame_depth: 0,
  }));
}

const TOKEN_COUNT = 5; // linear, relu, linear, relu, linear

describe("buildActivityIndex — one train_step (golden 34)", () => {
  it("walks forward tokens 0..4 -> loss -> loss-grad -> backward tokens 4..0 -> update -> reset -> idle", () => {
    const segments = buildActivityIndex(toEvents(GOLDEN_34), TOKEN_COUNT);
    const shape = segments.map((s) => [s.phase, s.activeToken]);
    expect(shape).toEqual([
      ["forward-train", 0],
      ["forward-train", 1],
      ["forward-train", 2],
      ["forward-train", 3],
      ["forward-train", 4],
      ["loss", -1],
      ["loss-grad", -1],
      ["backward", 4],
      ["backward", 3],
      ["backward", 2],
      ["backward", 1],
      ["backward", 0],
      ["update", -1],
      ["reset", -1],
      ["idle", -1],
    ]);
  });

  it("stamps each segment's fromIndex at the triggering call/return event", () => {
    const segments = buildActivityIndex(toEvents(GOLDEN_34), TOKEN_COUNT);
    expect(segments.map((s) => s.fromIndex)).toEqual([
      2, 4, 6, 8, 10, 13, 15, 18, 20, 22, 24, 26, 29, 31, 33,
    ]);
  });
});

describe("buildActivityIndex — evaluate block", () => {
  it("marks forward calls inside evaluate() as forward-eval, not forward-train", () => {
    const events = toEvents([
      ["call", "grackle_nn/train.py:evaluate"],
      ["call", "grackle_nn/model.py:Sequential.forward"],
      ["call", "grackle_nn/layers.py:Linear.forward"],
      ["return", "grackle_nn/layers.py:Linear.forward"],
      ["call", "grackle_nn/layers.py:ReLU.forward"],
      ["return", "grackle_nn/layers.py:ReLU.forward"],
      ["call", "grackle_nn/layers.py:Linear.forward"],
      ["return", "grackle_nn/layers.py:Linear.forward"],
      ["return", "grackle_nn/model.py:Sequential.forward"],
      ["call", "grackle_nn/losses.py:SoftmaxCrossEntropy.forward"],
      ["return", "grackle_nn/losses.py:SoftmaxCrossEntropy.forward"],
      ["call", "grackle_nn/metrics.py:accuracy"], // unmatched — ignored, not an error
      ["return", "grackle_nn/metrics.py:accuracy"],
      ["return", "grackle_nn/train.py:evaluate"],
    ]);
    const segments = buildActivityIndex(events, 3);
    expect(segments.map((s) => [s.phase, s.activeToken])).toEqual([
      ["forward-eval", 0],
      ["forward-eval", 1],
      ["forward-eval", 2],
      ["loss", -1],
    ]);
  });

  it("reverts to forward-train once evaluate() has returned", () => {
    const events = toEvents([
      ["call", "grackle_nn/train.py:evaluate"],
      ["call", "grackle_nn/model.py:Sequential.forward"],
      ["call", "grackle_nn/layers.py:Linear.forward"],
      ["return", "grackle_nn/layers.py:Linear.forward"],
      ["return", "grackle_nn/model.py:Sequential.forward"],
      ["return", "grackle_nn/train.py:evaluate"],
      ["call", "grackle_nn/train.py:train_step"],
      ["call", "grackle_nn/model.py:Sequential.forward"],
      ["call", "grackle_nn/layers.py:Linear.forward"],
      ["return", "grackle_nn/layers.py:Linear.forward"],
    ]);
    const segments = buildActivityIndex(events, 1);
    expect(segments.map((s) => s.phase)).toEqual([
      "forward-eval",
      "forward-train",
    ]);
  });
});

describe("buildActivityIndex — truncated prefix tolerance", () => {
  it("does not throw and stops producing segments when returns are missing", () => {
    // The golden-34 prefix cut off mid-backward, with every closing return
    // dropped from that point on.
    const truncated = toEvents(GOLDEN_34).slice(0, 20); // through the first backward call
    expect(() => buildActivityIndex(truncated, TOKEN_COUNT)).not.toThrow();
    const segments = buildActivityIndex(truncated, TOKEN_COUNT);
    expect(segments[segments.length - 1]).toEqual({
      fromIndex: 18,
      phase: "backward",
      activeToken: 4,
    });
  });
});

describe("activityAt", () => {
  const segments = buildActivityIndex(toEvents(GOLDEN_34), TOKEN_COUNT);

  it("returns idle at playhead 0 (before anything has happened)", () => {
    expect(activityAt(segments, 0)).toEqual({
      fromIndex: -1,
      phase: "idle",
      activeToken: -1,
    });
  });

  it("returns idle for an empty segment index at any playhead", () => {
    expect(activityAt([], 100)).toEqual({
      fromIndex: -1,
      phase: "idle",
      activeToken: -1,
    });
  });

  it("binary-search boundary: fromIndex itself is excluded, fromIndex+1 includes it", () => {
    // First forward segment fires at event index 2.
    expect(activityAt(segments, 2)?.phase).toBe("idle"); // not yet active
    expect(activityAt(segments, 3)?.phase).toBe("forward-train");
    expect(activityAt(segments, 3)?.activeToken).toBe(0);
  });

  it("tracks the segment forward through the whole sequence", () => {
    expect(activityAt(segments, 7)).toEqual({
      fromIndex: 6,
      phase: "forward-train",
      activeToken: 2,
    });
    expect(activityAt(segments, 14)).toEqual({
      fromIndex: 13,
      phase: "loss",
      activeToken: -1,
    });
    expect(activityAt(segments, 19)).toEqual({
      fromIndex: 18,
      phase: "backward",
      activeToken: 4,
    });
    expect(activityAt(segments, 34)).toEqual({
      fromIndex: 33,
      phase: "idle",
      activeToken: -1,
    });
  });

  it("holds the last known segment past the end of the array", () => {
    expect(activityAt(segments, 1000).phase).toBe("idle");
    expect(activityAt(segments, 1000).fromIndex).toBe(33);
  });
});

describe("ActivitySegment type re-export sanity", () => {
  it("is usable as an explicit annotation", () => {
    const seg: ActivitySegment = {
      fromIndex: 0,
      phase: "idle",
      activeToken: -1,
    };
    expect(seg.phase).toBe("idle");
  });
});
