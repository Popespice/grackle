import type { JSX } from "react";
import { useEffect } from "react";
import { ConnectionBadge } from "./components/ConnectionBadge";
import { ThemeToggle } from "./components/ThemeToggle";
import { useGrackleClient } from "./ws/client";

const WS_URL = "ws://127.0.0.1:7878";

export function App(): JSX.Element {
  const connect = useGrackleClient((s) => s.connect);

  useEffect(() => {
    connect(WS_URL);
  }, [connect]);

  return (
    <div
      style={{
        display: "flex",
        flexDirection: "column",
        minHeight: "100dvh",
      }}
    >
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
        <ConnectionBadge />
        <ThemeToggle />
      </header>

      <main
        style={{
          flex: 1,
          backgroundImage:
            "radial-gradient(circle, var(--color-border) 1px, transparent 1px)",
          backgroundSize: "24px 24px",
        }}
      />
    </div>
  );
}
