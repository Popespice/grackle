import type { TraceEvent } from "@grackle/shared-types";
import { describe, expect, it } from "vitest";
import {
  type ActivityScanState,
  type ActivitySegment,
  activityAt,
  scanActivitySegments,
} from "./layerActivity";

/**
 * The golden 34-event `train_step` call shape, transcribed verbatim from
 * `packages/nn/tests/test_traceability.py::_GOLDEN_34` (Phase 11.1/11.H),
 * with the `frame_depth` each event actually carries — read off
 * `packages/nn/run-a.jsonl`, not invented: `train_step` runs at depth 3,
 * `Sequential.forward`/`backward` and the loss/optimizer calls at 4, and the
 * per-layer calls at 5. Depth is load-bearing now: it is what tells the state
 * machine a frame has exited, including the exception case where no `return`
 * event is ever emitted.
 */
const GOLDEN_34: [string, string, number][] = [
  ["call", "grackle_nn/train.py:train_step", 3],
  ["call", "grackle_nn/model.py:Sequential.forward", 4],
  ["call", "grackle_nn/layers.py:Linear.forward", 5],
  ["return", "grackle_nn/layers.py:Linear.forward", 5],
  ["call", "grackle_nn/layers.py:ReLU.forward", 5],
  ["return", "grackle_nn/layers.py:ReLU.forward", 5],
  ["call", "grackle_nn/layers.py:Linear.forward", 5],
  ["return", "grackle_nn/layers.py:Linear.forward", 5],
  ["call", "grackle_nn/layers.py:ReLU.forward", 5],
  ["return", "grackle_nn/layers.py:ReLU.forward", 5],
  ["call", "grackle_nn/layers.py:Linear.forward", 5],
  ["return", "grackle_nn/layers.py:Linear.forward", 5],
  ["return", "grackle_nn/model.py:Sequential.forward", 4],
  ["call", "grackle_nn/losses.py:SoftmaxCrossEntropy.forward", 4],
  ["return", "grackle_nn/losses.py:SoftmaxCrossEntropy.forward", 4],
  ["call", "grackle_nn/losses.py:SoftmaxCrossEntropy.backward", 4],
  ["return", "grackle_nn/losses.py:SoftmaxCrossEntropy.backward", 4],
  ["call", "grackle_nn/model.py:Sequential.backward", 4],
  ["call", "grackle_nn/layers.py:Linear.backward", 5],
  ["return", "grackle_nn/layers.py:Linear.backward", 5],
  ["call", "grackle_nn/layers.py:ReLU.backward", 5],
  ["return", "grackle_nn/layers.py:ReLU.backward", 5],
  ["call", "grackle_nn/layers.py:Linear.backward", 5],
  ["return", "grackle_nn/layers.py:Linear.backward", 5],
  ["call", "grackle_nn/layers.py:ReLU.backward", 5],
  ["return", "grackle_nn/layers.py:ReLU.backward", 5],
  ["call", "grackle_nn/layers.py:Linear.backward", 5],
  ["return", "grackle_nn/layers.py:Linear.backward", 5],
  ["return", "grackle_nn/model.py:Sequential.backward", 4],
  ["call", "grackle_nn/optim.py:SGD.step", 4],
  ["return", "grackle_nn/optim.py:SGD.step", 4],
  ["call", "grackle_nn/model.py:Sequential.zero_grad", 4],
  ["return", "grackle_nn/model.py:Sequential.zero_grad", 4],
  ["return", "grackle_nn/train.py:train_step", 3],
];

function toEvents(pairs: readonly [string, string, number][]): TraceEvent[] {
  return pairs.map(([event, node_id, frame_depth]) => ({
    event,
    node_id,
    ts_ns: 0,
    thread_id: 1,
    frame_depth,
  }));
}

const TOKEN_COUNT = 5; // linear, relu, linear, relu, linear

const CHUNK = 3;

