/**
 * Hand-written message types for the grackle WebSocket protocol.
 * Stricter than the codegen output in src/generated/ — json-schema-to-typescript v14
 * can't express `maxProperties: 0` as `Record<string, never>`, so these types serve
 * as the canonical public API. The generated files are a sanity-check artifact only.
 * Review src/generated/messages.ts after schema changes to confirm intent is preserved.
 */

import type { Graph } from "./graph.js";

export interface WsEnvelope {
  id: string;
  type: string;
  payload: Record<string, unknown>;
}

export interface PingMessage extends WsEnvelope {
  type: "ping";
  payload: Record<string, never>;
}

export interface PongMessage extends WsEnvelope {
  type: "pong";
  payload: { ping_id: string };
}

// `Graph` has no index signature so it isn't assignable to `Record<string, unknown>`.
// Use a type intersection to avoid the `extends WsEnvelope` payload constraint.
export type StaticGraphMessage = Omit<WsEnvelope, "payload"> & {
  type: "static_graph";
  payload: Graph;
};

export interface ReadSourceRequest extends WsEnvelope {
  type: "read_source";
  payload: { path: string };
}

export interface ReadSourceResponse extends WsEnvelope {
  type: "source_response";
  payload: { path: string; source: string; encoding: string };
}

export interface ReadSourceError extends WsEnvelope {
  type: "source_error";
  payload: {
    path: string;
    reason: "not_found" | "forbidden" | "binary" | "too_large";
  };
}

/** One captured, formatted argument (mirrors trace.schema.json#/$defs/ArgValue). See ADR-0025. */
export interface ArgValue {
  name: string;
  repr: string;
  redacted?: boolean;
  truncated?: boolean;
}

/** Sampled captured values on a TraceEvent. Only 'call' carries args; only 'return' carries ret. */
export interface TraceValues {
  args?: ArgValue[];
  ret?: string;
  ret_truncated?: boolean;
}

/** Shape of a single runtime trace event (mirrors trace.schema.json#/$defs/TraceEvent). */
export interface TraceEvent {
  event: string;
  node_id: string;
  ts_ns: number;
  thread_id: number;
  frame_depth: number;
  metadata?: Record<string, unknown>;
  /** Absent unless --capture-values (Python-only, opt-in, sampled). See ADR-0025. */
  values?: TraceValues;
}

export interface TraceSessionStartMessage extends WsEnvelope {
  type: "trace_session_start";
  payload: {
    session_id: string;
    started_ns: number;
    source: "replay" | "live";
    /** When true, the server supports trace_seek_request for this session. */
    seekable?: boolean;
  };
}

// `TraceEvent` has no index signature — use type intersection for compatibility.
export type TraceEventMessage = Omit<WsEnvelope, "payload"> & {
  type: "trace_event";
  payload: TraceEvent;
};

export interface TraceSessionEndMessage extends WsEnvelope {
  type: "trace_session_end";
  payload: {
    session_id: string;
    ended_ns: number;
    event_count: number;
  };
}

/** Browser request for a window of events from a seekable trace session. */
export interface TraceSeekRequest extends WsEnvelope {
  type: "trace_seek_request";
  payload: {
    session_id: string;
    start_index: number;
    count: number;
  };
}

/** Agent reply to TraceSeekRequest — a window of trace events. id echoes the request. */
export interface TraceWindowMessage extends WsEnvelope {
  type: "trace_window";
  payload: {
    session_id: string;
    start_index: number;
    events: TraceEvent[];
    total: number;
  };
}

/** Agent error reply when TraceSeekRequest cannot be fulfilled. id echoes the request. */
export interface TraceSeekError extends WsEnvelope {
  type: "trace_seek_error";
  payload: {
    session_id: string;
    reason: string;
  };
}

/** Browser request for aggregate stats over a seekable trace session. */
export interface TraceQueryRequest extends WsEnvelope {
  type: "trace_query_request";
  payload: {
    session_id: string;
    kind: "cumulative_heat" | "coverage" | "top_k";
    at_index: number;
    k?: number;
  };
}

/** Agent reply to TraceQueryRequest. id echoes the request. */
export interface TraceQueryResponse extends WsEnvelope {
  type: "trace_query_response";
  payload: {
    session_id: string;
    kind: string;
    at_index: number;
    data: Record<string, unknown>;
    error?: string;
  };
}

/** Browser request for the list of stored sessions in the session library. */
export interface SessionListRequest extends WsEnvelope {
  type: "session_list_request";
  payload: Record<string, never>;
}

/** Agent reply to SessionListRequest. id echoes the request. */
export interface SessionListResponse extends WsEnvelope {
  type: "session_list_response";
  payload: {
    sessions: SessionMeta[];
  };
}

/** Metadata for one stored trace session. */
export interface SessionMeta {
  id: string;
  label: string;
  started_ns: number;
  ended_ns: number;
  source_path: string;
  event_count: number;
  language: string;
}

/** Browser request to load a stored session. Agent responds with trace_session_start (seekable=true). */
export interface SessionLoadRequest extends WsEnvelope {
  type: "session_load_request";
  payload: {
    session_id: string;
  };
}

export type AnyKnownMessage =
  | PingMessage
  | PongMessage
  | StaticGraphMessage
  | ReadSourceRequest
  | ReadSourceResponse
  | ReadSourceError
  | TraceSessionStartMessage
  | TraceEventMessage
  | TraceSessionEndMessage
  | TraceSeekRequest
  | TraceWindowMessage
  | TraceSeekError
  | TraceQueryRequest
  | TraceQueryResponse
  | SessionListRequest
  | SessionListResponse
  | SessionLoadRequest;

/** All message type strings recognised by this schema version. */
export const KNOWN_MESSAGE_TYPES = [
  "ping",
  "pong",
  "static_graph",
  "read_source",
  "source_response",
  "source_error",
  "trace_session_start",
  "trace_event",
  "trace_session_end",
  "trace_seek_request",
  "trace_window",
  "trace_seek_error",
  "trace_query_request",
  "trace_query_response",
  "session_list_request",
  "session_list_response",
  "session_load_request",
] as const;
