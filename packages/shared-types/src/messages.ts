/**
 * Hand-written message types for the grackle WebSocket protocol.
 * Must stay in sync with schema/messages.schema.json — run `pnpm codegen` to verify.
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
