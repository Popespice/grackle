import type { JSX } from "react";
import { useCallback, useEffect, useMemo, useRef } from "react";
import { useGraphStore } from "../graph/useGraphStore";
import { useTracePlayback } from "../graph/useTracePlayback";
import { useGrackleClient } from "../ws/client";

// ----- Shared token-based styles -----
const PANEL_STYLE: React.CSSProperties = {
  display: "flex",
  flexDirection: "column",
  gap: "var(--space-2)",
  padding: "var(--space-3) var(--space-4)",
  borderTop: "1px solid var(--color-border)",
  background: "var(--color-surface-2)",
  fontFamily: "var(--font-sans)",
  fontSize: "var(--text-xs)",
  color: "var(--color-text-muted)",
};

const ROW_STYLE: React.CSSProperties = {
  display: "flex",
  alignItems: "center",
  gap: "var(--space-3)",
  flexWrap: "wrap",
};

const CHIP_BASE: React.CSSProperties = {
  display: "inline-flex",
  alignItems: "center",
  padding: "1px var(--space-2)",
  borderRadius: "var(--radius-full)",
  border: "1px solid var(--color-border)",
  cursor: "pointer",
  fontFamily: "var(--font-mono)",
  fontSize: "var(--text-xs)",
  userSelect: "none",
};

/**
 * TimelinePanel — renders trace playback controls for Phase 6.3.
 *
 * ADR-0007 compliance: ALL hooks are called before the early `return null`.
 * The `useTracePlayback` hook mounts the rAF loop; it is safe to mount even
 * when the session is not yet active because the loop only runs while
 * `tracePlaying` is true.
 */
