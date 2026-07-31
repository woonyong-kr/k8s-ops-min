// @vitest-environment jsdom

import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { buildRealtimeUrl, createRealtimeClient } from "./live";

class FakeWebSocket extends EventTarget {
  static readonly CONNECTING = 0;
  static readonly OPEN = 1;
  static readonly CLOSING = 2;
  static readonly CLOSED = 3;
  static instances: FakeWebSocket[] = [];

  readonly url: string;
  readyState = FakeWebSocket.CONNECTING;
  closeCode: number | null = null;
  closeReason: string | null = null;

  constructor(url: string | URL) {
    super();
    this.url = String(url);
    FakeWebSocket.instances.push(this);
  }

  open(): void {
    this.readyState = FakeWebSocket.OPEN;
    this.dispatchEvent(new Event("open"));
  }

  message(payload: unknown): void {
    this.dispatchEvent(new MessageEvent("message", { data: JSON.stringify(payload) }));
  }

  rawMessage(payload: unknown): void {
    this.dispatchEvent(new MessageEvent("message", { data: payload }));
  }

  disconnect(): void {
    this.readyState = FakeWebSocket.CLOSED;
    this.dispatchEvent(new CloseEvent("close", { code: 1006 }));
  }

  close(code = 1000, reason = ""): void {
    this.closeCode = code;
    this.closeReason = reason;
    this.readyState = FakeWebSocket.CLOSED;
  }
}

const hello = {
  type: "hello",
  protocol: "realtime.v1",
  stream_policy: {
    revision: 1,
    max_frames_per_second: 60,
    hidden_tab: "coalesce",
    max_pending_messages: 32,
  },
};

beforeEach(() => {
  FakeWebSocket.instances = [];
  vi.stubGlobal("WebSocket", FakeWebSocket);
});

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("browser realtime transport", () => {
  it("uses a same-origin cookie-authenticated URL without token query parameters", () => {
    const url = new URL(buildRealtimeUrl(
      { workspaceId: "workspace-a", clusterId: "cluster-a", namespace: "sandbox" },
      { protocol: "https:", host: "console.example.test" },
    ));

    expect(url.protocol).toBe("wss:");
    expect(url.host).toBe("console.example.test");
    expect(url.pathname).toBe("/api/live/browser");
    expect(url.searchParams.get("workspace_id")).toBe("workspace-a");
    expect(url.searchParams.get("cluster_id")).toBe("cluster-a");
    expect(url.searchParams.get("namespace")).toBe("sandbox");
    expect([...url.searchParams.keys()]).not.toContain("token");
  });

  it("requires hello and snapshot, then reconnects with a fresh sequence", () => {
    vi.useFakeTimers();
    const states: string[] = [];
    const messages: string[] = [];
    const client = createRealtimeClient({
      subscription: { workspaceId: "workspace-a", clusterId: "cluster-a" },
      reconnect: {
        baseDelayMs: 100,
        maxDelayMs: 100,
        maxAttempts: 2,
        handshakeTimeoutMs: 1_000,
      },
      onMessage: (message) => messages.push(message.type),
      onStateChange: (state) => states.push(state.status),
    });

    client.connect();
    const first = FakeWebSocket.instances[0]!;
    first.open();
    first.message(hello);
    expect(client.getState().status).toBe("connecting");
    first.message({ type: "snapshot", seq: 1, state: {} });
    expect(client.getState()).toEqual(expect.objectContaining({
      status: "connected",
      reconnectAttempt: 0,
    }));
    expect(messages).toEqual(["hello", "snapshot"]);

    first.disconnect();
    expect(client.getState().status).toBe("reconnecting");
    vi.advanceTimersByTime(100);

    expect(FakeWebSocket.instances).toHaveLength(2);
    expect(client.getState().status).toBe("reconnecting");
    expect(client.getState().reconnectAttempt).toBe(1);
    expect(client.getState().sequence.connected).toBe(false);
    expect(states).toContain("connected");
    client.close();
  });

  it("rejects malformed frames without publishing them as observed data", () => {
    const messages: string[] = [];
    const errors: string[] = [];
    const client = createRealtimeClient({
      subscription: { workspaceId: "workspace-a", clusterId: "cluster-a" },
      onMessage: (message) => messages.push(message.type),
      onError: (error) => errors.push(error.kind),
    });

    client.connect();
    const socket = FakeWebSocket.instances[0]!;
    socket.open();
    socket.rawMessage("{not-json");

    expect(messages).toEqual([]);
    expect(errors).toEqual(["invalid-payload"]);
    expect(socket.closeCode).toBe(1002);
    expect(socket.closeReason).toBe("Invalid realtime.v1 payload");
    expect(client.getState().sequence.connected).toBe(false);
    client.close();
  });
});
