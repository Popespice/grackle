import type { JSX } from "react";
import { BrandMark } from "../components/BrandMark";
import { ConnectionBadge } from "../components/ConnectionBadge";
import { ThemeToggle } from "../components/ThemeToggle";

export function HeaderChrome(): JSX.Element {
  return (
    <header
      style={{
        display: "flex",
        alignItems: "center",
        justifyContent: "space-between",
        padding: "var(--space-3) var(--space-4)",
        borderBottom: "1px solid var(--color-border)",
        background: "var(--color-surface)",
      }}
    >
      <div
        style={{
          display: "flex",
          alignItems: "center",
          gap: "var(--space-3)",
        }}
      >
        <BrandMark />
        <ConnectionBadge />
      </div>
      <ThemeToggle />
    </header>
  );
}
