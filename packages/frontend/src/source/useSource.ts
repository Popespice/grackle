import type {
  ReadSourceError,
  ReadSourceResponse,
} from "@grackle/shared-types";
import { useEffect, useState } from "react";
import { useGrackleClient } from "../ws/client";

type SourceReply = ReadSourceResponse | ReadSourceError;

export type SourceState =
  | { status: "idle" }
  | { status: "loading" }
  | { status: "loaded"; path: string; source: string }
  | { status: "error"; path: string; reason: string };

// Path-keyed cache — simple in-memory store; cleared on page reload.
const cache = new Map<string, SourceState>();

export function useSource(path: string | null): SourceState {
  const sendReadSource = useGrackleClient((s) => s.sendReadSource);
  const [state, setState] = useState<SourceState>(() => {
    if (path === null) return { status: "idle" };
    return cache.get(path) ?? { status: "loading" };
  });

  useEffect(() => {
    if (path === null) {
      setState({ status: "idle" });
      return;
    }

    const cached = cache.get(path);
    if (cached) {
      setState(cached);
      return;
    }

    setState({ status: "loading" });

    let cancelled = false;
    sendReadSource(path)
      .then((reply: SourceReply) => {
        if (cancelled) return;
        let next: SourceState;
        if (reply.type === "source_response") {
          const r = reply as ReadSourceResponse;
          next = {
            status: "loaded",
            path: r.payload.path,
            source: r.payload.source,
          };
        } else {
          const e = reply as ReadSourceError;
          next = {
            status: "error",
            path: e.payload.path,
            reason: e.payload.reason,
          };
        }
        cache.set(path, next);
        setState(next);
      })
      .catch((err: unknown) => {
        if (cancelled) return;
        const reason = err instanceof Error ? err.message : "unknown error";
        const next: SourceState = { status: "error", path, reason };
        setState(next);
      });

    return () => {
      cancelled = true;
    };
  }, [path, sendReadSource]);

  return state;
}

// Reset cache for testing
export function _resetSourceCacheForTest(): void {
  cache.clear();
}