export function TimelinePanel(): JSX.Element | null {
  // ── ALL HOOKS FIRST (ADR-0007) ──────────────────────────────────────────
  useTracePlayback();

  const traceSessionId = useGraphStore((s) => s.traceSessionId);
  const traceEvents = useGraphStore((s) => s.traceEvents);
  const tracePlayhead = useGraphStore((s) => s.tracePlayhead);
  const tracePlaying = useGraphStore((s) => s.tracePlaying);
  const tracePlaybackSpeed = useGraphStore((s) => s.tracePlaybackSpeed);
  const traceEventTypeFilter = useGraphStore((s) => s.traceEventTypeFilter);
  const traceHeatMode = useGraphStore((s) => s.traceHeatMode);
  const traceWindowSize = useGraphStore((s) => s.traceWindowSize);
  const traceSeekable = useGraphStore((s) => s.traceSeekable);
  const traceTotal = useGraphStore((s) => s.traceTotal);

  const setPlayhead = useGraphStore((s) => s.setPlayhead);
  const play = useGraphStore((s) => s.play);
  const pause = useGraphStore((s) => s.pause);
  const setSpeed = useGraphStore((s) => s.setSpeed);
  const toggleEventType = useGraphStore((s) => s.toggleEventType);
  const setHeatMode = useGraphStore((s) => s.setHeatMode);
  const setWindowSize = useGraphStore((s) => s.setWindowSize);
  const setTraceWindow = useGraphStore((s) => s.setTraceWindow);

  const requestTraceWindow = useGrackleClient((s) => s.requestTraceWindow);

  // Debounced seek: when the scrubber changes in seekable mode, fire a
  // trace_seek_request after 150 ms of idle time.  The ref holds the pending
  // timer so we can cancel it on the next scrub or unmount.
  const seekTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null);

  const handleSeekablePlayheadChange = useCallback(
    (position: number) => {
      setPlayhead(position);
      if (!traceSeekable || traceSessionId === null) return;
      if (seekTimerRef.current !== null) clearTimeout(seekTimerRef.current);
      seekTimerRef.current = setTimeout(() => {
        seekTimerRef.current = null;
        const halfWindow = Math.floor(traceWindowSize / 2);
        const start = Math.max(0, position - halfWindow);
        requestTraceWindow(traceSessionId, start, traceWindowSize)
          .then((msg) => {
            setTraceWindow(
              msg.payload.start_index,
              msg.payload.events,
              msg.payload.total
            );
          })
          .catch(() => {
            // Seek error is non-fatal — the buffered stream is still active.
          });
      }, 150);
    },
    [
      setPlayhead,
      traceSeekable,
      traceSessionId,
      traceWindowSize,
      requestTraceWindow,
      setTraceWindow,
    ]
  );

  // On session start with seekable=true, auto-fetch the initial window to
  // populate traceTotal for scrubber sizing.
  useEffect(() => {
    if (!traceSeekable || traceSessionId === null) return;
    requestTraceWindow(traceSessionId, 0, traceWindowSize)
      .then((msg) => {
        setTraceWindow(
          msg.payload.start_index,
          msg.payload.events,
          msg.payload.total
        );
      })
      .catch(() => {
        // Seek error on initial load — non-fatal, buffered stream still active.
      });
  }, [
    traceSeekable,
    traceSessionId,
    traceWindowSize,
    requestTraceWindow,
    setTraceWindow,
  ]);

  // Cancel any pending debounce timer on unmount.
  useEffect(() => {
    return () => {
      if (seekTimerRef.current !== null) clearTimeout(seekTimerRef.current);
    };
  }, []);

  // Distinct event kinds present in the full session (for filter chips).
  const eventKinds = useMemo(
    () => [...new Set(traceEvents.map((e) => e.event))].sort(),
    [traceEvents]
  );

  // ── EARLY RETURN (after all hooks) ──────────────────────────────────────
  if (traceSessionId === null) return null;

  // In seekable mode the scrubber represents the *full* trace; traceTotal is
  // known after the first seek response.  In non-seekable mode use the buffered
  // event count (may grow during live streaming).
  const total = traceSeekable ? traceTotal : traceEvents.length;
  const isAtEnd = tracePlayhead >= total && total > 0;

  return (
    <section aria-label="Trace timeline" style={PANEL_STYLE}>
      {/* Row 1: scrubber + play/pause + speed */}
      <div style={ROW_STYLE}>
        <span style={{ color: "var(--color-text-subtle)", flexShrink: 0 }}>
          Timeline
        </span>

        <input
          type="range"
          aria-label="Playback position"
          aria-valuetext={`event ${tracePlayhead} of ${total}`}
          min={0}
          max={total}
          value={tracePlayhead}
          step={1}
          style={{ flex: 1, minWidth: 80 }}
          onChange={(e) => {
            const pos = Number(e.target.value);
            if (traceSeekable) {
              handleSeekablePlayheadChange(pos);
            } else {
              setPlayhead(pos);
            }
          }}
        />

        <span
          style={{
            color: "var(--color-text-subtle)",
            fontFamily: "var(--font-mono)",
            flexShrink: 0,
          }}
        >
          {tracePlayhead}/{total}
        </span>

        <button
          type="button"
          aria-pressed={tracePlaying}
          aria-label={tracePlaying ? "Pause" : isAtEnd ? "Replay" : "Play"}
          onClick={() => (tracePlaying ? pause() : play())}
          style={{
            flexShrink: 0,
            padding: "2px var(--space-3)",
            borderRadius: "var(--radius-sm)",
            border: "1px solid var(--color-border)",
            background: tracePlaying
              ? "var(--color-accent)"
              : "var(--color-surface)",
            color: tracePlaying ? "white" : "var(--color-text)",
            cursor: "pointer",
            fontFamily: "var(--font-sans)",
            fontSize: "var(--text-xs)",
          }}
        >
          {tracePlaying ? "⏸ Pause" : isAtEnd ? "↺ Replay" : "▶ Play"}
        </button>

        <label
          style={{
            display: "flex",
            alignItems: "center",
            gap: "var(--space-1)",
            flexShrink: 0,
          }}
        >
          <span style={{ color: "var(--color-text-subtle)" }}>Speed</span>
          <select
            aria-label="Playback speed"
            value={tracePlaybackSpeed}
            onChange={(e) => setSpeed(Number(e.target.value))}
            style={{
              background: "var(--color-surface)",
              border: "1px solid var(--color-border)",
              borderRadius: "var(--radius-sm)",
              color: "var(--color-text)",
              fontFamily: "var(--font-sans)",
              fontSize: "var(--text-xs)",
              padding: "1px var(--space-1)",
            }}
          >
            <option value={1}>1×</option>
            <option value={2}>2×</option>
            <option value={4}>4×</option>
          </select>
        </label>
      </div>

      {/* Row 2: event-type filter chips + heat mode toggle */}
      {(eventKinds.length > 0 || true) && (
        <div style={ROW_STYLE}>
          <span
            style={{
              color: "var(--color-text-subtle)",
              flexShrink: 0,
            }}
          >
            Filter:
          </span>
          {eventKinds.map((kind) => {
            const active = traceEventTypeFilter.has(kind);
            return (
              <label
                key={kind}
                style={{
                  ...CHIP_BASE,
                  background: active
                    ? "var(--color-accent)"
                    : "var(--color-surface)",
                  color: active ? "white" : "var(--color-text-muted)",
                  borderColor: active
                    ? "var(--color-accent)"
                    : "var(--color-border)",
                  cursor: "pointer",
                }}
              >
                <input
                  type="checkbox"
                  checked={active}
                  onChange={() => toggleEventType(kind)}
                  style={{
                    position: "absolute",
                    opacity: 0,
                    width: 0,
                    height: 0,
                  }}
                />
                {kind}
              </label>
            );
          })}

          <span
            style={{
              color: "var(--color-text-subtle)",
              marginLeft: "var(--space-2)",
              flexShrink: 0,
            }}
          >
            Heat:
          </span>
          <button
            type="button"
            aria-pressed={traceHeatMode === "cumulative"}
            aria-label="Cumulative heat mode"
            onClick={() => setHeatMode("cumulative")}
            style={{
              ...CHIP_BASE,
              background:
                traceHeatMode === "cumulative"
                  ? "var(--color-accent)"
                  : "var(--color-surface)",
              color:
                traceHeatMode === "cumulative"
                  ? "white"
                  : "var(--color-text-muted)",
              borderColor:
                traceHeatMode === "cumulative"
                  ? "var(--color-accent)"
                  : "var(--color-border)",
            }}
          >
            Cumulative
          </button>
          <button
            type="button"
            aria-pressed={traceHeatMode === "sliding"}
            aria-label="Sliding window heat mode"
            onClick={() => setHeatMode("sliding")}
            style={{
              ...CHIP_BASE,
              background:
                traceHeatMode === "sliding"
                  ? "var(--color-accent)"
                  : "var(--color-surface)",
              color:
                traceHeatMode === "sliding"
                  ? "white"
                  : "var(--color-text-muted)",
              borderColor:
                traceHeatMode === "sliding"
                  ? "var(--color-accent)"
                  : "var(--color-border)",
            }}
          >
            Sliding
          </button>

          {traceHeatMode === "sliding" && (
            <label
              style={{
                display: "flex",
                alignItems: "center",
                gap: "var(--space-1)",
                flexShrink: 0,
              }}
            >
              <span style={{ color: "var(--color-text-subtle)" }}>Window</span>
              <input
                type="number"
                aria-label="Sliding window size"
                min={10}
                max={10000}
                value={traceWindowSize}
                onChange={(e) =>
                  setWindowSize(
                    Math.max(10, Math.min(10000, Number(e.target.value)))
                  )
                }
                style={{
                  width: 64,
                  background: "var(--color-surface)",
                  border: "1px solid var(--color-border)",
                  borderRadius: "var(--radius-sm)",
                  color: "var(--color-text)",
                  fontFamily: "var(--font-mono)",
                  fontSize: "var(--text-xs)",
                  padding: "1px var(--space-1)",
                }}
              />
            </label>
          )}
        </div>
      )}
    </section>
  );
}
