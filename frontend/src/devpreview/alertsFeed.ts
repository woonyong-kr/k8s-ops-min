import { useEffect, useState } from "react";

import { listAlertChannels } from "../api/alert-channels";
import type { AlertChannel } from "../api/alert-channels-schemas";
import { listAlertEvents, subscribeAlertEvents } from "../api/alert-events";
import type { AlertEvent } from "../api/alert-events-schemas";
import { listAlertRules } from "../api/alert-rules";
import type { AlertRule } from "../api/alert-rules-schemas";

// UI-PHASE2-001 §2 "Alerts": typed live adapters for the /alerts surface.
// Reads `GET /api/alert-events` (fired/resolved events), `GET /api/alert-rules`
// (configured rules) and `GET /api/alert-channels` (delivery channels). The dev
// contract currently returns an empty event/rule/channel set — that is honest
// "관측된 알림 없음", never a fabricated queue. Mutations (rule enable/disable,
// creation, channel test) are NOT issued here; they belong to explicit user
// clicks with CSRF, so these hooks are strictly read-only.

export type AlertsFeedStatus = "loading" | "ready" | "unavailable";

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error
    && (error as { name?: unknown }).name === "AbortError";
}

// ── events ───────────────────────────────────────────────────────────────────

export interface AlertEventView {
  eventId: string;
  ruleId: string | null;
  ruleName: string | null;
  source: AlertEvent["source"];
  severity: AlertEvent["severity"];
  status: AlertEvent["status"];
  cluster: string;
  namespace: string | null;
  kind: string;
  name: string;
  firedAt: string;
  resolvedAt: string | null;
  observedValue: number | null;
  threshold: number | null;
  evidence: AlertEvent["evidence"];
  incidentId: string | null;
  acknowledgedAt: string | null;
  acknowledgedBy: string | null;
  promotedAt: string | null;
  promotedBy: string | null;
}

export interface AlertEventsFeed {
  status: AlertsFeedStatus;
  items: AlertEventView[];
  transport: "connecting" | "http" | "sse" | "stale";
}

export function alertEventOccurrenceKey(
  event: Pick<AlertEventView, "eventId" | "incidentId">,
): string {
  return `${event.eventId}\u0000${event.incidentId ?? ""}`;
}

export function isIncidentNotification(event: AlertEventView): boolean {
  return (
    (event.source === "incident" || event.source === "alertmanager")
    && event.status === "firing"
    && event.incidentId !== null
  );
}

function toEventView(event: AlertEvent): AlertEventView {
  return {
    eventId: event.event_id,
    ruleId: event.rule_id,
    ruleName: event.rule_name,
    source: event.source,
    severity: event.severity,
    status: event.status,
    cluster: event.subject.cluster,
    namespace: event.subject.namespace,
    kind: event.subject.kind,
    name: event.subject.name,
    firedAt: event.fired_at,
    resolvedAt: event.resolved_at,
    observedValue: event.observed_value,
    threshold: event.threshold,
    evidence: event.evidence,
    incidentId: event.incident_id,
    acknowledgedAt: event.acknowledged_at,
    acknowledgedBy: event.acknowledged_by,
    promotedAt: event.promoted_at,
    promotedBy: event.promoted_by,
  };
}

/**
 * Reads the live alert-event list. A load failure is an honest `unavailable`;
 * an empty list is an honest "no observed alerts", never backfilled.
 */
