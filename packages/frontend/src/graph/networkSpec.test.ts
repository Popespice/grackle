import type { TraceEvent } from "@grackle/shared-types";
import { describe, expect, it } from "vitest";
import { extractNetworkSpec } from "./networkSpec";

/** Build a `record_architecture`-shaped (or arbitrary) `"return"` event.
 *  `ret` is the raw `values.ret` repr string. */
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

function otherEvent(event: string, node_id: string): TraceEvent {
  return { event, node_id, ts_ns: 0, thread_id: 1, frame_depth: 0 };
}

const GOLDEN_RET = "'linear:2:32 relu linear:32:32 relu linear:32:3'";

describe("extractNetworkSpec", () => {
  it("parses the real golden repr from run-a.jsonl", () => {
    const events = [
      retEvent("grackle_nn/metrics.py:record_architecture", GOLDEN_RET),
    ];
    const spec = extractNetworkSpec(events);
    expect(spec).toEqual({
      tokens: [
        { kind: "linear", inDim: 2, outDim: 32 },
        { kind: "activation", name: "relu" },
        { kind: "linear", inDim: 32, outDim: 32 },
        { kind: "activation", name: "relu" },
        { kind: "linear", inDim: 32, outDim: 3 },
      ],
      columns: [2, 32, 32, 3],
    });
  });

  it("rejects a node_id where metrics.py:record_architecture is not exact-or-slash-preceded", () => {
    const events = [
      retEvent("pkg/mymetrics.py:record_architecture", GOLDEN_RET),
    ];
    expect(extractNetworkSpec(events)).toBeNull();
  });

  it("accepts a bare node_id", () => {
    const events = [retEvent("metrics.py:record_architecture", GOLDEN_RET)];
    expect(extractNetworkSpec(events)?.columns).toEqual([2, 32, 32, 3]);
  });

  it("skips an event with ret_truncated: true", () => {
    const events = [
      retEvent("grackle_nn/metrics.py:record_architecture", GOLDEN_RET, true),
    ];
    expect(extractNetworkSpec(events)).toBeNull();
  });

  it("returns null when the linear chain doesn't connect (corrupt beacon)", () => {
    const events = [
      retEvent(
        "grackle_nn/metrics.py:record_architecture",
        "'linear:2:32 relu linear:64:16'"
      ),
    ];
    expect(extractNetworkSpec(events)).toBeNull();
  });

  it("accepts an unknown non-linear token as an activation glyph", () => {
    const events = [
      retEvent(
        "grackle_nn/metrics.py:record_architecture",
        "'linear:2:8 gelu linear:8:3'"
      ),
    ];
    const spec = extractNetworkSpec(events);
    expect(spec?.tokens[1]).toEqual({ kind: "activation", name: "gelu" });
  });

  it("first match wins when multiple returns exist", () => {
    const events = [
      retEvent("grackle_nn/metrics.py:record_architecture", "'linear:1:1'"),
      retEvent("grackle_nn/metrics.py:record_architecture", GOLDEN_RET),
    ];
    expect(extractNetworkSpec(events)?.columns).toEqual([1, 1]);
  });

  it("ignores unrelated events interleaved before the match", () => {
    const events = [
      otherEvent("call", "grackle_nn/train.py:fit"),
      otherEvent("call", "grackle_nn/metrics.py:record_architecture"),
      retEvent("grackle_nn/metrics.py:record_architecture", GOLDEN_RET),
    ];
    expect(extractNetworkSpec(events)?.columns).toEqual([2, 32, 32, 3]);
  });

  it("returns null for an empty event array", () => {
    expect(extractNetworkSpec([])).toBeNull();
  });

  it("returns null when ret is not a repr-quoted string", () => {
    const events = [
      retEvent("grackle_nn/metrics.py:record_architecture", "linear:2:32"),
    ];
    expect(extractNetworkSpec(events)).toBeNull();
  });

  it("returns null for a non-string ret (e.g. a numeric repr)", () => {
    const events: TraceEvent[] = [
      {
        event: "return",
        node_id: "grackle_nn/metrics.py:record_architecture",
        ts_ns: 0,
        thread_id: 1,
        frame_depth: 0,
        values: {},
      },
    ];
    expect(extractNetworkSpec(events)).toBeNull();
  });
});
