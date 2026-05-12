/**
 * Hand-written message types for the grackle WebSocket protocol.
 * Stricter than the codegen output in src/generated/ — json-schema-to-typescript v14
 * can't express `maxProperties: 0` as `Record<string, never>`, so these types serve
 * as the canonical public API. The generated files are a sanity-check artifact only.
 * Review src/generated/messages.ts after schema changes to confirm intent is preserved.
 */

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

export type AnyKnownMessage = PingMessage | PongMessage;
