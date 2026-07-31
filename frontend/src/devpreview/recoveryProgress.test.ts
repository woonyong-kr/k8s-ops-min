import { describe, expect, it } from "vitest";

import type { RecoveryPlan } from "../api/recovery-schemas";
import {
  currentRecoveryAttemptPrUrl,
  recoveryDisplayedStep,
  recoveryProgressPercent,
  recoveryProgressState,
  withCreatedPullRequest,
} from "./recoveryProgress";

describe("recoveryProgressState", () => {
  it("keeps recovery at zero while RCA is still running", () => {
    expect(recoveryProgressState({ status: "rca_in_progress" })).toMatchObject({
      phase: "waiting",
      label: "복구 대기",
      step: 0,
    });
  });

  it("keeps recovery waiting until an action is selected", () => {
    expect(recoveryProgressState({ status: "rca_completed" })).toMatchObject({
      phase: "waiting",
      label: "복구 대기",
      step: 0,
    });
  });

  it("does not treat a recommended PR action as a submitted request", () => {
    const progress = recoveryProgressState({
      status: "approval_recommended",
      actionRoute: "draft_pr",
    });

    expect(progress).toMatchObject({
      phase: "waiting",
      label: "복구 대기",
      step: 0,
    });
    expect(recoveryDisplayedStep(progress)).toBe(0);
  });

  it("moves to submission when a candidate is selected", () => {
    expect(recoveryProgressState({ status: "rca_completed", selectionAccepted: true })).toMatchObject({
      phase: "submitting",
      label: "복구 요청됨",
      step: 1,
    });
  });

  it("does not expose an internal approval state after selection", () => {
    const progress = recoveryProgressState({
      status: "rca_completed",
      actionRoute: "approval_required",
      selectionAccepted: true,
    });
    expect(progress).toMatchObject({
      phase: "submitting",
      label: "복구 요청됨",
      step: 1,
    });
    expect(recoveryDisplayedStep(progress)).toBe(2);
  });

  it("names the submitted route instead of using one generic request label", () => {
    expect(recoveryProgressState({
      actionRoute: "auto",
      selectionAccepted: true,
    })).toMatchObject({ label: "자동 복구 요청됨", step: 1 });
    expect(recoveryProgressState({
      actionRoute: "draft_pr",
      selectionAccepted: true,
    })).toMatchObject({ label: "PR 생성 요청됨", step: 1 });
  });

  it("derives execution and verification from issue status", () => {
    expect(recoveryProgressState({ status: "command_dispatched" })).toMatchObject({
      phase: "executing",
      step: 2,
    });
    expect(recoveryProgressState({ status: "command_completed" })).toMatchObject({
      phase: "verifying",
      step: 3,
    });
  });

  it("shows durable Safe PR lifecycle states without claiming completion", () => {
    expect(recoveryProgressState({ status: "pr_open" })).toMatchObject({
      phase: "approval",
      label: "PR 검토 필요",
    });
    expect(recoveryProgressState({ status: "deploy_pending" })).toMatchObject({
      phase: "executing",
      label: "복구 배포 중",
    });
    expect(recoveryProgressState({ status: "verification_pending" })).toMatchObject({
      phase: "verifying",
      label: "안정화 검증 중",
    });
    expect(recoveryProgressState({ status: "failed" })).toMatchObject({
      phase: "failed",
      label: "복구 실패",
    });
  });

  it("uses lifecycle events when a snapshot status has not refreshed yet", () => {
    expect(recoveryProgressState({
      audit: [{
        event_id: "verification-started",
        subject: "recovery.verification.started",
        source: "rca-feedback-worker",
        created_at: "2026-07-24T01:00:00Z",
        causation_id: null,
        journey_stage: "recovery",
        payload_summary: {},
      }],
    })).toMatchObject({
      phase: "verifying",
      label: "안정화 검증 중",
    });
  });

  it("marks resolved incidents complete", () => {
    const progress = recoveryProgressState({ status: "incident_resolved" });
    expect(progress).toMatchObject({
      phase: "completed",
      label: "복구 완료",
      step: 5,
    });
    expect(recoveryDisplayedStep(progress)).toBe(5);
  });

  it("keeps a rejected command at the pre-execution policy stage", () => {
    expect(recoveryProgressState({ status: "command_rejected" })).toMatchObject({
      phase: "blocked",
      label: "정책 검증에서 거부",
      step: 1,
    });
  });

  it("shows a legacy control rejection at the policy stage with the true latest event", () => {
    expect(recoveryProgressState({
      status: "command_rejected",
      selectionFailed: true,
      audit: [
        {
          event_id: "old-evidence",
          subject: "cluster.evidence.received",
          source: "cluster-agent",
          created_at: "2026-07-24T01:00:00Z",
          causation_id: null,
          journey_stage: "evidence",
          payload_summary: {},
        },
        {
          event_id: "selected",
          subject: "recovery.action_selected",
          source: "api-gateway",
          created_at: "2026-07-24T01:01:00Z",
          causation_id: null,
          journey_stage: "recovery",
          payload_summary: {},
        },
        {
          event_id: "rejected",
          subject: "command.rejected",
          source: "command-worker",
          created_at: "2026-07-24T01:02:00Z",
          causation_id: "selected",
          journey_stage: "recovery",
          payload_summary: {
            reason_code: "control_namespace_not_allowed",
            reason: "namespace is not allowed by control policy",
          },
        },
      ],
    })).toMatchObject({
      phase: "blocked",
      label: "정책 검증에서 거부",
      step: 1,
      tone: "failed",
      latestEvent: { event_id: "rejected", subject: "command.rejected" },
    });
  });

  it("shows a preflight policy blocker before command submission", () => {
    expect(recoveryProgressState({
      actionRoute: "auto",
      selectionFailed: true,
      audit: [{
        event_id: "policy-blocked",
        subject: "rca.action_required",
        source: "api-gateway",
        created_at: "2026-07-24T01:00:00Z",
        causation_id: null,
        journey_stage: "recovery",
        payload_summary: {
          reason_code: "control_namespace_not_allowed",
          reason: "target 네임스페이스는 현재 클러스터 제어 허용 범위에 포함되지 않습니다.",
        },
      }],
    })).toMatchObject({
      phase: "blocked",
      label: "정책 검증에서 거부",
      step: 1,
      latestEvent: { event_id: "policy-blocked" },
    });
  });

  it("stops the bar at the center of the active stage marker", () => {
    expect(recoveryProgressPercent(
      recoveryProgressState({ status: "rca_completed" }),
    )).toBe(0);
    expect(recoveryProgressPercent(
      recoveryProgressState({ status: "rca_completed", selectionAccepted: true }),
    )).toBe(30);
    expect(recoveryProgressPercent(
      recoveryProgressState({ status: "incident_resolved" }),
    )).toBe(90);
  });

  it("uses the projected blocker reason before the audit feed refreshes", () => {
    expect(recoveryProgressState({
      actionRoute: "safe_pr",
      selectionAccepted: true,
      reasonCode: "gitops_authority_unavailable",
    })).toMatchObject({
      phase: "blocked",
      label: "추가 설정 필요",
      step: 1,
      tone: "failed",
    });
  });

  it("lets a backend authority blocker override the accepted selection", () => {
    expect(recoveryProgressState({
      actionRoute: "safe_pr",
      selectionAccepted: true,
      audit: [{
        event_id: "blocked",
        subject: "rca.action_required",
        source: "dispatch-worker",
        created_at: "2026-07-24T01:00:01Z",
        causation_id: null,
        journey_stage: "recovery",
        payload_summary: {
          reason_code: "gitops_authority_unavailable",
          reason: "승인 snapshot·binding·repository 권위 context를 확보하지 못했습니다.",
        },
      }],
    })).toMatchObject({
      phase: "blocked",
      label: "추가 설정 필요",
      step: 1,
      tone: "failed",
    });
  });

  it("ignores a blocker from an older recovery attempt after a new selection", () => {
    expect(recoveryProgressState({
      actionRoute: "safe_pr",
      selectionAccepted: true,
      audit: [
        {
          event_id: "old-blocker",
          subject: "rca.action_required",
          source: "dispatch-worker",
          created_at: "2026-07-24T01:00:00Z",
          causation_id: null,
          journey_stage: "recovery",
          payload_summary: {
            reason_code: "gitops_authority_unavailable",
            reason: "old attempt failed",
          },
        },
        {
          event_id: "new-selection",
          subject: "recovery.action_selected",
          source: "api-gateway",
          created_at: "2026-07-24T01:01:00Z",
          causation_id: null,
          journey_stage: "recovery",
          payload_summary: {},
        },
      ],
    })).toMatchObject({
      phase: "submitting",
      label: "PR 생성 요청됨",
      latestEvent: { event_id: "new-selection" },
    });
  });

  it("applies only a blocker emitted after the latest recovery selection", () => {
    expect(recoveryProgressState({
      actionRoute: "safe_pr",
      selectionAccepted: true,
      audit: [
        {
          event_id: "old-blocker",
          subject: "rca.action_required",
          source: "dispatch-worker",
          created_at: "2026-07-24T01:00:00Z",
          causation_id: null,
          journey_stage: "recovery",
          payload_summary: {
            reason_code: "gitops_authority_unavailable",
            reason: "old attempt failed",
          },
        },
        {
          event_id: "new-selection",
          subject: "recovery.action_selected",
          source: "api-gateway",
          created_at: "2026-07-24T01:01:00Z",
          causation_id: null,
          journey_stage: "recovery",
          payload_summary: {},
        },
        {
          event_id: "new-blocker",
          subject: "rca.action_required",
          source: "dispatch-worker",
          created_at: "2026-07-24T01:02:00Z",
          causation_id: "new-selection",
          journey_stage: "recovery",
          payload_summary: {
            reason_code: "gitops_authority_mismatch",
            reason: "new attempt failed",
          },
        },
      ],
    })).toMatchObject({
      phase: "blocked",
      label: "추가 설정 필요",
      latestEvent: { event_id: "new-blocker" },
    });
  });

  it("uses workflow terminal events without claiming the incident is resolved", () => {
    expect(recoveryProgressState({
      audit: [{
        event_id: "workflow-completed",
        subject: "workflow.run.completed",
        source: "workflow-controller",
        created_at: "2026-07-24T01:00:00Z",
        causation_id: null,
        journey_stage: "workflow",
        payload_summary: { summary: "rollout health completed" },
      }],
    })).toMatchObject({
      phase: "verifying",
      label: "검증 중",
      step: 3,
    });

    expect(recoveryProgressState({
      audit: [{
        event_id: "workflow-failed",
        subject: "workflow.run.failed",
        source: "workflow-controller",
        created_at: "2026-07-24T01:00:00Z",
        causation_id: null,
        journey_stage: "workflow",
        payload_summary: { reason: "health check failed" },
      }],
    })).toMatchObject({
      phase: "failed",
      label: "복구 실패",
      step: 3,
    });
  });

  it("keeps the PR reference without overwriting a completed recovery", () => {
    const completed = recoveryProgressState({ status: "incident_resolved" });

    expect(withCreatedPullRequest(
      completed,
      "https://github.com/kyro/platform/pull/17",
      "PR 검토 필요",
    )).toBe(completed);
  });

  it("marks a created PR as ready for review before recovery completes", () => {
    const requested = recoveryProgressState({
      status: "recovery_selected",
      actionRoute: "safe_pr",
      selectionAccepted: true,
    });

    expect(withCreatedPullRequest(
      requested,
      "https://github.com/kyro/platform/pull/17",
      "PR 생성됨",
    )).toMatchObject({
      phase: "verifying",
      label: "PR 생성됨",
      step: 3,
      tone: "approval",
    });
  });

  it("does not let an old PR URL overwrite deploy or stabilization truth", () => {
    const deploying = recoveryProgressState({ status: "deploy_pending" });
    expect(withCreatedPullRequest(
      deploying,
      "https://github.com/kyro/platform/pull/17",
      "PR 생성됨",
    )).toBe(deploying);
  });
});

describe("currentRecoveryAttemptPrUrl", () => {
  const planWithLifecycle = (
    lifecycle: Record<string, unknown>,
  ): RecoveryPlan => ({
    lifecycle,
  } as unknown as RecoveryPlan);
  const plan = planWithLifecycle({
      attempt: { id: "attempt-2", number: 2 },
      pr: {
        url: "https://github.com/kyro/platform/pull/22",
        attempt_id: "attempt-2",
      },
  });

  it("returns only the PR bound to the current attempt", () => {
    expect(currentRecoveryAttemptPrUrl(plan)).toBe(
      "https://github.com/kyro/platform/pull/22",
    );
  });

  it("does not reuse a PR from an older attempt", () => {
    expect(currentRecoveryAttemptPrUrl(
      planWithLifecycle({
        attempt: { id: "attempt-3", number: 3 },
        pr: {
          url: "https://github.com/kyro/platform/pull/22",
          attempt_id: "attempt-2",
        },
      }),
    )).toBeNull();
  });

  it("does not fall back to an unscoped legacy URL", () => {
    expect(currentRecoveryAttemptPrUrl(
      planWithLifecycle({
        pr: { url: "https://github.com/kyro/platform/pull/17" },
      }),
    )).toBeNull();
  });
});
