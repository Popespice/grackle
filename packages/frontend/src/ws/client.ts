import type {
  Graph,
  ReadSourceError,
  ReadSourceRequest,
  ReadSourceResponse,
  StaticGraphMessage,
  TraceEvent,
  TraceEventMessage,
  TraceSeekError,
  TraceSeekRequest,
  TraceSessionEndMessage,
  TraceSessionStartMessage,
  TraceWindowMessage,
  WsEnvelope,
} from "@grackle/shared-types";
import { create } from "zustand";

export type ConnectionStatus = "disconnected" | "connecting" | "connected";

type SourceReply = ReadSourceResponse | ReadSourceError;
type SeekReply = TraceWindowMessage | TraceSeekError;

interface GrackleClientState {
  status: ConnectionStatus;
  lastPong: string | null;
  _ws: WebSocket | null;
  _staticGraphHandlers: Set<(graph: Graph) => void>;
  _pendingReadSource: Map<string, (msg: SourceReply) => void>;
  _pendingTraceWindow: Map<string, (msg: SeekReply) => void>;
  _traceSessionStartHandlers: Set<(msg: TraceSessionStartMessage) => void>;
  _traceEventHandlers: Set<(ev: TraceEvent) => void>;
  _traceSessionEndHandlers: Set<(msg: TraceSessionEndMessage) => void>;
  connect: (url: string) => void;
  disconnect: () => void;
  ping: () => void;
  onStaticGraph: (handler: (graph: Graph) => void) => () => void;
  sendReadSource: (path: string) => Promise<SourceReply>;
  /**
   * Request a window of events from a seekable trace session.
   *
   * Sends a ``trace_seek_request`` and resolves when the server replies with
   * ``trace_window``.  Rejects on ``trace_seek_error`` or after a 5 s timeout.
   */
  requestTraceWindow: (
    sessionId: string,
    startIndex: number,
    count: number
  ) => Promise<TraceWindowMessage>;
  onTraceSessionStart: (
    handler: (msg: TraceSessionStartMessage) => void
  ) => () => void;
  onTraceEvent: (handler: (ev: TraceEvent) => void) => () => void;
  onTraceSessionEnd: (
    handler: (msg: TraceSessionEndMessage) => void
  ) => () => void;
}

