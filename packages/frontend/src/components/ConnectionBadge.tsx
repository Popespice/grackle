import type { JSX } from "react";
import type { ConnectionStatus } from "../ws/client";
import { useGrackleClient } from "../ws/client";

const STATUS_LABEL: Record<ConnectionStatus, string> = {
  disconnected: "agent disconnected",
  connecting: "connecting…",
  connected: "agent connected",
};

export function ConnectionBadge(): JSX.Element {
  const status = useGrackleClient((s) => s.status);

  return (
    <div
      className="connection-badge"
      data-status={status}
      style={{
        display: "inline-flex",
        alignItems: "center",
        gap: "var(--space-2)",
        padding: "var(--space-1) var(--space-3)",
        borderRadius: "var(--radius-full)",
        background: "var(--color-surface)",
        border: "1px solid var(--color-border)",
        fontSize: "var(--text-sm)",
        color: "var(--color-text-muted)",
      }}
    >
      <StatusDot status={status} />
      <span>{STATUS_LABEL[status]}</span>
    </div>
  );
}

function StatusDot({ status }: { status: ConnectionStatus }): JSX.Element {
  const color =
    status === "connected"
      ? "var(--color-success)"
      : status === "connecting"
        ? "var(--color-warning)"
        : "var(--color-error)";

  return (
    <span
      data-testid="status-dot"
      style={{
        width: 8,
        height: 8,
        borderRadius: "var(--radius-full)",
        background: color,
        flexShrink: 0,
        animation:
          status === "connected"
            ? "badge-pulse 2s ease-in-out infinite"
            : "none",
      }}
    >
      <style>{`
        @keyframes badge-pulse {
          0%, 100% { opacity: 1; box-shadow: 0 0 0 0 var(--color-accent-glow); }
          50% { opacity: 0.85; box-shadow: 0 0 0 4px transparent; }
        }
      `}</style>
    </span>
  );
}
