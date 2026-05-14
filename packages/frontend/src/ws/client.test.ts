import { beforeEach, describe, expect, it, vi } from "vitest";
import { useGrackleClient } from "./client";

// Minimal WebSocket mock that records sent messages and exposes simulators
class MockWebSocket {
  sent: string[] = [];
  private handlers = new Map<string, EventListener[]>();

  constructor(public url: string) {}

  addEventListener(event: string, handler: EventListener): void {
    const list = this.handlers.get(event) ?? [];
    list.push(handler);
    this.handlers.set(event, list);
  }

  send(data: string): void {
    this.sent.push(data);
  }

  close(): void {
    this.dispatch("close", new Event("close"));
  }

  simulateOpen(): void {
    this.dispatch("open", new Event("open"));
  }

  simulateMessage(data: string): void {
    this.dispatch("message", new MessageEvent("message", { data }));
  }

  simulateError(): void {
    this.dispatch("error", new Event("error"));
  }

  private dispatch(event: string, e: Event): void {
    for (const h of this.handlers.get(event) ?? []) h(e);
  }
}

let mockWs: MockWebSocket;
// biome-ignore lint/complexity/useArrowFunction: must be a function expression — arrow functions are non-constructable and vi.fn() rejects them as constructor mocks in Vitest 4
const MockWsClass = vi.fn(function (url: string) {
  mockWs = new MockWebSocket(url);
  return mockWs;
});

beforeEach(() => {
  vi.stubGlobal("WebSocket", MockWsClass);
  useGrackleClient.setState({
    status: "disconnected",
    lastPong: null,
    _ws: null,
  });
  MockWsClass.mockClear();
});

describe("useGrackleClient", () => {
  it("transitions to connecting then connected on open", () => {
    const { connect } = useGrackleClient.getState();
    connect("ws://127.0.0.1:7878");

    expect(useGrackleClient.getState().status).toBe("connecting");

    mockWs.simulateOpen();
    expect(useGrackleClient.getState().status).toBe("connected");
  });

  it("transitions to disconnected on close", () => {
    const { connect } = useGrackleClient.getState();
    connect("ws://127.0.0.1:7878");
    mockWs.simulateOpen();
    mockWs.close();

    expect(useGrackleClient.getState().status).toBe("disconnected");
    expect(useGrackleClient.getState()._ws).toBeNull();
  });

  it("transitions to disconnected on error", () => {
    const { connect } = useGrackleClient.getState();
    connect("ws://127.0.0.1:7878");
    mockWs.simulateError();

    expect(useGrackleClient.getState().status).toBe("disconnected");
  });

  it("ping sends a well-formed envelope when connected", () => {
    const { connect, ping } = useGrackleClient.getState();
    connect("ws://127.0.0.1:7878");
    mockWs.simulateOpen();

    ping();

    expect(mockWs.sent).toHaveLength(1);
    const msg = JSON.parse(mockWs.sent[0] ?? "{}") as {
      type: string;
      id: string;
      payload: unknown;
    };
    expect(msg.type).toBe("ping");
    expect(typeof msg.id).toBe("string");
    expect(msg.payload).toEqual({});
  });

  it("ping is a no-op when not connected", () => {
    const { ping } = useGrackleClient.getState();
    ping();
    expect(MockWsClass).not.toHaveBeenCalled();
  });

  it("pong message updates lastPong", () => {
    const { connect } = useGrackleClient.getState();
    connect("ws://127.0.0.1:7878");
    mockWs.simulateOpen();

    const pong = JSON.stringify({
      id: "abc",
      type: "pong",
      payload: { ping_id: "abc" },
    });
    mockWs.simulateMessage(pong);

    expect(useGrackleClient.getState().lastPong).toBe("abc");
  });

  it("non-pong message is ignored", () => {
    const { connect } = useGrackleClient.getState();
    connect("ws://127.0.0.1:7878");
    mockWs.simulateOpen();

    mockWs.simulateMessage(
      JSON.stringify({ id: "x", type: "unknown", payload: {} })
    );
    expect(useGrackleClient.getState().lastPong).toBeNull();
  });

  it("malformed message is ignored without throwing", () => {
    const { connect } = useGrackleClient.getState();
    connect("ws://127.0.0.1:7878");
    mockWs.simulateOpen();

    expect(() => mockWs.simulateMessage("not json")).not.toThrow();
    expect(useGrackleClient.getState().status).toBe("connected");
  });

  it("late events from a stale socket are ignored (StrictMode guard)", () => {
    const { connect } = useGrackleClient.getState();

    // First connect → staleWs
    connect("ws://127.0.0.1:7878");
    const staleWs = mockWs;

    // Second connect → activeWs replaces staleWs in state
    connect("ws://127.0.0.1:7878");
    const activeWs = mockWs;

    expect(useGrackleClient.getState()._ws).toBe(activeWs);

    // Active socket opens normally
    activeWs.simulateOpen();
    expect(useGrackleClient.getState().status).toBe("connected");

    // Stale socket fires open late — must not overwrite status or _ws
    staleWs.simulateOpen();
    expect(useGrackleClient.getState().status).toBe("connected");
    expect(useGrackleClient.getState()._ws).toBe(activeWs);
  });
});
