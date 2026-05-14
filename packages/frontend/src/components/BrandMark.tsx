import type { JSX } from "react";

export function BrandMark(): JSX.Element {
  return (
    <span
      style={{
        fontFamily: "var(--font-display)",
        fontWeight: 700,
        fontSize: "var(--text-xl)",
        letterSpacing: "0.04em",
        lineHeight: 1,
        color: "var(--color-accent-bright)",
        textShadow: "0 0 8px var(--color-accent-glow)",
        userSelect: "none",
        WebkitFontSmoothing: "auto",
        MozOsxFontSmoothing: "auto",
      }}
    >
      grackle
    </span>
  );
}
