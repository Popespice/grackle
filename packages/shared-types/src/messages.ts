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

// Demo-branch message types — not part of the production protocol. Carried in
// shared-types so the demo's TypeScript code can typecheck without redefining
// them. Phase 6/7 will replace `PulseMessage` with real sys.monitoring traces.

export interface DemoFixtureInfo {
  name: string;
  label?: string;
  description?: string;
  nodeCount?: number | null;
  edgeCount?: number | null;
}

export interface AgentHelloMessage extends WsEnvelope {
  type: "agent_hello";
  payload: {
    fixtures: DemoFixtureInfo[];
    active: string;
    live: boolean;
    pulseIntervalMs?: number;
    pulseNodesPerPulse?: number;
  };
}

export interface LoadFixtureMessage extends WsEnvelope {
  type: "load_fixture";
  payload: { name: string };
}

export interface PulseMessage extends WsEnvelope {
  type: "pulse";
  payload: { nodes: string[] };
}

export interface SetPulseRateMessage extends WsEnvelope {
  type: "set_pulse_rate";
  payload: { intervalMs?: number; nodesPerPulse?: number };
}

export type AnyKnownMessage =
  | PingMessage
  | PongMessage
  | StaticGraphMessage
  | ReadSourceRequest
  | ReadSourceResponse
  | ReadSourceError
  | AgentHelloMessage
  | LoadFixtureMessage
  | PulseMessage
  | SetPulseRateMessage;
