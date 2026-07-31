import { ApiError } from "./client";
import {
  createRealtimeSequenceState,
  liveSubscriptionSchema,
  parseRealtimeTextFrame,
  reduceRealtimeSequence,
  type LiveSubscription,
  type RealtimeConnectionStatus,
  type RealtimeMessage,
  type RealtimeSequenceState,
} from "./live-schemas";

export {
  createRealtimeSequenceState,
  reduceRealtimeSequence,
  type RealtimeConnectionStatus,
  type RealtimeMessage,
  type RealtimeSequenceReduction,
  type RealtimeSequenceState,
} from "./live-schemas";

const LIVE_BROWSER_PATH = "/api/live/browser";
const PROTOCOL_CLOSE_CODE = 1002;
const PROTOCOL_CLOSE_REASON = "Invalid realtime.v1 payload";
const DEFAULT_RECONNECT_BASE_DELAY_MS = 500;
const DEFAULT_RECONNECT_MAX_DELAY_MS = 10_000;
const DEFAULT_RECONNECT_MAX_ATTEMPTS = 8;
const DEFAULT_HANDSHAKE_TIMEOUT_MS = 10_000;

export interface RealtimeConnectionState {
  readonly status: RealtimeConnectionStatus;
  readonly reconnectAttempt: number;
  readonly sequence: RealtimeSequenceState;
  readonly error: ApiError | null;
}

export interface RealtimeReconnectOptions {
  readonly baseDelayMs?: number;
  readonly maxDelayMs?: number;
  readonly maxAttempts?: number;
  readonly handshakeTimeoutMs?: number;
}

export interface RealtimeClientOptions {
  readonly subscription: LiveSubscription;
  readonly reconnect?: RealtimeReconnectOptions;
  readonly onMessage?: (message: RealtimeMessage) => void;
  readonly onStateChange?: (state: RealtimeConnectionState) => void;
  readonly onError?: (error: ApiError) => void;
}

export interface RealtimeClient {
  connect(): void;
  close(code?: number, reason?: string): void;
  getState(): RealtimeConnectionState;
}
interface BrowserLocation {
  readonly protocol: string;
  readonly host: string;
}

interface ReconnectPolicy {
  readonly baseDelayMs: number;
  readonly maxDelayMs: number;
  readonly maxAttempts: number;
  readonly handshakeTimeoutMs: number;
}

/** Builds the same-host WebSocket URL so browser cookies remain same-origin. */
export function buildRealtimeUrl(
  subscriptionInput: LiveSubscription,
  browserLocation: BrowserLocation = window.location,
): string {
  const subscription = liveSubscriptionSchema.parse(subscriptionInput);
  const socketProtocol = websocketProtocol(browserLocation.protocol);
  const url = new URL(LIVE_BROWSER_PATH, `${socketProtocol}//${browserLocation.host}`);

  url.searchParams.set("workspace_id", subscription.workspaceId);
  setOptionalQuery(url, "cluster_id", subscription.clusterId);
  setOptionalQuery(url, "namespace", subscription.namespace);
  setOptionalQuery(url, "app", subscription.app);
  return url.toString();
}

export function createRealtimeClient(options: RealtimeClientOptions): RealtimeClient {
  return new BrowserRealtimeClient(options);
}

export function connectRealtime(options: RealtimeClientOptions): RealtimeClient {
  const client = createRealtimeClient(options);
  client.connect();
  return client;
}

class BrowserRealtimeClient implements RealtimeClient {
  private readonly options: RealtimeClientOptions;
  private readonly subscription: LiveSubscription;
  private readonly reconnectPolicy: ReconnectPolicy;
  private socket: WebSocket | null = null;
  private reconnectTimer: ReturnType<typeof setTimeout> | null = null;
  private handshakeTimer: ReturnType<typeof setTimeout> | null = null;
  private explicitlyClosed = false;
  private reconnectAttemptsUsed = 0;
  private status: RealtimeConnectionStatus = "idle";
  private sequenceState = createRealtimeSequenceState();
  private error: ApiError | null = null;

  constructor(options: RealtimeClientOptions) {
    this.options = options;
    this.subscription = liveSubscriptionSchema.parse(options.subscription);
    this.reconnectPolicy = reconnectPolicy(options.reconnect);
  }

  connect(): void {
    if (this.socket !== null || this.reconnectTimer !== null) return;
    this.explicitlyClosed = false;
    this.reconnectAttemptsUsed = 0;
    this.openSocket("connecting");
  }

  close(code = 1000, reason = "Client closed realtime connection"): void {
    this.explicitlyClosed = true;
    this.clearReconnectTimer();
    this.clearHandshakeTimer();
    const socket = this.socket;
    this.socket = null;
    if (socket !== null && socket.readyState < WebSocket.CLOSING) {
      socket.close(code, reason);
    }
    this.status = "closed";
    this.emitState();
  }

  getState(): RealtimeConnectionState {
    return {
      status: this.status,
      reconnectAttempt: this.reconnectAttemptsUsed,
      sequence: this.sequenceState,
      error: this.error,
    };
  }

