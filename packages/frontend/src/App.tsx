import type { JSX } from "react";
import { useEffect } from "react";
import { BrandMark } from "./components/BrandMark";
import { ConnectionBadge } from "./components/ConnectionBadge";
import { ThemeToggle } from "./components/ThemeToggle";
import { GraphCanvas } from "./graph/GraphCanvas";
import { useGraphStore } from "./graph/useGraphStore";
import { useGrackleClient } from "./ws/client";

const WS_URL = "ws://127.0.0.1:7878";

export function App(): JSX.Element {
  const connect = useGrackleClient((s) => s.connect);
  const onStaticGraph = useGrackleClient((s) => s.onStaticGraph);
  const setGraph = useGraphStore((s) => s.setGraph);

  useEffect(() => {
    connect(WS_URL);
  }, [connect]);

  useEffect(() => {
    return onStaticGraph(setGraph);
  }, [onStaticGraph, setGraph]);

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

      <main
        style={{
          flex: 1,
          position: "relative",
          backgroundImage:
            "radial-gradient(circle, var(--color-border) 1px, transparent 1px)",
          backgroundSize: "24px 24px",
        }}
      >
        <GraphCanvas />
      </main>
    </div>
  );
}
