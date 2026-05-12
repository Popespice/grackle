import type { WsEnvelope } from "@grackle/shared-types";
import { create } from "zustand";

export type ConnectionStatus = "disconnected" | "connecting" | "connected";

interface GrackleClientState {
  status: ConnectionStatus;
  lastPong: string | null;
  _ws: WebSocket | null;
  connect: (url: string) => void;
  disconnect: () => void;
  ping: () => void;
}

export const useGrackleClient = create<GrackleClientState>()((set, get) => ({
  status: "disconnected",
  lastPong: null,
  _ws: null,

  connect: (url: string) => {
    get()._ws?.close();
    set({ status: "connecting", _ws: null });

    const ws = new WebSocket(url);

    ws.addEventListener("open", () => {
      set({ status: "connected" });
    });

    ws.addEventListener("close", () => {
      set({ status: "disconnected", _ws: null });
    });

    ws.addEventListener("error", () => {
      set({ status: "disconnected", _ws: null });
    });

    ws.addEventListener("message", (event: MessageEvent<string>) => {
      try {
        const envelope = JSON.parse(event.data) as WsEnvelope;
        if (envelope.type === "pong") {
          set({ lastPong: envelope.id });
        }
      } catch {
        // ignore non-JSON messages
      }
    });

    set({ _ws: ws });
  },

  disconnect: () => {
    get()._ws?.close();
    set({ status: "disconnected", _ws: null });
  },

  ping: () => {
    const { _ws, status } = get();
    if (_ws !== null && status === "connected") {
      const envelope: WsEnvelope = {
        id: crypto.randomUUID(),
        type: "ping",
        payload: {},
      };
      _ws.send(JSON.stringify(envelope));
    }
  },
}));
