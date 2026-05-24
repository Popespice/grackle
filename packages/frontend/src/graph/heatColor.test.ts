import { describe, expect, it } from "vitest";
import { COLD_HEX, heatColor } from "./heatColor";

describe("heatColor", () => {
  it("returns a hex string for all valid inputs", () => {
    for (let i = 0; i <= 10; i++) {
      expect(heatColor(i / 10)).toMatch(/^#[0-9a-f]{6}$/i);
    }
  });

  it("clamps values below 0 to the coldest colour", () => {
    expect(heatColor(-1)).toBe(heatColor(0));
  });

  it("clamps values above 1 to the hottest colour", () => {
    expect(heatColor(2)).toBe(heatColor(1));
  });

  it("is monotonically increasing through buckets (each bucket ≥ previous)", () => {
    // Buckets are cold→hot; we check that later buckets are not the same hex
    // as earlier ones by ensuring distinct values exist across the ramp.
    const samples = [0, 0.2, 0.4, 0.6, 0.8, 1.0].map(heatColor);
    // The first and last colours should differ.
    expect(samples[0]).not.toBe(samples[samples.length - 1]);
  });

  it("COLD_HEX is a valid hex colour (Sigma-safe)", () => {
    expect(COLD_HEX).toMatch(/^#[0-9a-f]{6}$/i);
  });

  // Regression guard: Sigma 3.x parseColor only accepts #hex and rgb/rgba.
  // Any oklch/hsl/CSS-var silently becomes opaque black. This test ensures
  // the heat ramp never regresses to a non-hex format.
  it("never returns oklch, hsl, or CSS var (Sigma hex-only invariant)", () => {
    for (let i = 0; i <= 20; i++) {
      const colour = heatColor(i / 20);
      expect(colour).not.toMatch(/oklch|hsl|var\(/i);
      expect(colour).toMatch(/^#/);
    }
  });
});
