import { describe, expect, it } from "vitest";

import type { AlertEventView } from "./alertsFeed";
import {
  ALERT_RCA_POLL_MS,
  alertIncidentClusterIds,
  alertIncidentPollMs,
  incidentFromAlertEvent,
  promoteAlertIncident,
} from "./alertIncident";
import type { RcaIssueDetailView } from "./rcaDetailFeed";

function alertEvent(overrides: Partial<AlertEventView> = {}): AlertEventView {
  return {
    eventId: "alert-1",
    ruleId: null,
    ruleName: "Readiness probe response failure",
    source: "incident",
    severity: "medium",
    status: "firing",
    cluster: "cluster-1",
    namespace: "sandbox",
    kind: "ReplicaSet",
    name: "game-room-abc",
    firedAt: "2026-07-23T22:37:09Z",
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

function rcaIssue(overrides: Partial<RcaIssueDetailView> = {}): RcaIssueDetailView {
  return {
    correlationId: "correlation-1",
    incidentId: "incident-1",
    currentSubject: "rca.completed",
    clusterId: "cluster-1",
    namespace: "sandbox",
    resourceName: "game-room-abc",
    resourceKind: "ReplicaSet",
    rawSymptom: "Readiness probe response failure",
    symptom: "준비 상태 확인 응답 실패",
    status: "rca_completed",
    severity: "warning",
    rootCause: "probe_path_wrong",
    confidence: 0.92,
    supportingEvidence: ["object://evidence/correlation-1.json#logs:related_logs"],
    missingEvidence: [],
    situationSummary: "준비 상태 확인 경로가 실제 서버 경로와 다릅니다.",
    recommendedActionSummary: "준비 상태 확인 경로를 수정합니다.",
    evidenceSummary: "404 응답이 반복됐습니다.",
    evidenceBundleSummary: "logs, kubernetes",
    actionRoute: "draft_pr",
    prUrl: null,
    errorReason: null,
    updatedAt: "2026-07-23T22:38:09Z",
    ...overrides,
    attemptCount: overrides.attemptCount ?? 1,
    newerAttemptCount: overrides.newerAttemptCount ?? 0,
    latestAttempt: overrides.latestAttempt ?? null,
    recentAttempts: overrides.recentAttempts ?? [],
  };
}

describe("incidentFromAlertEvent", () => {
  it("opens the observed incident while the RCA issue projection catches up", () => {
    expect(incidentFromAlertEvent(alertEvent())).toMatchObject({
      name: "game-room-abc",
      symptom: "준비 상태 확인 응답 실패",
      rawSymptom: "Readiness probe response failure",
      cluster: "cluster-1",
      svc: "game-room-abc",
      ns: "sandbox",
      resourceKind: "ReplicaSet",
      incidentId: "incident-1",
      status: "firing",
      severity: "warning",
    });
    expect(incidentFromAlertEvent(alertEvent()).correlationId).toBeUndefined();
  });

  it("preserves an observed high severity as critical", () => {
    expect(incidentFromAlertEvent(alertEvent({ severity: "high" })).severity).toBe("critical");
  });

  it("polls only the incident cluster until the real correlation arrives", () => {
    const provisional = incidentFromAlertEvent(alertEvent());

    expect(alertIncidentPollMs(provisional)).toBe(ALERT_RCA_POLL_MS);
    expect(alertIncidentClusterIds(provisional, ["cluster-1", "cluster-2"])).toEqual(["cluster-1"]);

    const promoted = promoteAlertIncident(provisional, [rcaIssue()]);
    expect(promoted).toMatchObject({
      incidentId: "incident-1",
      correlationId: "correlation-1",
      rootCause: "probe_path_wrong",
      confidence: 0.92,
    });
    expect(alertIncidentPollMs(promoted)).toBe(0);
    expect(alertIncidentClusterIds(promoted, ["cluster-1", "cluster-2"])).toEqual([]);
    expect(alertIncidentClusterIds(null, ["cluster-1", "cluster-2"])).toEqual([]);
  });

  it("never regresses a promoted RCA when a later issue fetch is empty", () => {
    const promoted = promoteAlertIncident(
      incidentFromAlertEvent(alertEvent()),
      [rcaIssue()],
    );

    expect(promoteAlertIncident(promoted, [])).toBe(promoted);
  });
});
