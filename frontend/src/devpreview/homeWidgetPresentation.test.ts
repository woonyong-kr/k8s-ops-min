import { describe, expect, it } from "vitest";

import type { AlertEventView } from "./alertsFeed";
import type { ApplicationView } from "./deployFeed";
import {
  activeAlertRows,
  applicationAttentionRows,
  applicationHealthItems,
} from "./homeWidgetPresentation";

function alert(overrides: Partial<AlertEventView> = {}): AlertEventView {
  return {
    eventId: "event-1",
    ruleId: null,
    ruleName: "Replica availability",
    source: "alertmanager",
    severity: "medium",
    status: "firing",
    cluster: "management-server",
    namespace: "management",
    kind: "Deployment",
    name: "api-gateway",
    firedAt: "2026-07-25T03:00:00+09:00",
    resolvedAt: null,
    observedValue: null,
    threshold: null,
    evidence: [],
    incidentId: "incident-1",
    acknowledgedAt: null,
    acknowledgedBy: null,
    promotedAt: null,
    promotedBy: null,
    ...overrides,
  };
}

function application(overrides: Partial<ApplicationView> = {}): ApplicationView {
  return {
    id: "app-1",
    name: "demo",
    environments: ["dev"],
    lifecycleStatus: "active",
    repositoryRef: "org/repo",
    defaultBranch: "main",
    manifestPath: "deploy",
    healthStatus: "healthy",
    deliveryStatus: "synced",
    deliveryAvailability: "observed",
    workflowRunId: null,
    deliveryObservedAt: null,
    ...overrides,
  };
}

describe("activeAlertRows", () => {
  it("keeps firing alerts, orders newest first, and maps severity tone", () => {
    expect(activeAlertRows([
      alert({ eventId: "older", firedAt: "2026-07-25T01:00:00+09:00" }),
      alert({ eventId: "resolved", status: "resolved" }),
      alert({
        eventId: "critical",
        firedAt: "2026-07-25T04:00:00+09:00",
        severity: "critical",
      }),
    ])).toEqual([
      expect.objectContaining({ id: "critical", tone: "crit", right: "심각" }),
      expect.objectContaining({ id: "older", tone: "warn", right: "중간" }),
    ]);
  });

  it("honors the row limit", () => {
    expect(activeAlertRows([
      alert({ eventId: "one" }),
      alert({ eventId: "two" }),
    ], 1)).toHaveLength(1);
  });
});

describe("applicationHealthItems", () => {
  it("separates healthy, degraded, and unknown observations", () => {
    expect(applicationHealthItems([
      application({ healthStatus: "healthy" }),
      application({ id: "degraded", healthStatus: "degraded" }),
      application({ id: "unknown", healthStatus: "unknown" }),
      application({ id: "missing", healthStatus: null }),
    ], { ok: "green", warn: "orange", unknown: "gray" })).toEqual([
      { label: "정상", value: 1, color: "green" },
      { label: "주의", value: 1, color: "orange" },
      { label: "관측 안 됨", value: 2, color: "gray" },
    ]);
  });
});

describe("applicationAttentionRows", () => {
  it("puts degraded applications first and preserves exact application ids", () => {
    expect(applicationAttentionRows([
      application({ id: "healthy", name: "healthy-app" }),
      application({ id: "unknown", name: "unknown-app", healthStatus: "unknown" }),
      application({ id: "degraded", name: "degraded-app", healthStatus: "degraded" }),
    ])).toEqual([
      expect.objectContaining({ id: "degraded", tone: "crit", right: "주의" }),
      expect.objectContaining({ id: "unknown", tone: "warn", right: "미관측" }),
      expect.objectContaining({ id: "healthy", tone: "ok", right: "정상" }),
    ]);
  });
});
