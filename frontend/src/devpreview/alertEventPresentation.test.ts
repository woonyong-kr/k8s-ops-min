import { describe, expect, it } from "vitest";
import { AlertTriangle, Bell, BellRing, CheckCircle2, CircleCheck } from "lucide-react";

import { alertEventPresentation, alertSeverityTone, strongestAlertEventPresentation } from "./alertEventPresentation";
import type { AlertEventView } from "./alertsFeed";
import { BLUE, HP } from "./theme";

function alert(changes: Partial<AlertEventView> = {}): AlertEventView {
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

describe("alertEventPresentation", () => {
  it("keeps high incidents red with the warning icon for toast, bell, and alerts consumers", () => {
    const event = alert();
    const presentation = alertEventPresentation(event);

    expect(presentation.tone).toBe("crit");
    expect(presentation.color).toBe(HP.crit);
    expect(presentation.Icon).toBe(AlertTriangle);
    expect(alertSeverityTone(event.severity)).toBe("crit");
    expect(strongestAlertEventPresentation([event])).toMatchObject({
      tone: "crit",
      color: HP.crit,
      Icon: AlertTriangle,
    });
  });

  it("lets resolved and acknowledged status choose status-specific icons and severity colors", () => {
    expect(alertEventPresentation(alert({ status: "resolved", severity: "critical" }))).toMatchObject({
      tone: "ok",
      color: HP.ok,
      Icon: CircleCheck,
    });

    expect(alertEventPresentation(alert({ status: "acked", severity: "warning" }))).toMatchObject({
      tone: "warn",
      color: HP.warn,
      Icon: CheckCircle2,
    });

    expect(alertEventPresentation(alert({ status: "acked", severity: "info" }))).toMatchObject({
      tone: "info",
      color: BLUE,
      Icon: CheckCircle2,
    });

    expect(alertEventPresentation(alert({ status: "acked", severity: "high" }))).toMatchObject({
      tone: "crit",
      color: HP.crit,
      Icon: CheckCircle2,
    });

    expect(alertEventPresentation(alert({ status: "acked", severity: "medium" }))).toMatchObject({
      tone: "warn",
      color: HP.warn,
      Icon: CheckCircle2,
    });
  });

  it("uses incident icons with severity colors for all firing incident levels", () => {
    expect(alertEventPresentation(alert({ severity: "critical" }))).toMatchObject({
      tone: "crit",
      color: HP.crit,
      Icon: AlertTriangle,
    });

    expect(alertEventPresentation(alert({ severity: "warning" }))).toMatchObject({
      tone: "warn",
      color: HP.warn,
      Icon: AlertTriangle,
    });

    expect(alertEventPresentation(alert({ severity: "low" }))).toMatchObject({
      tone: "info",
      color: BLUE,
      Icon: AlertTriangle,
    });

    expect(alertEventPresentation(alert({ incidentId: "" }))).toMatchObject({
      tone: "crit",
      color: HP.crit,
      Icon: AlertTriangle,
    });
  });

  it("uses source icons for firing non-incident alerts", () => {
    expect(alertEventPresentation(alert({
      source: "alertmanager",
      severity: "high",
      incidentId: null,
    }))).toMatchObject({
      tone: "crit",
      color: HP.crit,
      Icon: BellRing,
    });

    expect(alertEventPresentation(alert({
      source: "opsia",
      severity: "warning",
      incidentId: null,
    }))).toMatchObject({
      tone: "warn",
      color: HP.warn,
      Icon: Bell,
    });
  });

  it("selects the strongest alert presentation for the bell badge", () => {
    expect(strongestAlertEventPresentation([
      alert({ eventId: "info", severity: "info", incidentId: null, source: "opsia" }),
      alert({ eventId: "warn", severity: "warning", incidentId: null, source: "opsia" }),
      alert({ eventId: "high", severity: "high" }),
    ])).toMatchObject({
      tone: "crit",
      color: HP.crit,
      Icon: AlertTriangle,
    });

    expect(strongestAlertEventPresentation([
      alert({ eventId: "resolved", status: "resolved", severity: "critical" }),
      alert({ eventId: "info", severity: "info", incidentId: null, source: "opsia" }),
    ])).toMatchObject({
      tone: "info",
      color: BLUE,
      Icon: Bell,
    });
  });
});
