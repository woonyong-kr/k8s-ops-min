import { describe, expect, it } from "vitest";

import {
  alertEventOccurrenceKey,
  isIncidentNotification,
  type AlertEventView,
} from "./alertsFeed";

function alert(
  changes: Partial<AlertEventView> = {},
): AlertEventView {
  return {
    eventId: "ale-inc-1",
    ruleId: null,
    ruleName: "CrashLoopBackOff",
    source: "incident",
    severity: "high",
    status: "firing",
    cluster: "game-server",
    namespace: "sandbox",
    kind: "Pod",
    name: "demo-game-abc",
    firedAt: "2026-07-23T05:00:00Z",
    resolvedAt: null,
    observedValue: null,
    threshold: null,
    evidence: [],
    incidentId: "incident-1",
    acknowledgedAt: null,
    acknowledgedBy: null,
    promotedAt: null,
    promotedBy: null,
    ...changes,
  };
}

describe("isIncidentNotification", () => {
  it("notifies for confirmed internal and Alertmanager incidents", () => {
    expect(isIncidentNotification(alert())).toBe(true);
    expect(isIncidentNotification(alert({ source: "alertmanager" }))).toBe(true);
  });

  it("does not notify for rules, resolved events, or unconfirmed alerts", () => {
    expect(isIncidentNotification(alert({ source: "opsia" }))).toBe(false);
    expect(isIncidentNotification(alert({ status: "resolved" }))).toBe(false);
    expect(isIncidentNotification(alert({ incidentId: null }))).toBe(false);
  });
});

describe("alertEventOccurrenceKey", () => {
  it("does not let a read alert hide a new incident reusing the source event id", () => {
    const first = alert({ eventId: "ale-am-stable", incidentId: "incident-old" });
    const reopened = alert({ eventId: "ale-am-stable", incidentId: "incident-new" });

    expect(alertEventOccurrenceKey(first)).not.toBe(alertEventOccurrenceKey(reopened));
  });
});
