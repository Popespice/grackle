import { useEffect, useRef } from "react";
import { useGraphStore } from "./useGraphStore";

/** Approximate target events per millisecond at 1× speed. */
const EVENTS_PER_MS = 0.05; // 50 events/s at 1×

/**
 * Side-effect-only rAF loop that advances the trace playhead while playing.
 *
 * **Mounting:** call this once inside TimelinePanel. It creates and tears down
 * the animation loop for the lifetime of that component.
 *
 * **StrictMode safety:**
 * - The rAF id is stored in a ref so the cleanup in effect teardown always
 *   cancels the correct frame, even when React double-invokes the effect in dev.
 * - Live state is read via `useGraphStore.getState()` inside the rAF callback
 *   so the loop never closes over stale values.
 * - The effect depends only on `[tracePlaying]` — it is not restarted on every
 *   playhead advance, avoiding drift from effect re-scheduling.
 *
 * **Seekable mode:** in seekable mode the playhead is an absolute index
 * (0..traceTotal).  The playback loop uses ``traceTotal`` as the stop bound so
 * it advances through the full trace rather than stopping at the end of the
 * current in-memory window.  The window itself is updated by scrubber seek
 * requests — playback and windowing are independent concerns.
 *
 * **jsdom guard:** `requestAnimationFrame` is not available in jsdom; tests that
 * exercise the loop should `vi.stubGlobal("requestAnimationFrame", ...)`.
 */
export function useTracePlayback(): void {
  const tracePlaying = useGraphStore((s) => s.tracePlaying);
  const rafRef = useRef<number | null>(null);
  const lastTimeRef = useRef<number | null>(null);

  useEffect(() => {
    if (typeof requestAnimationFrame !== "function") return;
    if (!tracePlaying) return;

    lastTimeRef.current = null;

    const tick = (timestamp: number) => {
      const {
        tracePlaying: stillPlaying,
        tracePlaybackSpeed,
        traceEvents,
        tracePlayhead,
        traceSeekable,
        traceTotal,
        setPlayhead,
        pause,
      } = useGraphStore.getState();

      if (!stillPlaying) return;

      const last = lastTimeRef.current ?? timestamp;
      const deltaMs = timestamp - last;
      lastTimeRef.current = timestamp;

      const advance = Math.max(
        1,
        Math.round(deltaMs * EVENTS_PER_MS * tracePlaybackSpeed)
      );
      const next = tracePlayhead + advance;

      // In seekable mode the playhead is absolute (0..traceTotal); use the
      // full-trace total as the stop condition.  In buffered mode stop at the
      // end of the in-memory window.
      const bound = traceSeekable ? traceTotal : traceEvents.length;

      if (next >= bound) {
        setPlayhead(bound);
        pause();
        return;
      }

      // Use the internal setState to avoid triggering a re-render on every frame.
      // setPlayhead sets tracePlaying:false which would stop the loop; instead
      // we update the store directly here.
      useGraphStore.setState({ tracePlayhead: next });
      rafRef.current = requestAnimationFrame(tick);
    };

    rafRef.current = requestAnimationFrame(tick);
    return () => {
      if (rafRef.current !== null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [tracePlaying]);
}
