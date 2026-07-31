import { ApiError, apiRequest, apiStreamResponse, type ApiPath } from "./client";
import {
  alertEventListSchema,
  alertEventSchema,
  alertIncidentPromotionSchema,
  type AlertEvent,
  type AlertEventSeverity,
  type AlertEventStatus,
  type AlertIncidentPromotion,
} from "./alert-events-schemas";
import { encodePathSegment } from "./url";
import { parseSseFrames } from "../shared/streaming/sse";

export const ALERT_EVENTS_PATH: ApiPath = "/api/alert-events";
export const ALERT_EVENTS_STREAM_PATH: ApiPath = "/api/alert-events/stream";
const SSE_MEDIA_TYPE = "text/event-stream";

export interface AlertEventListOptions {
  from?: string;
  to?: string;
  ruleId?: string;
  severity?: AlertEventSeverity;
  status?: AlertEventStatus;
  limit?: number;
  signal?: AbortSignal;
}

export type AlertStreamLifecycleState =
  | "connecting"
  | "connected"
  | "closed"
  | "failed";

export interface AlertEventStreamSubscription {
  signal?: AbortSignal;
  onLifecycle?: (state: AlertStreamLifecycleState) => void;
}

export function listAlertEvents(options: AlertEventListOptions = {}): Promise<AlertEvent[]> {
  const params = new URLSearchParams();
  if (options.from) params.set("from", options.from);
  if (options.to) params.set("to", options.to);
  if (options.ruleId) params.set("rule_id", options.ruleId);
  if (options.severity) params.set("severity", options.severity);
  if (options.status) params.set("status", options.status);
  params.set("limit", String(options.limit ?? 200));
  return apiRequest(
    `${ALERT_EVENTS_PATH}?${params.toString()}` as ApiPath,
    alertEventListSchema,
    { signal: options.signal },
  );
}

export async function* subscribeAlertEvents(
  subscription: AlertEventStreamSubscription = {},
): AsyncIterable<AlertEvent> {
  const { onLifecycle, signal } = subscription;
  onLifecycle?.("connecting");
  try {
    const response = await apiStreamResponse(ALERT_EVENTS_STREAM_PATH, SSE_MEDIA_TYPE, signal);
    if (!response.headers.get("content-type")?.toLowerCase().startsWith(SSE_MEDIA_TYPE)) {
      throw new ApiError("invalid-payload", "Alert stream did not use text/event-stream.");
    }
    if (!response.body) {
      throw new ApiError("invalid-payload", "Alert stream body was unavailable.");
    }
    onLifecycle?.("connected");
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
          if (frame.event !== "alert") continue;
          try {
            yield alertEventSchema.parse(JSON.parse(frame.data));
          } catch (cause) {
            throw new ApiError("invalid-payload", "Alert stream event was invalid.", { cause });
          }
        }
        if (result.done) break;
      }
      onLifecycle?.("closed");
    } finally {
      reader.releaseLock();
    }
  } catch (cause) {
    onLifecycle?.("failed");
    throw cause;
  }
}

export function acknowledgeAlertEvent(
  eventId: string,
  signal?: AbortSignal,
): Promise<AlertEvent> {
  return apiRequest(
    alertEventActionPath(eventId, "ack"),
    alertEventSchema,
    { method: "POST", signal },
  );
}

export function promoteAlertEvent(
  eventId: string,
  signal?: AbortSignal,
): Promise<AlertIncidentPromotion> {
  return apiRequest(
    alertEventActionPath(eventId, "promote-incident"),
    alertIncidentPromotionSchema,
    { method: "POST", signal },
  );
}

function alertEventActionPath(
  eventId: string,
  action: "ack" | "promote-incident",
): ApiPath {
  if (eventId.trim() === "") throw new TypeError("eventId must not be empty");
  return `${ALERT_EVENTS_PATH}/${encodePathSegment(eventId)}/${action}` as ApiPath;
}
