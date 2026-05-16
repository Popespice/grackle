import "./panels/init";
import type { JSX } from "react";
import { useEffect } from "react";
import { useGraphStore } from "./graph/useGraphStore";
import { SlotContainer } from "./panels/SlotContainer";
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
        display: "grid",
        gridTemplateRows: "auto 1fr auto",
        gridTemplateColumns: "auto 1fr auto",
        height: "100dvh",
      }}
    >
      <div style={{ gridColumn: "1 / -1" }}>
        <SlotContainer slot="top-bar" />
      </div>
      <div>
        <SlotContainer slot="left-sidebar" />
      </div>
      <main
        style={{
          position: "relative",
          overflow: "hidden",
          backgroundImage:
            "radial-gradient(circle, var(--color-border) 1px, transparent 1px)",
          backgroundSize: "24px 24px",
        }}
      >
        <SlotContainer slot="floating-overlay" />
      </main>
      <div
        style={{
          display: "flex",
          flexDirection: "column",
          overflowY: "auto",
          borderLeft: "1px solid var(--color-border)",
        }}
      >
        <SlotContainer slot="right-sidebar" />
      </div>
      <div style={{ gridColumn: "1 / -1" }}>
        <SlotContainer slot="bottom-status" />
      </div>
    </div>
  );
}