/**
 * Runs the events through BOTH a single pass and a chunked incremental scan,
 * asserts the two agree, and returns the single-pass result. Every test below
 * therefore exercises the composition the panels actually ship — the
 * incremental scanner accumulated across appends — rather than a one-shot
 * convenience wrapper no production code calls.
 */
function segmentsOf(
  events: readonly TraceEvent[],
  tokenCount: number
): ActivitySegment[] {
  const single = scanActivitySegments(events, tokenCount, 0, undefined);
  let chunked: ActivitySegment[] = [];
  let carry: ActivityScanState | undefined;
  for (let start = 0; start < events.length; start += CHUNK) {
    const end = Math.min(events.length, start + CHUNK);
    const step = scanActivitySegments(
      events.slice(0, end),
      tokenCount,
      start,
      carry
    );
    chunked = chunked.concat(step.items);
    carry = step.carry;
  }
  expect(chunked).toEqual(single.items);
  return [...single.items];
}

/**
 * The discriminating case for keying on depth rather than on a `return`
 * event. `tracer.py`'s PY_UNWIND callback does depth bookkeeping and emits
 * NOTHING, so `evaluate` here never gets a `return`; the `"exception"` event
 * RAISE emits cannot stand in for one either, since RAISE also fires for
 * exceptions that are caught locally. Keyed on a boolean flag, `evaluate`
 * would stay "open" forever and every later training forward would be
 * mislabelled `forward-eval` for the rest of the trace.
 */
const CRASHED = toEvents([
  ["call", "grackle_nn/train.py:evaluate", 3],
  ["call", "grackle_nn/model.py:Sequential.forward", 4],
  ["call", "grackle_nn/layers.py:Linear.forward", 5],
  ["exception", "grackle_nn/layers.py:Linear.forward", 5],
  // No returns at all for Linear.forward / Sequential.forward / evaluate:
  // the exception unwound all three. The next event is the caller resuming.
  ["call", "grackle_nn/train.py:train_step", 3],
  ["call", "grackle_nn/model.py:Sequential.forward", 4],
  ["call", "grackle_nn/layers.py:Linear.forward", 5],
  ["return", "grackle_nn/layers.py:Linear.forward", 5],
]);

describe("scanActivitySegments — one train_step (golden 34)", () => {
  it("walks forward tokens 0..4 -> loss -> loss-grad -> backward tokens 4..0 -> update -> reset -> idle", () => {
    const segments = segmentsOf(toEvents(GOLDEN_34), TOKEN_COUNT);
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
    const segments = segmentsOf(toEvents(GOLDEN_34), TOKEN_COUNT);
    expect(segments.map((s) => s.fromIndex)).toEqual([
      2, 4, 6, 8, 10, 13, 15, 18, 20, 22, 24, 26, 29, 31, 33,
    ]);
  });
});

