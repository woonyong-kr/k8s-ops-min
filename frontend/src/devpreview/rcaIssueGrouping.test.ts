import { describe, expect, it } from "vitest";

import type { RcaIssueItem } from "../api/schemas";
import { selectRcaIssueRepresentativeItems } from "./rcaIssuesFeed";

const baseIssue: RcaIssueItem = {
  workspace_id: "workspace-1",
  correlation_id: "correlation-base",
  cluster_id: "cluster-1",
  incident_id: "incident-1",
  incident_namespace: "sandbox",
  incident_resource_kind: "ReplicaSet",
  incident_resource_name: "game-room",
  incident_symptom: "FailedScheduling",
  incident_occurrence_id: "occurrence-1",
  evidence_ref: null,
  current_subject: "rca.completed",
  status: "rca_completed",
  root_cause: "insufficient_cpu",
  confidence: 0.91,
  supporting_evidence: [],
  missing_evidence: [],
  action_route: "safe_pr",
  command_id: null,
  pr_url: null,
  error_reason: null,
  updated_at: "2026-07-24T00:00:00Z",
  issue_severity: "warning",
  severity_availability: "available",
  severity_reason_code: null,
  situation_summary: null,
  recommended_action_summary: null,
  evidence_summary: null,
  evidence_bundle_summary: null,
  recovery_reason_code: null,
};

function issue(overrides: Partial<RcaIssueItem>): RcaIssueItem {
  return { ...baseIssue, ...overrides };
}

describe("selectRcaIssueRepresentativeItems", () => {
  it("keeps the user-pinned recovery correlation as the group representative", () => {
    const representatives = selectRcaIssueRepresentativeItems([
      issue({ correlation_id: "latest", status: "rca_evaluated", updated_at: "2026-07-24T00:03:00Z" }),
      issue({ correlation_id: "pinned", status: "approval_recommended", updated_at: "2026-07-24T00:01:00Z" }),
      issue({ correlation_id: "middle", status: "rca_evaluated", updated_at: "2026-07-24T00:02:00Z" }),
    ], ["pinned"]);

    expect(representatives).toHaveLength(1);
    expect(representatives[0].item.correlation_id).toBe("pinned");
    expect(representatives[0].latestItem.correlation_id).toBe("latest");
    expect(representatives[0].attemptCount).toBe(3);
    expect(representatives[0].newerAttemptCount).toBe(2);
  });

  it("uses the newest correlation when the group is not pinned", () => {
    const representatives = selectRcaIssueRepresentativeItems([
      issue({ correlation_id: "older", updated_at: "2026-07-24T00:01:00Z" }),
      issue({ correlation_id: "latest", updated_at: "2026-07-24T00:02:00Z" }),
    ]);

    expect(representatives[0].item.correlation_id).toBe("latest");
    expect(representatives[0].newerAttemptCount).toBe(0);
  });

  it("lets the newest terminal correlation close the group instead of reviving a pin", () => {
    const representatives = selectRcaIssueRepresentativeItems([
      issue({ correlation_id: "pinned", status: "approval_recommended", updated_at: "2026-07-24T00:01:00Z" }),
      issue({ correlation_id: "resolved", status: "resolved", updated_at: "2026-07-24T00:02:00Z" }),
    ], ["pinned"]);

    expect(representatives[0].item.correlation_id).toBe("resolved");
    expect(representatives[0].newerAttemptCount).toBe(0);
  });

  it("does not revive an old pin after that repair was superseded by a terminal row", () => {
    const representatives = selectRcaIssueRepresentativeItems([
      issue({ correlation_id: "pinned", status: "approval_recommended", updated_at: "2026-07-24T00:01:00Z" }),
      issue({ correlation_id: "resolved", status: "resolved", updated_at: "2026-07-24T00:02:00Z" }),
      issue({ correlation_id: "new-recurrence", status: "rca_evaluated", updated_at: "2026-07-24T00:03:00Z" }),
    ], ["pinned"]);

    expect(representatives[0].item.correlation_id).toBe("new-recurrence");
    expect(representatives[0].newerAttemptCount).toBe(0);
  });

  it("keeps a recurring symptom in a new pin after the previous occurrence completed", () => {
    const representatives = selectRcaIssueRepresentativeItems([
      issue({
        correlation_id: "completed",
        incident_occurrence_id: "occurrence-1",
        status: "incident_resolved",
        updated_at: "2026-07-24T00:02:00Z",
      }),
      issue({
        correlation_id: "recurrence",
        incident_occurrence_id: "occurrence-2",
        status: "incident_detected",
        updated_at: "2026-07-24T00:03:00Z",
      }),
    ]);

    expect(representatives).toHaveLength(2);
    expect(representatives.map(({ item }) => item.correlation_id)).toEqual([
      "recurrence",
      "completed",
    ]);
  });
});
