import type {
  Graph,
  ReadSourceError,
  ReadSourceRequest,
  ReadSourceResponse,
  SessionListResponse,
  StaticGraphMessage,
  TraceEvent,
  TraceEventMessage,
  TraceQueryResponse,
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

/**
 * One entry in the demo agent's `agent_hello` fixture list.
 *
 * Demo-only (`grackle demo`, not production `grackle serve`) — a production
 * agent never sends `agent_hello`, so `availableFixtures` stays empty and
 * `FixtureSwitcher` renders null outside the demo.
 */
export interface FixtureInfo {
  name: string;
  label: string;
  nodeCount: number;
  edgeCount: number;
  hasTrace?: boolean;
}

interface AgentHello {
  fixtures: FixtureInfo[];
  active: string;
}

interface GrackleClientState {
  status: ConnectionStatus;
  lastPong: string | null;
  availableFixtures: FixtureInfo[];
  activeFixtureName: string | null;
  isLoadingFixture: boolean;
  _ws: WebSocket | null;
  _staticGraphHandlers: Set<(graph: Graph) => void>;
  _pendingReadSource: Map<string, (msg: SourceReply) => void>;
  _pendingTraceWindow: Map<string, (msg: SeekReply) => void>;
  _pendingTraceQuery: Map<string, (msg: TraceQueryResponse) => void>;
  _pendingSessionList: Map<string, (msg: SessionListResponse) => void>;
  _traceSessionStartHandlers: Set<(msg: TraceSessionStartMessage) => void>;
  _traceEventHandlers: Set<(ev: TraceEvent) => void>;
  _traceSessionEndHandlers: Set<(msg: TraceSessionEndMessage) => void>;
  connect: (url: string) => void;
  disconnect: () => void;
  /** Demo-only: ask the demo agent to switch the active fixture. */
  loadFixture: (name: string) => void;
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
  /**
   * Query the agent for aggregate stats over a seekable session.
   * Resolves when the agent replies with trace_query_response.
   * Rejects on error response or after 5 s timeout.
   */
  requestTraceQuery: (
    sessionId: string,
    kind: "cumulative_heat" | "coverage" | "top_k",
    atIndex: number,
    k?: number
  ) => Promise<TraceQueryResponse>;
  /** Request the list of stored sessions from the agent. */
  requestSessionList: () => Promise<SessionListResponse>;
  /** Ask the agent to load a stored session (agent replies with trace_session_start). */
  sendSessionLoad: (sessionId: string) => void;
  onTraceSessionStart: (
    handler: (msg: TraceSessionStartMessage) => void
  ) => () => void;
  onTraceEvent: (handler: (ev: TraceEvent) => void) => () => void;
  onTraceSessionEnd: (
    handler: (msg: TraceSessionEndMessage) => void
  ) => () => void;
}

/** A resolver stored in a pending-request map. */
type PendingResolver<R> = (msg: R) => void;

/**
 * Shared request/response scaffold for the WebSocket request methods.
 *
 * Generates a UUID, arms a 5 s timeout, registers a resolver in the map
 * returned by `getPending` keyed by that id, and sends `envelope` (with its id
 * stamped to the generated UUID). When the matching reply arrives the message
 * listener looks the id up in that map and invokes the stored resolver, which
 * clears the timeout and runs `onReply(msg, resolve, reject)`.
 *
 * `getPending` is a selector (not a captured map reference) so the timeout /
 * reply paths re-read the live map via `get()._pendingX` exactly like the
 * original hand-written methods did.
 *
 * @param get          zustand store getter (for the live socket)
 * @param getPending   selector returning the per-method pending-resolver map
 * @param envelope     the request to send (its id is overwritten with the UUID)
 * @param timeoutLabel reject message used when no reply arrives in time
 * @param onReply      maps the reply `R` to resolve/reject; defaults to
 *                     `resolve(msg)`
 *
 * `R` is the reply type stored in the resolver map; `T` is what the returned
 * promise resolves to (often a narrowing of `R`).
 */
function pendingRequest<R>(
  get: () => GrackleClientState,
  getPending: () => Map<string, PendingResolver<R>>,
  envelope: WsEnvelope,
  timeoutLabel: string
): Promise<R>;
function pendingRequest<R, T>(
  get: () => GrackleClientState,
  getPending: () => Map<string, PendingResolver<R>>,
  envelope: WsEnvelope,
  timeoutLabel: string,
  onReply: (
    msg: R,
    resolve: (value: T) => void,
    reject: (reason: Error) => void
  ) => void
): Promise<T>;
function pendingRequest<R, T = R>(
  get: () => GrackleClientState,
  getPending: () => Map<string, PendingResolver<R>>,
  envelope: WsEnvelope,
  timeoutLabel: string,
  onReply?: (
    msg: R,
    resolve: (value: T) => void,
    reject: (reason: Error) => void
  ) => void
): Promise<T> {
  return new Promise<T>((resolve, reject) => {
    const { _ws, status } = get();
    if (_ws === null || status !== "connected") {
      reject(new Error("not connected"));
      return;
    }
    const id = crypto.randomUUID();
    const sendEnvelope = { ...envelope, id };

    const timeoutId = setTimeout(() => {
      getPending().delete(id);
      reject(new Error(timeoutLabel));
    }, 5000);

    getPending().set(id, (msg: R) => {
      clearTimeout(timeoutId);
      if (onReply) {
        onReply(msg, resolve, reject);
      } else {
        resolve(msg as unknown as T);
      }
    });

    _ws.send(JSON.stringify(sendEnvelope));
  });
}

export const useGrackleClient = create<GrackleClientState>()((set, get) => ({
  status: "disconnected",
  lastPong: null,
  availableFixtures: [],
  activeFixtureName: null,
  isLoadingFixture: false,
  _ws: null,
  _staticGraphHandlers: new Set(),
  _pendingReadSource: new Map(),
  _pendingTraceWindow: new Map(),
  _pendingTraceQuery: new Map(),
  _pendingSessionList: new Map(),
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
        } else if (envelope.type === "agent_hello") {
          // Demo-only: the fixture switcher list.
          const hello = envelope.payload as unknown as AgentHello;
          set({
            availableFixtures: hello.fixtures,
            activeFixtureName: hello.active,
          });
        } else if (envelope.type === "static_graph") {
          const msg = envelope as unknown as StaticGraphMessage;
          // A graph arriving completes any in-flight fixture switch.
          set({ isLoadingFixture: false });
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
          // Discard pending *session-scoped* requests before the new session
          // starts, so a stale reply from the prior session cannot match an
          // entry from the new one.  Session-library requests
          // (_pendingSessionList) are NOT cleared: they are independent of the
          // trace-session lifecycle, and loading a session itself triggers a
          // trace_session_start — clearing them here would orphan an in-flight
          // requestSessionList() until its 5 s timeout.
          get()._pendingTraceWindow.clear();
          get()._pendingTraceQuery.clear();
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
        } else if (envelope.type === "trace_query_response") {
          const resolve = get()._pendingTraceQuery.get(envelope.id);
          if (resolve) {
            get()._pendingTraceQuery.delete(envelope.id);
            resolve(envelope as TraceQueryResponse);
          }
        } else if (envelope.type === "session_list_response") {
          const resolve = get()._pendingSessionList.get(envelope.id);
          if (resolve) {
            get()._pendingSessionList.delete(envelope.id);
            resolve(envelope as SessionListResponse);
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

  loadFixture: (name: string) => {
    const { _ws, status, activeFixtureName } = get();
    if (_ws === null || status !== "connected") return;
    if (name === activeFixtureName) return;
    // Optimistic: the agent replies with a static_graph (no fixture name in
    // the payload), which clears isLoadingFixture above.
    set({ isLoadingFixture: true, activeFixtureName: name });
    const envelope: WsEnvelope = {
      id: crypto.randomUUID(),
      type: "load_fixture",
      payload: { name },
    };
    _ws.send(JSON.stringify(envelope));
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
    const envelope: ReadSourceRequest = {
      id: "",
      type: "read_source",
      payload: { path },
    };
    return pendingRequest<SourceReply>(
      get,
      () => get()._pendingReadSource,
      envelope,
      "read_source timeout"
    );
  },

  requestTraceWindow: (
    sessionId: string,
    startIndex: number,
    count: number
  ) => {
    const envelope: TraceSeekRequest = {
      id: "",
      type: "trace_seek_request",
      payload: { session_id: sessionId, start_index: startIndex, count },
    };
    return pendingRequest<SeekReply, TraceWindowMessage>(
      get,
      () => get()._pendingTraceWindow,
      envelope,
      "trace_seek_request timeout",
      (msg, resolve, reject) => {
        if (msg.type === "trace_window") {
          resolve(msg as TraceWindowMessage);
        } else {
          reject(
            new Error(
              `trace_seek_error: ${(msg as TraceSeekError).payload.reason}`
            )
          );
        }
      }
    );
  },

  requestTraceQuery: (sessionId, kind, atIndex, k) => {
    const payload: Record<string, unknown> = {
      session_id: sessionId,
      kind,
      at_index: atIndex,
    };
    if (k !== undefined) payload.k = k;
    const envelope: WsEnvelope = {
      id: "",
      type: "trace_query_request",
      payload,
    };
    return pendingRequest<TraceQueryResponse, TraceQueryResponse>(
      get,
      () => get()._pendingTraceQuery,
      envelope,
      "trace_query_request timed out",
      (msg, resolve, reject) => {
        if (msg.payload.error) {
          reject(new Error(msg.payload.error as string));
        } else {
          resolve(msg);
        }
      }
    );
  },

  requestSessionList: () => {
    const envelope: WsEnvelope = {
      id: "",
      type: "session_list_request",
      payload: {},
    };
    return pendingRequest<SessionListResponse>(
      get,
      () => get()._pendingSessionList,
      envelope,
      "session_list_request timed out"
    );
  },

  sendSessionLoad: (sessionId: string) => {
    const id = crypto.randomUUID();
    get()._ws?.send(
      JSON.stringify({
        id,
        type: "session_load_request",
        payload: { session_id: sessionId },
      })
    );
  },
}));
