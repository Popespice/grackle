/**
 * Map a normalised heat value [0..1] to a hex colour string.
 *
 * All colours are hex constants — NEVER oklch/hsl/CSS-var — because
 * Sigma 3.x parseColor() only accepts `#rrggbb` and `rgb()/rgba()`.
 * Any other format silently falls through to opaque black (r:0,g:0,b:0,a:1).
 * See ADR-0015 and the Sigma colour-parsing critical finding.
 *
 * Ramp: cold (deep blue) → warm (bright orange-red), 7 stops.
 */

// Bucketed ramp — cold to hot, all hex.
const RAMP: readonly string[] = [
  "#1e3a5f", // 0.00 – 0.14  deep-blue (coldest)
  "#1d6fa4", // 0.14 – 0.28  medium-blue
  "#2196a8", // 0.28 – 0.43  teal
  "#29a669", // 0.43 – 0.57  green
  "#e8c234", // 0.57 – 0.71  amber
  "#e86b20", // 0.71 – 0.86  orange
  "#e8221e", // 0.86 – 1.00  red (hottest)
];

/**
 * Convert a normalised heat value to a hex colour.
 *
 * @param norm Value in [0, 1] (values outside the range are clamped).
 * @returns `"#rrggbb"` hex string.
 */
export function heatColor(norm: number): string {
  const clamped = Math.max(0, Math.min(1, norm));
  const idx = Math.min(RAMP.length - 1, Math.floor(clamped * RAMP.length));
  // idx is clamped to [0, RAMP.length-1], so this access is always defined.
  // biome-ignore lint/style/noNonNullAssertion: index is clamped above
  return RAMP[idx]!;
}

/** The desaturated hex used for cold (untouched) nodes while heat is active. */
export const COLD_HEX = "#4a5568";
