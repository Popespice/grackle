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

  it("returns null when the beacon names no param-carrying layer", () => {
    // A spec with no linear tokens has no neuron columns, so layoutNetwork
    // would return null and the panel would paint a blank card instead of
    // taking its "no network beacons" degrade path.
    const events = [
      retEvent("grackle_nn/metrics.py:record_architecture", "'relu relu'"),
    ];
    expect(extractNetworkSpec(events)).toBeNull();
  });

  it("resumes the search from startIndex", () => {
    const events = [
      retEvent("grackle_nn/metrics.py:record_architecture", "'linear:1:1'"),
      retEvent("grackle_nn/metrics.py:record_architecture", GOLDEN_RET),
    ];
    expect(extractNetworkSpec(events, 1)?.columns).toEqual([2, 32, 32, 3]);
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

describe("extractNetworkSpec — adversarial beacon payloads", () => {
  const spec = (ret: string) =>
    extractNetworkSpec([
      retEvent("grackle_nn/metrics.py:record_architecture", ret),
    ]);

  it("tolerates runs of spaces and leading/trailing padding", () => {
    expect(spec("'  linear:2:8   relu  linear:8:3 '")?.columns).toEqual([
      2, 8, 3,
    ]);
  });

  it("treats a tab as part of the token, not as a separator", () => {
    // record_architecture builds the string with `" ".join(...)`; anything
    // else is a foreign payload, and gluing two tokens together must not
    // silently half-parse into a plausible-looking net.
    expect(spec("'linear:2:8\trelu'")).toBeNull();
  });

  it("rejects a token with a trailing newline (JS $ is not Python's $)", () => {
    // Python's re `$` matches before a trailing newline and JS's does not —
    // relying on that difference either way would be a trap, so pin it.
    expect(spec("'linear:2:8\n'")).toBeNull();
  });

  it("is case-sensitive about the linear token", () => {
    // "Linear:2:8" is not the grammar; it degrades to an activation glyph,
    // which leaves no param-carrying layer and so no spec at all.
    expect(spec("'Linear:2:8'")).toBeNull();
  });

  it("keeps a three-part token as an activation rather than half-parsing it", () => {
    const parsed = spec("'linear:2:8 linear:8:3:1 linear:8:3'");
    expect(parsed?.tokens[1]).toEqual({
      kind: "activation",
      name: "linear:8:3:1",
    });
    expect(parsed?.columns).toEqual([2, 8, 3]);
  });

  it("accepts a double-quoted repr (Python switches quotes on demand)", () => {
    expect(spec('"linear:2:8"')?.columns).toEqual([2, 8]);
  });

  it("rejects mismatched quotes", () => {
    expect(spec("'linear:2:8\"")).toBeNull();
  });

  it("rejects an empty repr", () => {
    expect(spec("''")).toBeNull();
  });

  it("parses leading zeros as decimal, not octal", () => {
    expect(spec("'linear:08:010'")?.columns).toEqual([8, 10]);
  });

  it("carries a dimension too large for the 64-neuron cap intact", () => {
    // The cap is a LAYOUT concern; the spec must report the real width so the
    // caption can say "64 of 4096 shown".
    expect(spec("'linear:4096:10'")?.columns).toEqual([4096, 10]);
  });

  it("skips a truncated beacon and uses a later intact one", () => {
    const events = [
      retEvent(
        "grackle_nn/metrics.py:record_architecture",
        "'linear:9:9'",
        true
      ),
      retEvent("grackle_nn/metrics.py:record_architecture", GOLDEN_RET),
    ];
    expect(extractNetworkSpec(events)?.columns).toEqual([2, 32, 32, 3]);
  });

  it("does NOT look past a beacon that parsed but did not chain", () => {
    // Deliberate asymmetry with the truncated case above: an unparseable
    // payload is skipped, a parsed-but-incoherent one is fatal. A trace with
    // two different architectures is not something this parser reconciles.
    const events = [
      retEvent(
        "grackle_nn/metrics.py:record_architecture",
        "'linear:2:32 linear:64:16'"
      ),
      retEvent("grackle_nn/metrics.py:record_architecture", GOLDEN_RET),
    ];
    expect(extractNetworkSpec(events)).toBeNull();
  });

  it("ignores a matching node_id on a call event (only returns carry values)", () => {
    const events: TraceEvent[] = [
      {
        event: "call",
        node_id: "grackle_nn/metrics.py:record_architecture",
        ts_ns: 0,
        thread_id: 1,
        frame_depth: 0,
        values: { ret: GOLDEN_RET },
      },
    ];
    expect(extractNetworkSpec(events)).toBeNull();
  });
});
