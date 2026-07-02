import type { TraceEvent } from "@grackle/shared-types";
import { useCallback, useEffect, useRef, useState } from "react";
import { useGrackleClient } from "../ws/client";
import { type FullTraceResult, fetchFullTrace } from "./fetchFullTrace";
import { useGraphStore } from "./useGraphStore";

export interface UseFullTraceResult {
  /** The full-trace prefix used for stack reconstruction. */
  events: TraceEvent[];
  /** True when a seekable trace exceeded the 50k paging cap. */
  truncated: boolean;
  loading: boolean;
  error: boolean;
  /** True once `events` is a usable prefix (always true in buffered mode). */
  loaded: boolean;
  /** Page the seekable prefix. No-op in buffered mode and after a successful load. */
  load: () => void;
}

/**
 * Module-level promise cache keyed by `sessionId:traceTotal`. Deduplicates
 * concurrent consumers to a single `fetchFullTrace` per session (so two panels
 * sharing the prefix don't double-page) and preserves a loaded prefix across
 * scrubs — a scrub must never re-page. Only seekable sessions are cached;
 * buffered sessions read the live store directly.
 *
 * The `traceTotal` component matters for correctness: a store-loaded session id
 * is a *stable* uuid5 of the trace file's path, so overwriting that file (e.g.
 * re-running `grackle … -o out.jsonl`) and reloading reuses the id with new
 * content. Folding `traceTotal` into the key makes a changed trace a cache miss
 * (a re-run almost always changes the event count) instead of serving a stale
 * prefix; it also re-pages if the total is refined after a first `load()`.
 */
const fullTraceCache = new Map<string, Promise<FullTraceResult>>();

/** Compose the content-aware cache key for a seekable session. */
function cacheKey(sessionId: string, total: number): string {
  return `${sessionId}:${total}`;
}

/** Test-only: reset the module cache between test cases. */
export function _resetFullTraceCache(): void {
  fullTraceCache.clear();
}

/**
 * Supply the full-trace prefix a time-travel panel needs to reconstruct the
 * call stack at the playhead (Phase 10.3).
 *
 * - **Buffered (live) sessions**: the store's `traceEvents` is append-only and
 *   IS the whole trace, so it is returned directly and stays live while
 *   streaming — never cached (a cached snapshot would freeze a growing session).
 * - **Seekable (file-replay) sessions**: lazy. `load()` pages the prefix once
 *   (up to `fetchFullTrace`'s 50k cap) via the seek channel and caches the
 *   promise. Guards: never fetch before `traceTotal` is known; drop a stale
 *   result if the session changed mid-fetch; on failure, evict so a retry can
 *   re-fetch.
 */
export function useFullTrace(): UseFullTraceResult {
  const traceSessionId = useGraphStore((s) => s.traceSessionId);
  const traceSeekable = useGraphStore((s) => s.traceSeekable);
  const traceTotal = useGraphStore((s) => s.traceTotal);
  const storeEvents = useGraphStore((s) => s.traceEvents);
  const requestTraceWindow = useGrackleClient((s) => s.requestTraceWindow);

  const [result, setResult] = useState<FullTraceResult | null>(null);
  const [loading, setLoading] = useState(false);
  const [error, setError] = useState(false);

  // Skip a resolve/reject that lands after unmount — the cached promise
  // outlives this component (module cache), so a fetch settling post-unmount
  // must not setState. `stale()` only catches a session *change*, not unmount.
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  // A new session's prefix is unrelated — reset local state on session change.
  // biome-ignore lint/correctness/useExhaustiveDependencies: keyed on session id only.
  useEffect(() => {
    setResult(null);
    setLoading(false);
    setError(false);
  }, [traceSessionId]);

  const load = useCallback(() => {
    // Buffered mode needs no fetch; guard against a premature fetch before the
    // seek handshake reports the total (traceTotal is 0 until then).
    if (!traceSeekable || traceSessionId === null || traceTotal <= 0) return;
    const sid = traceSessionId;
    const key = cacheKey(sid, traceTotal);
    const stale = () => useGraphStore.getState().traceSessionId !== sid;

    let promise = fullTraceCache.get(key);
    if (!promise) {
      // Bound the cache — a debug tool, not a memory-critical path. Evict the
      // single oldest entry (Map preserves insertion order) rather than
      // clear()-ing all, so one 9th session can't wipe the other 8 and force
      // their consumers to re-page.
      if (fullTraceCache.size > 8) {
        const oldest = fullTraceCache.keys().next().value;
        if (oldest !== undefined) fullTraceCache.delete(oldest);
      }
      promise = fetchFullTrace(requestTraceWindow, sid, traceTotal);
      fullTraceCache.set(key, promise);
    }
    setLoading(true);
    setError(false);
    promise.then(
      (res) => {
        if (!mounted.current || stale()) return;
        setResult(res);
        setLoading(false);
      },
      () => {
        // Evict the failed promise so a later load() re-fetches instead of
        // resolving the same rejection forever.
        fullTraceCache.delete(key);
        if (!mounted.current || stale()) return;
        setError(true);
        setLoading(false);
      }
    );
  }, [traceSeekable, traceSessionId, traceTotal, requestTraceWindow]);

  if (!traceSeekable) {
    return {
      events: storeEvents,
      truncated: false,
      loading: false,
      error: false,
      loaded: true,
      load,
    };
  }

  return {
    events: result?.events ?? [],
    truncated: result?.truncated ?? false,
    loading,
    error,
    loaded: result !== null,
    load,
  };
}