  private openSocket(status: "connecting" | "reconnecting"): void {
    this.sequenceState = createRealtimeSequenceState();
    this.status = status;
    this.emitState();
    let socket: WebSocket;
    try {
      // Browser WebSocket handshakes automatically carry same-origin cookies.
      // Tokens and synthetic fallback data are intentionally not accepted here.
      socket = new WebSocket(buildRealtimeUrl(this.subscription));
    } catch (cause) {
      this.recordError(networkError("Realtime connection could not be opened.", cause));
      this.scheduleReconnect();
      return;
    }
    this.socket = socket;
    socket.addEventListener("open", () => {
      if (this.socket !== socket || this.explicitlyClosed) return;
      this.startHandshakeTimer(socket);
    });
    socket.addEventListener("message", (event) => {
      if (this.socket !== socket || this.explicitlyClosed) return;
      this.handleMessage(socket, event.data);
    });
    socket.addEventListener("error", () => {
      if (this.socket !== socket || this.explicitlyClosed) return;
      this.recordError(networkError("Realtime connection reported a transport error."));
    });
    socket.addEventListener("close", () => {
      if (this.socket !== socket) return;
      this.socket = null;
      this.clearHandshakeTimer();
      if (!this.explicitlyClosed) this.scheduleReconnect();
    });
  }

  private handleMessage(socket: WebSocket, rawData: unknown): void {
    let message: RealtimeMessage;
    try {
      message = parseRealtimeTextFrame(rawData);
    } catch (cause) {
      const error = invalidPayloadError(cause);
      this.recordError(error);
      socket.close(PROTOCOL_CLOSE_CODE, PROTOCOL_CLOSE_REASON);
      return;
    }
    const reduction = reduceRealtimeSequence(this.sequenceState, message);
    if (!reduction.accepted) return;
    this.sequenceState = reduction.state;
    this.options.onMessage?.(message);
    if (this.sequenceState.connected) {
      this.clearHandshakeTimer();
      this.reconnectAttemptsUsed = 0;
      this.error = null;
      this.status = "connected";
    }
    this.emitState();
  }

  private startHandshakeTimer(socket: WebSocket): void {
    this.clearHandshakeTimer();
    this.handshakeTimer = setTimeout(() => {
      if (this.socket !== socket || this.sequenceState.connected || this.explicitlyClosed) return;
      this.recordError(
        networkError("Realtime hello and snapshot handshake timed out."),
      );
      socket.close(PROTOCOL_CLOSE_CODE, "Realtime handshake timed out");
    }, this.reconnectPolicy.handshakeTimeoutMs);
  }

  private scheduleReconnect(): void {
    if (this.explicitlyClosed || this.reconnectTimer !== null) return;
    if (this.reconnectAttemptsUsed >= this.reconnectPolicy.maxAttempts) {
      this.status = "disconnected";
      this.emitState();
      return;
    }
    const attempt = this.reconnectAttemptsUsed + 1;
    const delay = reconnectDelay(this.reconnectPolicy, attempt);
    this.status = "reconnecting";
    this.emitState();
    this.reconnectTimer = setTimeout(() => {
      this.reconnectTimer = null;
      if (this.explicitlyClosed) return;
      this.reconnectAttemptsUsed = attempt;
      this.openSocket("reconnecting");
    }, delay);
  }

  private clearReconnectTimer(): void {
    if (this.reconnectTimer === null) return;
    clearTimeout(this.reconnectTimer);
    this.reconnectTimer = null;
  }

  private clearHandshakeTimer(): void {
    if (this.handshakeTimer === null) return;
    clearTimeout(this.handshakeTimer);
    this.handshakeTimer = null;
  }

  private recordError(error: ApiError): void {
    this.error = error;
    this.options.onError?.(error);
    this.emitState();
  }

  private emitState(): void {
    this.options.onStateChange?.(this.getState());
  }
}

function reconnectPolicy(options: RealtimeReconnectOptions | undefined): ReconnectPolicy {
  const baseDelayMs = positiveInteger(options?.baseDelayMs, DEFAULT_RECONNECT_BASE_DELAY_MS);
  const requestedMaxDelayMs = positiveInteger(options?.maxDelayMs,
    DEFAULT_RECONNECT_MAX_DELAY_MS);
  return {
    baseDelayMs,
    maxDelayMs: Math.max(baseDelayMs, requestedMaxDelayMs),
    maxAttempts: nonNegativeInteger(options?.maxAttempts, DEFAULT_RECONNECT_MAX_ATTEMPTS),
    handshakeTimeoutMs: positiveInteger(options?.handshakeTimeoutMs,
      DEFAULT_HANDSHAKE_TIMEOUT_MS),
  };
}

function reconnectDelay(policy: ReconnectPolicy, attempt: number): number {
  const exponent = Math.min(attempt - 1, 30);
  return Math.min(policy.maxDelayMs, policy.baseDelayMs * 2 ** exponent);
}

function positiveInteger(value: number | undefined, fallback: number): number {
  return value !== undefined && Number.isSafeInteger(value) && value > 0 ? value : fallback;
}

function nonNegativeInteger(value: number | undefined, fallback: number): number {
  return value !== undefined && Number.isSafeInteger(value) && value >= 0 ? value : fallback;
}

function setOptionalQuery(url: URL, name: string, value: string | undefined): void {
  if (value !== undefined && value !== "") url.searchParams.set(name, value);
}

function websocketProtocol(browserProtocol: string): "ws:" | "wss:" {
  if (browserProtocol === "http:") return "ws:";
  if (browserProtocol === "https:") return "wss:";
  throw new ApiError("invalid-request", "Realtime requires an HTTP or HTTPS page origin.");
}

function invalidPayloadError(cause: unknown): ApiError {
  if (cause instanceof ApiError) return cause;
  return new ApiError("invalid-payload", "Realtime payload did not match realtime.v1.", {
    cause,
  });
}

function networkError(message: string, cause?: unknown): ApiError {
  return new ApiError("network", message, { cause });
}
