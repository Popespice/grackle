import { describe, expect, it } from "vitest";
import { matchesBeaconMethod, matchesBeaconNode } from "./beaconNode";

describe("matchesBeaconNode", () => {
  it("matches a bare node_id exactly", () => {
    expect(
      matchesBeaconNode("metrics.py:record_epoch", "metrics.py:record_epoch")
    ).toBe(true);
  });

  it("matches a slash-preceded suffix", () => {
    expect(
      matchesBeaconNode(
        "grackle_nn/metrics.py:record_epoch",
        "metrics.py:record_epoch"
      )
    ).toBe(true);
  });

  it("rejects a filename that merely ENDS WITH the beacon's filename", () => {
    // The whole point of the guard: pkg/mymetrics.py is a different module.
    expect(
      matchesBeaconNode(
        "pkg/mymetrics.py:record_epoch",
        "metrics.py:record_epoch"
      )
    ).toBe(false);
  });

  it("rejects a different qualname in the right file", () => {
    expect(
      matchesBeaconNode(
        "grackle_nn/metrics.py:accuracy",
        "metrics.py:record_epoch"
      )
    ).toBe(false);
  });
});

describe("matchesBeaconMethod", () => {
  it("matches any class in the named file with the named method", () => {
    expect(
      matchesBeaconMethod(
        "grackle_nn/layers.py:Linear.forward",
        "layers.py",
        "forward"
      )
    ).toBe(true);
    expect(
      matchesBeaconMethod(
        "grackle_nn/layers.py:ReLU.forward",
        "layers.py",
        "forward"
      )
    ).toBe(true);
  });

  it("rejects the wrong method", () => {
    expect(
      matchesBeaconMethod(
        "grackle_nn/layers.py:Linear.backward",
        "layers.py",
        "forward"
      )
    ).toBe(false);
  });

  it("rejects the wrong file, including a suffix near-miss", () => {
    expect(
      matchesBeaconMethod(
        "grackle_nn/losses.py:X.forward",
        "layers.py",
        "forward"
      )
    ).toBe(false);
    expect(
      matchesBeaconMethod("pkg/mylayers.py:X.forward", "layers.py", "forward")
    ).toBe(false);
  });

  it("rejects a module-level function (no class qualifier)", () => {
    expect(
      matchesBeaconMethod(
        "grackle_nn/layers.py:forward",
        "layers.py",
        "forward"
      )
    ).toBe(false);
  });

  it("splits on the LAST colon, so a colon in the path cannot confuse it", () => {
    expect(
      matchesBeaconMethod(
        "odd:dir/layers.py:Linear.forward",
        "layers.py",
        "forward"
      )
    ).toBe(true);
  });

  it("rejects a node_id with no colon at all", () => {
    expect(matchesBeaconMethod("layers.py", "layers.py", "forward")).toBe(
      false
    );
  });
});
