import type { TraceEvent, TraceWindowMessage } from "@grackle/shared-types";

/** The seekable-window request function (matches `client.ts`'s
 *  `requestTraceWindow(sessionId, startIndex, count)`). */
export type RequestTraceWindow = (
  sessionId: string,
  startIndex: number,
  count: number
) => Promise<TraceWindowMessage>;

export interface FetchFullTraceOptions {
  /** Events per request. Defaults to 1000 — the server's `_MAX_SEEK_COUNT`. */
  chunk?: number;
  /** Hard ceiling on events fetched, so a multi-million-event file does not
   *  flood the browser. Defaults to 50 000. */
  cap?: number;
}

export interface FullTraceResult {
  events: TraceEvent[];
  /** True when the trace exceeded `cap` and only the prefix was fetched. */
  truncated: boolean;
}

/**
 * Page the entire trace of a seekable session into memory for whole-run flame
 * reconstruction (Phase 8.2). Reuses the existing `trace_seek_request` channel
 * (`requestTraceWindow`) — no new wire message (ADR-0017 noted the index could
 * back streaming too; the proper server-side aggregation lands in a later Phase
 * 8 chunk, ADR-0018). The request function is injected so this is testable
 * without the WebSocket client.
 *
 * Fetches sequential windows `[0, min(total, cap))`. Advancement is driven by
 * the server's echoed `start_index` plus the returned batch length, so a short
 * or clamped window can never spin forever; an empty or non-advancing response
 * ends the loop.
 */
export async function fetchFullTrace(
  requestTraceWindow: RequestTraceWindow,
  sessionId: string,
  total: number,
  options: FetchFullTraceOptions = {}
): Promise<FullTraceResult> {
  const chunk = options.chunk ?? 1000;
  const cap = options.cap ?? 50_000;
  const limit = Math.min(total, cap);
  const events: TraceEvent[] = [];

  let start = 0;
  while (start < limit) {
    const count = Math.min(chunk, limit - start);
    const msg = await requestTraceWindow(sessionId, start, count);
    const batch = msg.payload.events;
    if (batch.length === 0) break;
    for (const e of batch) events.push(e);
    const next = msg.payload.start_index + batch.length;
    if (next <= start) break; // no forward progress — bail
    start = next;
  }

  return { events, truncated: total > cap };
}
