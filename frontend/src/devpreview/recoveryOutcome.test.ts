import { describe, expect, it } from "vitest";

import type { AuditTimelineItem } from "../api/audit-timeline-schemas";
import { recoveryOutcomeNotices } from "./recoveryOutcome";

function event(
  eventId: string,
  subject: string,
  payloadSummary: Record<string, unknown> = {},
  createdAt = "2026-07-24T01:00:01Z",
): AuditTimelineItem {
  return {
    event_id: eventId,
    subject,
    source: "test",
    created_at: createdAt,
    causation_id: null,
    journey_stage: subject.startsWith("safe_pr") ? "pr" : "command",
    payload_summary: payloadSummary,
  };
}

describe("recoveryOutcomeNotices", () => {
  it("reports a created PR without claiming the incident is resolved", () => {
    const notices = recoveryOutcomeNotices({
      actionRoute: "safe_pr",
      audit: [
        event("selected", "recovery.action_selected", {}, "2026-07-24T01:00:00Z"),
        event("pr", "safe_pr.created", { pr_url: "https://github.com/kyro/platform/pull/17" }),
      ],
      issueStatus: "recovery_selected",
      selectionEventId: "selected",
      submittedAt: "2026-07-24T01:00:00Z",
    });

    expect(notices).toEqual([
      expect.objectContaining({
        kind: "pull_request_created",
        terminal: false,
        prUrl: "https://github.com/kyro/platform/pull/17",
      }),
    ]);
  });

  it("reports execution completion separately from incident resolution", () => {
    const notices = recoveryOutcomeNotices({
      actionRoute: "auto",
      audit: [
        event("selected", "recovery.action_selected", {}, "2026-07-24T01:00:00Z"),
        event("workflow", "workflow.run.completed", { summary: "rollout health completed" }),
      ],
      issueStatus: "command_completed",
      selectionEventId: "selected",
      submittedAt: "2026-07-24T01:00:00Z",
    });

    expect(notices).toEqual([
      expect.objectContaining({
        kind: "execution_completed",
        terminal: false,
        summary: "rollout health completed",
      }),
    ]);
  });

  it("only reports final recovery completion after the issue resolves", () => {
    const notices = recoveryOutcomeNotices({
      actionRoute: "auto",
      audit: [event("selected", "recovery.action_selected", {}, "2026-07-24T01:00:00Z")],
      issueStatus: "incident_resolved",
      selectionEventId: "selected",
      submittedAt: "2026-07-24T01:00:00Z",
    });

    expect(notices).toEqual([
      expect.objectContaining({
        kind: "recovery_completed",
        terminal: true,
        title: "복구가 완료되었습니다.",
      }),
    ]);
  });

  it("reports backend failures with the recorded reason", () => {
    const notices = recoveryOutcomeNotices({
      actionRoute: "safe_pr",
      audit: [
        event("selected", "recovery.action_selected", {}, "2026-07-24T01:00:00Z"),
        event("failed", "safe_pr.failed", { reason: "GitHub 권한이 없습니다." }),
      ],
      issueStatus: "pr_failed",
      selectionEventId: "selected",
      submittedAt: "2026-07-24T01:00:00Z",
    });

    expect(notices).toEqual([
      expect.objectContaining({
        kind: "recovery_failed",
        terminal: true,
        detail: "GitHub 권한이 없습니다.",
      }),
    ]);
  });

  it("turns a control namespace rejection into an actionable operator message", () => {
    const notices = recoveryOutcomeNotices({
      actionRoute: "auto",
      audit: [
        event("selected", "recovery.action_selected", {}, "2026-07-24T01:00:00Z"),
        event("rejected", "command.rejected", {
          reason_code: "control_namespace_not_allowed",
          reason: "backend wording can change independently",
        }),
      ],
      issueStatus: "command_rejected",
      selectionEventId: "selected",
      submittedAt: "2026-07-24T01:00:00Z",
    });

    expect(notices[0]).toMatchObject({
      title: "복구 명령이 정책 검증에서 거부되었습니다.",
      detail:
      "대상 네임스페이스가 클러스터 연결 시 허용한 제어 범위 밖입니다. 클러스터 설정의 제어 네임스페이스를 확인해 주세요.",
    });
  });

  it("explains a control policy blocker emitted before selection", () => {
    const notices = recoveryOutcomeNotices({
      actionRoute: "auto",
      audit: [
        event("blocked", "rca.action_required", {
          reason_code: "control_namespace_not_allowed",
          reason: "target 네임스페이스는 현재 클러스터 제어 허용 범위에 포함되지 않습니다.",
        }),
      ],
      issueStatus: "selection_required",
      selectionEventId: "not-persisted",
      submittedAt: "2026-07-24T01:00:00Z",
    });

    expect(notices[0]).toMatchObject({
      kind: "recovery_blocked",
      title: "복구 조치를 시작할 수 없습니다.",
      summary: "대상 네임스페이스가 제어 허용 범위 밖입니다.",
      detail: "target 네임스페이스는 현재 클러스터 제어 허용 범위에 포함되지 않습니다.",
    });
  });

  it("reports an authority blocker instead of leaving PR creation pending", () => {
    const notices = recoveryOutcomeNotices({
      actionRoute: "safe_pr",
      audit: [
        event("selected", "recovery.action_selected", {}, "2026-07-24T01:00:00Z"),
        event("blocked", "rca.action_required", {
          reason_code: "gitops_authority_unavailable",
          reason: "승인 snapshot·binding·repository 권위 context를 확보하지 못했습니다.",
        }),
      ],
      issueStatus: "recovery_selected",
      selectionEventId: "selected",
      submittedAt: "2026-07-24T01:00:00Z",
    });

    expect(notices).toEqual([
      expect.objectContaining({
        kind: "recovery_blocked",
        terminal: true,
        title: "복구 PR 생성을 시작할 수 없습니다.",
        summary: "저장소·배포 바인딩·승인 스냅샷 연결이 필요합니다.",
        detail: "승인 snapshot·binding·repository 권위 context를 확보하지 못했습니다.",
      }),
    ]);
  });
});