describe("scanActivitySegments — evaluate block", () => {
  it("marks forward calls inside evaluate() as forward-eval, not forward-train", () => {
    const events = toEvents([
      ["call", "grackle_nn/train.py:evaluate", 3],
      ["call", "grackle_nn/model.py:Sequential.forward", 4],
      ["call", "grackle_nn/layers.py:Linear.forward", 5],
      ["return", "grackle_nn/layers.py:Linear.forward", 5],
      ["call", "grackle_nn/layers.py:ReLU.forward", 5],
      ["return", "grackle_nn/layers.py:ReLU.forward", 5],
      ["call", "grackle_nn/layers.py:Linear.forward", 5],
      ["return", "grackle_nn/layers.py:Linear.forward", 5],
      ["return", "grackle_nn/model.py:Sequential.forward", 4],
      ["call", "grackle_nn/losses.py:SoftmaxCrossEntropy.forward", 4],
      ["return", "grackle_nn/losses.py:SoftmaxCrossEntropy.forward", 4],
      ["call", "grackle_nn/metrics.py:accuracy", 4], // unmatched — ignored, not an error
      ["return", "grackle_nn/metrics.py:accuracy", 4],
      ["return", "grackle_nn/train.py:evaluate", 3],
    ]);
    const segments = segmentsOf(events, 3);
    expect(segments.map((s) => [s.phase, s.activeToken])).toEqual([
      ["forward-eval", 0],
      ["forward-eval", 1],
      ["forward-eval", 2],
      ["loss", -1],
    ]);
  });

  it("reverts to forward-train once evaluate() has returned", () => {
    const events = toEvents([
      ["call", "grackle_nn/train.py:evaluate", 3],
      ["call", "grackle_nn/model.py:Sequential.forward", 4],
      ["call", "grackle_nn/layers.py:Linear.forward", 5],
      ["return", "grackle_nn/layers.py:Linear.forward", 5],
      ["return", "grackle_nn/model.py:Sequential.forward", 4],
      ["return", "grackle_nn/train.py:evaluate", 3],
      ["call", "grackle_nn/train.py:train_step", 3],
      ["call", "grackle_nn/model.py:Sequential.forward", 4],
      ["call", "grackle_nn/layers.py:Linear.forward", 5],
      ["return", "grackle_nn/layers.py:Linear.forward", 5],
    ]);
    const segments = segmentsOf(events, 1);
    expect(segments.map((s) => s.phase)).toEqual([
      "forward-eval",
      "forward-train",
    ]);
  });
});

describe("scanActivitySegments — a frame that exits via an exception", () => {
  it("does not latch forward-eval past the unwound frame", () => {
    const segments = segmentsOf(CRASHED, 1);
    expect(segments.map((s) => [s.phase, s.activeToken])).toEqual([
      ["forward-eval", 0],
      ["forward-train", 0],
    ]);
  });

  it("restarts the forward token counter for the post-crash pass", () => {
    const segments = segmentsOf(CRASHED, 1);
    // Not 1: `Sequential.forward` reopened, so this is the pass's FIRST layer.
    expect(segments[1]?.activeToken).toBe(0);
  });

  it("ignores a depth signal from a different thread", () => {
    const events: TraceEvent[] = [
      {
        event: "call",
        node_id: "grackle_nn/train.py:evaluate",
        ts_ns: 0,
        thread_id: 1,
        frame_depth: 3,
      },
      // A shallow event on ANOTHER thread says nothing about thread 1's stack.
      {
        event: "call",
        node_id: "worker.py:tick",
        ts_ns: 0,
        thread_id: 2,
        frame_depth: 0,
      },
      {
        event: "call",
        node_id: "grackle_nn/model.py:Sequential.forward",
        ts_ns: 0,
        thread_id: 1,
        frame_depth: 4,
      },
      {
        event: "call",
        node_id: "grackle_nn/layers.py:Linear.forward",
        ts_ns: 0,
        thread_id: 1,
        frame_depth: 5,
      },
    ];
    expect(segmentsOf(events, 1)[0]?.phase).toBe("forward-eval");
  });
});

describe("scanActivitySegments — incremental resume", () => {
  it("matches a single full scan for every split point", () => {
    const events = toEvents(GOLDEN_34);
    const full = scanActivitySegments(events, TOKEN_COUNT, 0, undefined).items;
    for (let split = 0; split <= events.length; split++) {
      const first = scanActivitySegments(
        events.slice(0, split),
        TOKEN_COUNT,
        0,
        undefined
      );
      const second = scanActivitySegments(
        events,
        TOKEN_COUNT,
        split,
        first.carry
      );
      expect(first.items.concat(second.items)).toEqual(full);
    }
  });
});

describe("scanActivitySegments — truncated prefix tolerance", () => {
  it("does not throw and stops producing segments when returns are missing", () => {
    // The golden-34 prefix cut off mid-backward, with every closing return
    // dropped from that point on.
    const truncated = toEvents(GOLDEN_34).slice(0, 20); // through the first backward call
    expect(() => segmentsOf(truncated, TOKEN_COUNT)).not.toThrow();
    const segments = segmentsOf(truncated, TOKEN_COUNT);
    expect(segments[segments.length - 1]).toEqual({
      fromIndex: 18,
      phase: "backward",
      activeToken: 4,
    });
  });
});

