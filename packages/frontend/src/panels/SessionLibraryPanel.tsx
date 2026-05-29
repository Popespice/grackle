import type { SessionMeta } from "@grackle/shared-types";
import type React from "react";
import { useCallback, useEffect, useState } from "react";
import { useGrackleClient } from "../ws/client";

export function SessionLibraryPanel(): React.ReactElement | null {
  const status = useGrackleClient((s) => s.status);
  const requestSessionList = useGrackleClient((s) => s.requestSessionList);
  const sendSessionLoad = useGrackleClient((s) => s.sendSessionLoad);
  const [sessions, setSessions] = useState<SessionMeta[]>([]);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const refresh = useCallback(() => {
    if (status !== "connected") return;
    setLoading(true);
    setError(null);
    requestSessionList()
      .then((resp) => setSessions(resp.payload.sessions))
      .catch((e: unknown) => setError(String(e)))
      .finally(() => setLoading(false));
  }, [status, requestSessionList]);

  useEffect(() => {
    if (status === "connected") refresh();
  }, [status, refresh]);

  if (sessions.length === 0 && !loading) {
    return (
      <div
        style={{
          padding: "1rem",
          color: "var(--color-muted, #888)",
          fontSize: "0.85rem",
        }}
      >
        {error
          ? `Error: ${error}`
          : "No stored sessions. Start the server with --store to save sessions."}
      </div>
    );
  }

  return (
    <div style={{ padding: "0.5rem", overflowY: "auto" }}>
      <div
        style={{
          display: "flex",
          justifyContent: "space-between",
          alignItems: "center",
          marginBottom: "0.5rem",
        }}
      >
        <span style={{ fontSize: "0.8rem", color: "var(--color-muted, #888)" }}>
          {loading
            ? "Loading…"
            : `${sessions.length} session${sessions.length !== 1 ? "s" : ""}`}
        </span>
        <button
          type="button"
          onClick={refresh}
          style={{
            fontSize: "0.75rem",
            padding: "0.2rem 0.5rem",
            cursor: "pointer",
          }}
        >
          Refresh
        </button>
      </div>
      <ul style={{ listStyle: "none", margin: 0, padding: 0 }}>
        {sessions.map((s) => (
          <li key={s.id} style={{ marginBottom: "0.25rem" }}>
            <button
              type="button"
              onClick={() => sendSessionLoad(s.id)}
              style={{
                display: "block",
                width: "100%",
                textAlign: "left",
                padding: "0.4rem 0.5rem",
                borderRadius: "4px",
                border: "none",
                background: "var(--color-surface, #1a1a1a)",
                cursor: "pointer",
                fontSize: "0.8rem",
              }}
            >
              <div style={{ fontWeight: 500 }}>{s.label}</div>
              <div
                style={{
                  color: "var(--color-muted, #888)",
                  fontSize: "0.75rem",
                }}
              >
                {s.event_count.toLocaleString()} events &middot; {s.language}
              </div>
            </button>
          </li>
        ))}
      </ul>
    </div>
  );
}
