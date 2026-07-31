import { ApiError, apiStreamResponse, type ApiPath } from "./client";
import {
  commandOperationEventSchema,
  type CommandOperationEventEndpoint,
} from "./operation-events-schemas";
import { encodePathSegment } from "./url";
import type {
  OperationEventsSubscription,
  OperationStreamFailure,
  OperationStreamLifecycle,
} from "../shared/parity/referenceParity";
import { parseSseFrames } from "../shared/streaming/sse";

const SSE_MEDIA_TYPE = "text/event-stream";
const RECONNECT_BASE_DELAY_MS = 250;
const RECONNECT_MAX_DELAY_MS = 5_000;

/** Reconnecting SSE reader for one audited command. It never falls back to status polling. */
export async function* subscribeCommandOperationEvents(
  commandId: string,
  subscription: OperationEventsSubscription = {},
): AsyncIterable<CommandOperationEventEndpoint> {
  const { onLifecycle, signal } = subscription;
  let path: ApiPath;
  try {
    path = commandOperationEventsPath(commandId);
  } catch (error) {
    onLifecycle?.({ state: "failed", failure: "invalid" });
    throw error;
  }
  let cursor: number;
  try {
    cursor = normalizeCursor(subscription.afterSequence);
  } catch (error) {
    onLifecycle?.({ state: "failed", failure: "invalid" });
    throw error;
  }
  let attempt = 0;
  while (!signal?.aborted) {
    onLifecycle?.({ state: "connecting" });
    try {
      const result = yield* consume(
        path,
        cursor,
        signal,
        () => onLifecycle?.({ state: "connected" }),
        (sequence) => {
          cursor = sequence;
        },
      );
      cursor = result.cursor;
      attempt = result.progressed ? 0 : attempt + 1;
      if (result.completed || signal?.aborted) {
        onLifecycle?.({ state: "closed" });
        return;
      }
    } catch (error) {
      if (isAbortError(error)) return;
      if (!isTransientStreamError(error)) {
        onLifecycle?.({ state: "failed", failure: streamFailureFor(error) });
        throw error;
      }
      attempt += 1;
      await reconnectDelay(error, attempt, signal, onLifecycle);
      continue;
    }
    await reconnectDelay(null, attempt, signal, onLifecycle);
  }
}

function commandOperationEventsPath(commandId: string): ApiPath {
  const normalized = commandId.trim();
  if (!normalized) throw new TypeError("command ID must not be empty");
  return `/api/commands/${encodePathSegment(normalized)}/events` as ApiPath;
}

async function* consume(
  path: ApiPath,
  startingCursor: number,
  signal?: AbortSignal,
  onConnected?: () => void,
  onCursor?: (sequence: number) => void,
): AsyncGenerator<CommandOperationEventEndpoint, { completed: boolean; cursor: number; progressed: boolean }> {
  const response = await apiStreamResponse(
    path,
    SSE_MEDIA_TYPE,
    signal,
    startingCursor > 0 ? { "last-event-id": String(startingCursor) } : undefined,
  );
  if (!response.headers.get("content-type")?.toLowerCase().startsWith(SSE_MEDIA_TYPE)) {
    throw new ApiError("invalid-payload", "Operation stream did not use text/event-stream.");
  }
  if (!response.body) throw new ApiError("invalid-payload", "Operation stream body was unavailable.");
  onConnected?.();
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  let cursor = startingCursor;
  let progressed = false;
  try {
    while (true) {
      const result = await reader.read();
      buffer += decoder.decode(result.value, { stream: !result.done });
      const parsed = parseSseFrames(buffer);
      buffer = parsed.remainder;
      for (const frame of parsed.frames) {
        if (frame.event !== "operation") continue;
        const event = parseOperationEvent(frame.data);
        assertFrameSequence(frame.id, event.sequence);
        if (event.command_id !== pathCommandId(path)) {
          throw new ApiError("invalid-payload", "Operation event command ID did not match the stream.");
        }
        if (event.sequence <= cursor) continue;
        if (event.sequence !== cursor + 1) {
          throw new ApiError("invalid-payload", "Operation event sequence was not contiguous.");
        }
        cursor = event.sequence;
        onCursor?.(cursor);
        progressed = true;
        yield event;
        if (
          event.kind === "completed"
          || event.kind === "failed"
          || event.kind === "cancelled"
        ) {
          return { completed: true, cursor, progressed };
        }
      }
      if (result.done) break;
    }
  } finally {
    reader.releaseLock();
  }
  if (buffer.trim()) {
    throw new ApiError("invalid-payload", "Operation stream ended with an unterminated frame.");
  }
  return { completed: false, cursor, progressed };
}

function parseOperationEvent(data: string): CommandOperationEventEndpoint {
  try {
    return commandOperationEventSchema.parse(JSON.parse(data));
  } catch (cause) {
    throw new ApiError("invalid-payload", "Operation event violated its contract.", { cause });
  }
}

function assertFrameSequence(id: string | null, sequence: number): void {
  if (id === null) return;
  if (!/^\d+$/u.test(id) || Number(id) !== sequence) {
    throw new ApiError("invalid-payload", "Operation event SSE ID did not match its sequence.");
  }
}

function pathCommandId(path: ApiPath): string {
  const segments = path.split("/");
  return decodeURIComponent(segments[3] ?? "");
}

function isTransientStreamError(error: unknown): boolean {
  if (!(error instanceof ApiError)) return true;
  return ![
    "unauthorized",
    "forbidden",
    "not-found",
    "invalid-request",
    "invalid-payload",
  ].includes(error.kind);
}

async function reconnectDelay(
  error: unknown,
  attempt: number,
  signal?: AbortSignal,
  onLifecycle?: (lifecycle: OperationStreamLifecycle) => void,
): Promise<void> {
  const delay = reconnectDelayMs(error, attempt);
  onLifecycle?.({ state: "reconnecting", attempt, retryAfterMs: delay });
  await new Promise<void>((resolve) => {
    let settled = false;
    const complete = () => {
      if (settled) return;
      settled = true;
      clearTimeout(timer);
      signal?.removeEventListener("abort", abort);
      resolve();
    };
    const abort = complete;
    const timer = setTimeout(complete, delay);
    signal?.addEventListener("abort", abort, { once: true });
  });
}

function reconnectDelayMs(error: unknown, attempt: number): number {
  const retryAfter = error instanceof ApiError ? error.retryAfter : null;
  const cappedAttempt = Math.min(attempt, 8);
  const cap = Math.min(RECONNECT_MAX_DELAY_MS, RECONNECT_BASE_DELAY_MS * 2 ** cappedAttempt);
  // reconnect backoff jitter, not domain data — randomizes retry spacing to
  // prevent synchronized reconnect storms; never rendered as an observed value.
  return retryAfter === null
    ? Math.floor(cap * (0.5 + Math.random() * 0.5))
    : retryAfter * 1_000;
}

function normalizeCursor(value: number | undefined): number {
  if (value === undefined) return 0;
  if (!Number.isInteger(value) || value < 0) {
    throw new TypeError("Operation stream cursor must be a non-negative integer.");
  }
  return value;
}

function streamFailureFor(error: unknown): OperationStreamFailure {
  if (!(error instanceof ApiError)) return "unavailable";
  if (error.kind === "unauthorized" || error.kind === "forbidden") return "forbidden";
  if (["not-found", "invalid-request", "invalid-payload"].includes(error.kind)) return "invalid";
  return "unavailable";
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error
    && error.name === "AbortError";
}
