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
  const onTraceSessionStart = useGrackleClient((s) => s.onTraceSessionStart);
  const onTraceEvent = useGrackleClient((s) => s.onTraceEvent);
  const onTraceSessionEnd = useGrackleClient((s) => s.onTraceSessionEnd);
  const setGraph = useGraphStore((s) => s.setGraph);
  const startTraceSession = useGraphStore((s) => s.startTraceSession);
  const addTraceEvent = useGraphStore((s) => s.addTraceEvent);
  const endTraceSession = useGraphStore((s) => s.endTraceSession);

  useEffect(() => {
    connect(WS_URL);
  }, [connect]);

  useEffect(() => {
    return onStaticGraph(setGraph);
  }, [onStaticGraph, setGraph]);

  useEffect(() => {
    return onTraceSessionStart((msg) =>
      startTraceSession(msg.payload.session_id)
    );
  }, [onTraceSessionStart, startTraceSession]);

  useEffect(() => {
    return onTraceEvent(addTraceEvent);
  }, [onTraceEvent, addTraceEvent]);

  useEffect(() => {
    return onTraceSessionEnd(endTraceSession);
  }, [onTraceSessionEnd, endTraceSession]);

  return (
    <div
      style={{
        display: "grid",
        gridTemplateRows: "auto 1fr auto auto",
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
        }}
      >
        <SlotContainer slot="right-sidebar" />
      </div>
      <div style={{ gridColumn: "1 / -1" }}>
        <SlotContainer slot="bottom-dock" />
      </div>
      <div style={{ gridColumn: "1 / -1" }}>
        <SlotContainer slot="bottom-status" />
      </div>
    </div>
  );
}