export const useGrackleClient = create<GrackleClientState>()((set, get) => ({
  status: "disconnected",
  lastPong: null,
  _ws: null,
  _staticGraphHandlers: new Set(),
  _pendingReadSource: new Map(),
  _pendingTraceWindow: new Map(),
  _traceSessionStartHandlers: new Set(),
  _traceEventHandlers: new Set(),
  _traceSessionEndHandlers: new Set(),

  connect: (url: string) => {
    get()._ws?.close();
    set({ status: "connecting", _ws: null });

    const ws = new WebSocket(url);

    // Guards against late events from a stale socket. React StrictMode runs
    // effects twice in dev, so two sockets may exist briefly — only the
    // second one should update state.
    ws.addEventListener("open", () => {
      if (get()._ws === ws) set({ status: "connected" });
    });

    ws.addEventListener("close", () => {
      if (get()._ws === ws) set({ status: "disconnected", _ws: null });
    });

    ws.addEventListener("error", () => {
      if (get()._ws === ws) set({ status: "disconnected", _ws: null });
    });

    ws.addEventListener("message", (event: MessageEvent<string>) => {
      if (get()._ws !== ws) return;
      try {
        const envelope = JSON.parse(event.data) as WsEnvelope;
        if (envelope.type === "pong") {
          set({ lastPong: envelope.id });
        } else if (envelope.type === "static_graph") {
          const msg = envelope as unknown as StaticGraphMessage;
          get()._staticGraphHandlers.forEach((h) => {
            h(msg.payload);
          });
        } else if (
          envelope.type === "source_response" ||
          envelope.type === "source_error"
        ) {
          const resolver = get()._pendingReadSource.get(envelope.id);
          if (resolver) {
            get()._pendingReadSource.delete(envelope.id);
            resolver(envelope as SourceReply);
          }
        } else if (envelope.type === "trace_session_start") {
          // Discard pending seek requests from the prior session before the
          // new session starts.  A stale trace_window reply arriving after the
          // session restart could otherwise match a new session's pending
          // entry (same envelope id is astronomically unlikely, but the map
          // should be empty between sessions on principle).
          get()._pendingTraceWindow.clear();
          const msg = envelope as unknown as TraceSessionStartMessage;
          get()._traceSessionStartHandlers.forEach((h) => {
            h(msg);
          });
        } else if (envelope.type === "trace_event") {
          const msg = envelope as unknown as TraceEventMessage;
          get()._traceEventHandlers.forEach((h) => {
            h(msg.payload);
          });
        } else if (envelope.type === "trace_session_end") {
          const msg = envelope as unknown as TraceSessionEndMessage;
          get()._traceSessionEndHandlers.forEach((h) => {
            h(msg);
          });
        } else if (
          envelope.type === "trace_window" ||
          envelope.type === "trace_seek_error"
        ) {
          // Seek reply — resolve the pending request by envelope id.
          const resolver = get()._pendingTraceWindow.get(envelope.id);
          if (resolver) {
            get()._pendingTraceWindow.delete(envelope.id);
            resolver(envelope as SeekReply);
          }
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

  onStaticGraph: (handler: (graph: Graph) => void) => {
    get()._staticGraphHandlers.add(handler);
    return () => {
      get()._staticGraphHandlers.delete(handler);
    };
  },

  onTraceSessionStart: (handler: (msg: TraceSessionStartMessage) => void) => {
    get()._traceSessionStartHandlers.add(handler);
    return () => {
      get()._traceSessionStartHandlers.delete(handler);
    };
  },

  onTraceEvent: (handler: (ev: TraceEvent) => void) => {
    get()._traceEventHandlers.add(handler);
    return () => {
      get()._traceEventHandlers.delete(handler);
    };
  },

  onTraceSessionEnd: (handler: (msg: TraceSessionEndMessage) => void) => {
    get()._traceSessionEndHandlers.add(handler);
    return () => {
      get()._traceSessionEndHandlers.delete(handler);
    };
  },

  sendReadSource: (path: string) => {
    return new Promise<SourceReply>((resolve, reject) => {
      const { _ws, status } = get();
      if (_ws === null || status !== "connected") {
        reject(new Error("not connected"));
        return;
      }
      const id = crypto.randomUUID();
      const timeoutId = setTimeout(() => {
        get()._pendingReadSource.delete(id);
        reject(new Error("read_source timeout"));
      }, 5000);

      get()._pendingReadSource.set(id, (msg: SourceReply) => {
        clearTimeout(timeoutId);
        resolve(msg);
      });

      const envelope: ReadSourceRequest = {
        id,
        type: "read_source",
        payload: { path },
      };
      _ws.send(JSON.stringify(envelope));
    });
  },

  requestTraceWindow: (
    sessionId: string,
    startIndex: number,
    count: number
  ) => {
    return new Promise<TraceWindowMessage>((resolve, reject) => {
      const { _ws, status } = get();
      if (_ws === null || status !== "connected") {
        reject(new Error("not connected"));
        return;
      }
      const id = crypto.randomUUID();
      const timeoutId = setTimeout(() => {
        get()._pendingTraceWindow.delete(id);
        reject(new Error("trace_seek_request timeout"));
      }, 5000);

      get()._pendingTraceWindow.set(id, (msg: SeekReply) => {
        clearTimeout(timeoutId);
        if (msg.type === "trace_window") {
          resolve(msg as TraceWindowMessage);
        } else {
          reject(
            new Error(
              `trace_seek_error: ${(msg as TraceSeekError).payload.reason}`
            )
          );
        }
      });

      const envelope: TraceSeekRequest = {
        id,
        type: "trace_seek_request",
        payload: { session_id: sessionId, start_index: startIndex, count },
      };
      _ws.send(JSON.stringify(envelope));
    });
  },
}));