export function useAlertEvents(): AlertEventsFeed {
  const [feed, setFeed] = useState<AlertEventsFeed>({
    status: "loading",
    items: [],
    transport: "connecting",
  });
  useEffect(() => {
    const controller = new AbortController();
    const refresh = async () => {
      const events = await listAlertEvents({ signal: controller.signal });
      if (!controller.signal.aborted) {
        setFeed({
          status: "ready",
          items: events.map(toEventView),
          transport: "http",
        });
      }
    };
    const run = async () => {
      try {
        await refresh();
      } catch (cause: unknown) {
        if (controller.signal.aborted || isAbortError(cause)) return;
        setFeed({
          status: "unavailable",
          items: [],
          transport: "stale",
        });
      }
      while (!controller.signal.aborted) {
        try {
          for await (const event of subscribeAlertEvents({
            signal: controller.signal,
            onLifecycle: (lifecycle) => {
              if (controller.signal.aborted) return;
              setFeed((current) => ({
                ...current,
                transport: lifecycle === "connected"
                  ? "sse"
                  : lifecycle === "connecting"
                    ? current.status === "ready" ? "stale" : "connecting"
                    : "stale",
              }));
            },
          })) {
            if (controller.signal.aborted) return;
            const next = toEventView(event);
            setFeed((current) => ({
              status: "ready",
              items: mergeAlertEvent(current.items, next),
              transport: "sse",
            }));
          }
        } catch (cause: unknown) {
          if (controller.signal.aborted || isAbortError(cause)) return;
          setFeed((current) => ({ ...current, transport: "stale" }));
        }
        try {
          await refresh();
        } catch (cause: unknown) {
          if (controller.signal.aborted || isAbortError(cause)) return;
          setFeed((current) => ({ ...current, transport: "stale" }));
        }
        await reconnectDelay(controller.signal);
      }
    };
    void run();
    return () => controller.abort();
  }, []);
  return feed;
}

function mergeAlertEvent(current: AlertEventView[], next: AlertEventView): AlertEventView[] {
  return [next, ...current.filter((item) => item.eventId !== next.eventId)]
    .sort((a, b) => Date.parse(b.firedAt) - Date.parse(a.firedAt))
    .slice(0, 200);
}

async function reconnectDelay(signal: AbortSignal): Promise<void> {
  await new Promise<void>((resolve) => {
    const timeout = window.setTimeout(resolve, 1_000);
    signal.addEventListener("abort", () => {
      window.clearTimeout(timeout);
      resolve();
    }, { once: true });
  });
}

// ── rules ────────────────────────────────────────────────────────────────────

export interface AlertRuleView {
  ruleId: string;
  name: string;
  metric: AlertRule["metric"];
  comparator: AlertRule["comparator"];
  threshold: number;
  severity: AlertRule["severity"];
  enabled: boolean;
  channels: string[];
  lastFiredAt: string | null;
  occurrenceCount: number;
}

export interface AlertRulesFeed {
  status: AlertsFeedStatus;
  items: AlertRuleView[];
}

function toRuleView(rule: AlertRule): AlertRuleView {
  return {
    ruleId: rule.rule_id,
    name: rule.name,
    metric: rule.metric,
    comparator: rule.comparator,
    threshold: rule.threshold,
    severity: rule.severity,
    enabled: rule.enabled,
    channels: [...rule.channels],
    lastFiredAt: rule.last_fired_at,
    occurrenceCount: rule.occurrence_count,
  };
}

/** Reads the live alert-rule list; failure is honest `unavailable`. */
export function useAlertRules(): AlertRulesFeed {
  const [feed, setFeed] = useState<AlertRulesFeed>({ status: "loading", items: [] });
  useEffect(() => {
    const controller = new AbortController();
    void listAlertRules(controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return;
        setFeed({ status: "ready", items: response.rules.map(toRuleView) });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || isAbortError(cause)) return;
        setFeed({ status: "unavailable", items: [] });
      });
    return () => controller.abort();
  }, []);
  return feed;
}

// ── channels ─────────────────────────────────────────────────────────────────

export interface AlertChannelView {
  channelId: string;
  name: string;
  kind: string;
  minSeverity: string;
  enabled: boolean;
}

export interface AlertChannelsFeed {
  status: AlertsFeedStatus;
  items: AlertChannelView[];
}

function toChannelView(channel: AlertChannel): AlertChannelView {
  return {
    channelId: channel.channel_id,
    name: channel.name,
    kind: channel.kind,
    minSeverity: channel.min_severity,
    enabled: channel.enabled,
  };
}

/** Reads the live alert-channel list; failure is honest `unavailable`. */
export function useAlertChannels(): AlertChannelsFeed {
  const [feed, setFeed] = useState<AlertChannelsFeed>({ status: "loading", items: [] });
  useEffect(() => {
    const controller = new AbortController();
    void listAlertChannels(controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return;
        setFeed({ status: "ready", items: response.channels.map(toChannelView) });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || isAbortError(cause)) return;
        setFeed({ status: "unavailable", items: [] });
      });
    return () => controller.abort();
  }, []);
  return feed;
}
