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

/** Shape of a single runtime trace event (mirrors trace.schema.json#/$defs/TraceEvent). */
export interface TraceEvent {
  event: string;
  node_id: string;
  ts_ns: number;
  thread_id: number;
  frame_depth: number;
  metadata?: Record<string, unknown>;
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
  | TraceSeekError;

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
] as const;