describe("activityAt", () => {
  const segments = segmentsOf(toEvents(GOLDEN_34), TOKEN_COUNT);

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

  it("boundary: fromIndex itself is INCLUDED (playheadLookup convention)", () => {
    // First forward segment fires at event index 2.
    expect(activityAt(segments, 1)?.phase).toBe("idle"); // not yet
    expect(activityAt(segments, 2)?.phase).toBe("forward-train");
    expect(activityAt(segments, 2)?.activeToken).toBe(0);
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

describe("scanActivitySegments — depth is the ONLY close signal", () => {
  /**
   * `RAISE` fires for exceptions that are caught locally too, and it reports
   * the *body* depth of the frame that raised — one deeper than that frame's
   * own `call`. So an exception event can never be at or below the depth of an
   * enclosing tracked frame, and must not close one. A `try/except` inside a
   * layer is ordinary Python; if a raise anywhere under `Sequential.forward`
   * closed it, the rest of that pass would stop being counted.
   */
  it("a locally-caught exception under an open frame closes nothing", () => {
    const events = toEvents([
      ["call", "grackle_nn/model.py:Sequential.forward", 4],
      ["call", "grackle_nn/layers.py:Linear.forward", 5],
      ["exception", "grackle_nn/layers.py:Linear.forward", 6], // body depth
      ["return", "grackle_nn/layers.py:Linear.forward", 5], // caught, returns
      ["call", "grackle_nn/layers.py:ReLU.forward", 5],
    ]);
    const segments = segmentsOf(events, 2);
    // Still counting: the second layer is token 1, not a restarted token 0.
    expect(segments.map((s) => s.activeToken)).toEqual([0, 1]);
  });

  it("a frame's own return, at its exact call depth, closes it", () => {
    // The boundary the `<=` comparison exists for: `_on_return` decrements
    // BEFORE emitting, so a frame opened by a call at depth 4 returns at 4.
    const events = toEvents([
      ["call", "grackle_nn/train.py:evaluate", 3],
      ["return", "grackle_nn/train.py:evaluate", 3],
      ["call", "grackle_nn/model.py:Sequential.forward", 4],
      ["call", "grackle_nn/layers.py:Linear.forward", 5],
    ]);
    expect(segmentsOf(events, 1)[0]?.phase).toBe("forward-train");
  });

  it("a sibling call at the same depth closes the frame before it", () => {
    // No return event at all between them — the loss call at depth 4 is proof
    // enough that Sequential.forward (also 4) is no longer on the stack.
    const events = toEvents([
      ["call", "grackle_nn/model.py:Sequential.forward", 4],
      ["call", "grackle_nn/layers.py:Linear.forward", 5],
      ["call", "grackle_nn/losses.py:SoftmaxCrossEntropy.forward", 4],
      ["call", "grackle_nn/layers.py:Linear.forward", 5], // orphan: no forward
    ]);
    const segments = segmentsOf(events, 2);
    expect(segments.map((s) => s.phase)).toEqual(["forward-train", "loss"]);
  });

  it("a layer forward outside any Sequential.forward produces no segment", () => {
    const events = toEvents([
      ["call", "grackle_nn/layers.py:Linear.forward", 5],
    ]);
    expect(segmentsOf(events, 3)).toEqual([]);
  });

  it("resumes correctly at every split point across a crash boundary", () => {
    const full = scanActivitySegments(CRASHED, 1, 0, undefined).items;
    for (let split = 0; split <= CRASHED.length; split++) {
      const first = scanActivitySegments(
        CRASHED.slice(0, split),
        1,
        0,
        undefined
      );
      const second = scanActivitySegments(CRASHED, 1, split, first.carry);
      expect(first.items.concat(second.items)).toEqual(full);
    }
  });
});

describe("scanActivitySegments — token mapping", () => {
  it("maps the backward sweep right-to-left for a model ending in an activation", () => {
    // tokens = [linear, relu]: backward runs relu (token 1) then linear (0).
    const events = toEvents([
      ["call", "grackle_nn/model.py:Sequential.backward", 4],
      ["call", "grackle_nn/layers.py:ReLU.backward", 5],
      ["return", "grackle_nn/layers.py:ReLU.backward", 5],
      ["call", "grackle_nn/layers.py:Linear.backward", 5],
    ]);
    expect(segmentsOf(events, 2).map((s) => s.activeToken)).toEqual([1, 0]);
  });

  it("restarts the forward counter on each successive train_step", () => {
    const one: [string, string, number][] = [
      ["call", "grackle_nn/train.py:train_step", 3],
      ["call", "grackle_nn/model.py:Sequential.forward", 4],
      ["call", "grackle_nn/layers.py:Linear.forward", 5],
      ["return", "grackle_nn/layers.py:Linear.forward", 5],
      ["return", "grackle_nn/model.py:Sequential.forward", 4],
      ["return", "grackle_nn/train.py:train_step", 3],
    ];
    const segments = segmentsOf(toEvents([...one, ...one]), 1);
    expect(segments.map((s) => [s.phase, s.activeToken])).toEqual([
      ["forward-train", 0],
      ["idle", -1],
      ["forward-train", 0],
      ["idle", -1],
    ]);
  });
});

/**
 * ─── KNOWN DEFECTS ────────────────────────────────────────────────────────
 *
 * `it.fails` asserts the test throws: each of these encodes a defect that is
 * real today, so the suite stays green while the expectation is on record.
 * Fixing the module makes the corresponding case START failing here — that is
 * the signal to delete the `.fails` and move the test up into the live blocks
 * above. Nothing here is skipped: every one runs on every `pnpm test`.
 */
describe("KNOWN DEFECT — idle is keyed on a return event, not on frame closure", () => {
  /**
   * The module docstring's own argument is that a frame's `return` event may
   * never arrive, which is why every *open* frame is closed by depth. The one
   * transition that still keys on a return event is `train_step`'s — so the
   * two cases below, both ordinary, leave the phase chip asserting a phase
   * that finished long ago.
   *
   * FIX: track `train_step` (and `evaluate`) as `OpenFrame`s like the rest,
   * and push `idle` when `closeIfExited` drops one. That covers the clean
   * return, the unwind, and the evaluate tail with one rule.
   */

  it.fails("returns to idle once evaluate() has returned", () => {
    const events = toEvents([
      ["call", "grackle_nn/train.py:evaluate", 3],
      ["call", "grackle_nn/model.py:Sequential.forward", 4],
      ["call", "grackle_nn/layers.py:Linear.forward", 5],
      ["return", "grackle_nn/layers.py:Linear.forward", 5],
      ["return", "grackle_nn/model.py:Sequential.forward", 4],
      ["return", "grackle_nn/train.py:evaluate", 3],
    ]);
    const segments = segmentsOf(events, 1);
    expect(activityAt(segments, 5).phase).toBe("idle"); // is "forward-eval"
  });

  it.fails("is idle at the epoch boundary a loss-curve click seeks to", () => {
    // `train.fit` runs: train_step*, evaluate, record_epoch, record_layer_stats
    // (packages/nn/src/grackle_nn/train.py:64-83). LossCurvePanel seeks to the
    // record_epoch event index, so this playhead is where EVERY click on the
    // loss curve lands — and where the trace rests at the end of a run.
    // Measured against the real 25,870-event packages/nn/run-a.jsonl: all 60
    // of 60 epoch markers report "loss", the loss call inside evaluate().
    const events = toEvents([
      ["call", "grackle_nn/train.py:train_step", 3],
      ["return", "grackle_nn/train.py:train_step", 3],
      ["call", "grackle_nn/train.py:evaluate", 3],
      ["call", "grackle_nn/losses.py:SoftmaxCrossEntropy.forward", 4],
      ["return", "grackle_nn/losses.py:SoftmaxCrossEntropy.forward", 4],
      ["return", "grackle_nn/train.py:evaluate", 3],
      ["return", "grackle_nn/metrics.py:record_epoch", 3], // <- the seek target
    ]);
    const segments = segmentsOf(events, 1);
    expect(activityAt(segments, 6).phase).toBe("idle"); // is "loss"
  });

  it.fails("returns to idle after train_step exits via an exception", () => {
    // Phase 12.0's incremental `.part` writer makes a killed/crashed run a
    // first-class input, and the module docstring names exactly this shape.
    // The unwound train_step emits no return, so nothing ever restores idle:
    // the chip holds the last phase for the remainder of the trace.
    const events = toEvents([
      ["call", "grackle_nn/train.py:train_step", 3],
      ["call", "grackle_nn/model.py:Sequential.forward", 4],
      ["call", "grackle_nn/layers.py:Linear.forward", 5],
      ["exception", "grackle_nn/layers.py:Linear.forward", 6],
      ["call", "grackle_nn/train.py:fit", 2], // caller resumes, shallower
    ]);
    const segments = segmentsOf(events, 1);
    expect(activityAt(segments, 4).phase).toBe("idle"); // is "forward-train"
  });
});

describe("KNOWN DEFECT — the scan is single-slot, so threads clobber each other", () => {
  /**
   * `closeIfExited` is careful to ignore depths from other threads, which
   * reads as a promise that the walk is thread-safe. It is not: `forward` /
   * `backward` / `evaluate` are single slots, so any thread's
   * `Sequential.forward` overwrites whichever thread's pass was in progress
   * and resets its token counter.
   *
   * Low priority — grackle_nn is single-threaded numpy — but the panel runs
   * over any Python trace, and the half-measure is worse than either whole:
   * the module pays for per-thread bookkeeping and still gets it wrong.
   *
   * FIX: key the three slots (and `forwardCount`/`backwardCount`) by
   * `thread_id`, and drive segments from the thread that owns the event.
   */

  it.fails("another thread's forward does not reset this thread's sweep", () => {
    const events: TraceEvent[] = [
      ["call", "grackle_nn/model.py:Sequential.forward", 4, 1],
      ["call", "grackle_nn/layers.py:Linear.forward", 5, 1], // t1 token 0
      ["return", "grackle_nn/layers.py:Linear.forward", 5, 1],
      ["call", "grackle_nn/model.py:Sequential.forward", 4, 2], // t2 starts
      ["call", "grackle_nn/layers.py:ReLU.forward", 5, 1], // t1 token 1
    ].map(([event, node_id, frame_depth, thread_id]) => ({
      event: event as string,
      node_id: node_id as string,
      ts_ns: 0,
      thread_id: thread_id as number,
      frame_depth: frame_depth as number,
    }));
    const segments = segmentsOf(events, 3);
    expect(segments[segments.length - 1]?.activeToken).toBe(1); // is 0
  });

  it.fails("another thread's train_step return does not force this thread idle", () => {
    const events: TraceEvent[] = [
      ["call", "grackle_nn/model.py:Sequential.forward", 4, 1],
      ["call", "grackle_nn/layers.py:Linear.forward", 5, 1],
      ["return", "grackle_nn/train.py:train_step", 3, 2], // other thread ends
    ].map(([event, node_id, frame_depth, thread_id]) => ({
      event: event as string,
      node_id: node_id as string,
      ts_ns: 0,
      thread_id: thread_id as number,
      frame_depth: frame_depth as number,
    }));
    const segments = segmentsOf(events, 3);
    expect(segments.map((s) => s.phase)).toEqual(["forward-train"]); // gains "idle"
  });
});
