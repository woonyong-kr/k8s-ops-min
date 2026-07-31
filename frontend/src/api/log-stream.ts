import { ApiError, apiStreamResponse, type ApiPath } from "./client";
import {
  logStreamEventSchema,
  type LogStreamEventEndpoint,
} from "./log-stream-schemas";
import { encodePathSegment, withQuery } from "./url";
import { parseSseFrames } from "../shared/streaming/sse";

const SSE_MEDIA_TYPE = "text/event-stream";

export interface LogStreamEndpointHandlers {
  onEvent: (event: LogStreamEventEndpoint) => void;
  onFailure: (error: unknown) => void;
}

export type WorkloadLogStreamKind = "deployments" | "statefulsets" | "daemonsets";

export function openPodLogStream(
  clusterId: string,
  namespace: string,
  name: string,
  container: string | null,
  handlers: LogStreamEndpointHandlers,
): () => void {
  const path = `/api/pods/${segment(namespace)}/${segment(name)}/logs/stream` as ApiPath;
  return openStream(withQuery(path, [
    ["cluster_id", required(clusterId, "cluster ID")],
    ["container", optional(container)],
  ]), handlers);
}

export function openWorkloadLogStream(
  clusterId: string,
  kind: WorkloadLogStreamKind,
  namespace: string,
  name: string,
  handlers: LogStreamEndpointHandlers,
): () => void {
  const path = `/api/workloads/${segment(kind)}/${segment(namespace)}/${segment(name)}/logs/stream` as ApiPath;
  return openStream(withQuery(path, [["cluster_id", required(clusterId, "cluster ID")]]), handlers);
}

function openStream(path: ApiPath, handlers: LogStreamEndpointHandlers): () => void {
  const controller = new AbortController();
  void consume(path, handlers.onEvent, controller.signal).catch((error: unknown) => {
    if (!isAbortError(error)) handlers.onFailure(error);
  });
  return () => controller.abort();
}

async function consume(
  path: ApiPath,
  onEvent: (event: LogStreamEventEndpoint) => void,
  signal: AbortSignal,
) {
  const response = await apiStreamResponse(path, SSE_MEDIA_TYPE, signal);
  if (!response.headers.get("content-type")?.toLowerCase().startsWith(SSE_MEDIA_TYPE)) {
    throw new ApiError("invalid-payload", "Log stream did not use text/event-stream.");
  }
  if (!response.body) throw new ApiError("invalid-payload", "Log stream body was unavailable.");
  const reader = response.body.getReader();
  const decoder = new TextDecoder();
  let buffer = "";
  try {
    while (true) {
      const result = await reader.read();
      buffer += decoder.decode(result.value, { stream: !result.done });
      const parsed = parseSseFrames(buffer);
      buffer = parsed.remainder;
      for (const frame of parsed.frames) {
        if (frame.event !== null && frame.event !== "message") {
          throw invalidFrame("unexpected SSE event name");
        }
        const event = parsePayload(frame.data);
        onEvent(event);
        if (event.type === "end" || event.type === "error") {
          await reader.cancel();
          return;
        }
      }
      if (result.done) break;
    }
  } finally {
    reader.releaseLock();
  }
  if (buffer.trim()) throw invalidFrame("unterminated SSE frame");
  throw invalidFrame("log stream ended without a terminal event");
}

function parsePayload(payload: string): LogStreamEventEndpoint {
  let decoded: unknown;
  try {
    decoded = JSON.parse(payload);
  } catch (cause) {
    throw new ApiError("invalid-payload", "Log stream data was not valid JSON.", { cause });
  }
  const result = logStreamEventSchema.safeParse(decoded);
  if (!result.success) {
    throw new ApiError("invalid-payload", "Log stream event violated its contract.", {
      cause: result.error,
    });
  }
  return result.data;
}

function invalidFrame(message: string): ApiError {
  return new ApiError("invalid-payload", message);
}

function segment(value: string): string {
  return encodePathSegment(required(value, "path segment"));
}

function required(value: string, label: string): string {
  const normalized = value.trim();
  if (!normalized) throw new TypeError(`${label} must not be empty`);
  return normalized;
}

function optional(value: string | null): string | undefined {
  return value?.trim() || undefined;
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error && error.name === "AbortError";
}
