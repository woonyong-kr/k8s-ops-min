import { AlertTriangle, Bell, BellRing, CheckCircle2, CircleCheck } from "lucide-react";

import { BLUE, HP } from "./theme";
import type { AlertEventView } from "./alertsFeed";

export type AlertPresentationTone = "ok" | "warn" | "crit" | "info";
export type AlertEventIcon = typeof AlertTriangle;
type AlertEventSeverity = AlertEventView["severity"];
type AlertEventStatus = AlertEventView["status"];
type AlertEventSource = AlertEventView["source"];

export interface AlertEventPresentation {
  tone: AlertPresentationTone;
  color: string;
  Icon: AlertEventIcon;
}

const DEFAULT_ALERT_TONE: AlertPresentationTone = "info";

const ALERT_SEVERITY_TONE: Record<AlertEventSeverity, AlertPresentationTone> = {
  critical: "crit",
  high: "crit",
  medium: "warn",
  warning: "warn",
  low: "info",
  info: "info",
};

const ALERT_TONE_COLOR: Record<AlertPresentationTone, string> = {
  ok: HP.ok,
  info: BLUE,
  warn: HP.warn,
  crit: HP.crit,
};

const STATUS_ICON: Partial<Record<AlertEventStatus, AlertEventIcon>> = {
  resolved: CircleCheck,
  acked: CheckCircle2,
};

const STATUS_TONE_OVERRIDE: Partial<Record<AlertEventStatus, AlertPresentationTone>> = {
  resolved: "ok",
};

const NON_INCIDENT_SOURCE_ICON: Partial<Record<AlertEventSource, AlertEventIcon>> = {
  alertmanager: BellRing,
  opsia: Bell,
};

const TONE_RANK: Record<AlertPresentationTone, number> = {
  ok: 0,
  info: 1,
  warn: 2,
  crit: 3,
};

export function alertSeverityTone(severity: string | null | undefined): AlertPresentationTone {
  const normalized = normalizeAlertSeverity(severity);
  return normalized === null ? DEFAULT_ALERT_TONE : ALERT_SEVERITY_TONE[normalized];
}

function alertToneColor(tone: AlertPresentationTone): string {
  return ALERT_TONE_COLOR[tone];
}

export function alertEventPresentation(event: AlertEventView): AlertEventPresentation {
  const tone = STATUS_TONE_OVERRIDE[event.status] ?? alertSeverityTone(event.severity);
  const color = alertToneColor(tone);
  const statusIcon = STATUS_ICON[event.status];

  if (statusIcon !== undefined) {
    return { tone, color, Icon: statusIcon };
  }

  if (hasIncidentId(event)) {
    return { tone, color, Icon: AlertTriangle };
  }

  return { tone, color, Icon: NON_INCIDENT_SOURCE_ICON[event.source] ?? Bell };
}

export function strongestAlertEventPresentation(events: readonly AlertEventView[]): AlertEventPresentation | null {
  let strongest: AlertEventPresentation | null = null;
  let strongestRank = -1;
  for (const event of events) {
    const presentation = alertEventPresentation(event);
    const rank = TONE_RANK[presentation.tone];
    if (rank > strongestRank) {
      strongest = presentation;
      strongestRank = rank;
    }
  }
  return strongest;
}

function hasIncidentId(event: AlertEventView): boolean {
  return event.incidentId !== null;
}

function normalizeAlertSeverity(severity: string | null | undefined): AlertEventSeverity | null {
  const normalized = severity?.trim().toLowerCase();
  if (normalized === undefined || normalized === "") return null;
  return Object.prototype.hasOwnProperty.call(ALERT_SEVERITY_TONE, normalized)
    ? normalized as AlertEventSeverity
    : null;
}
