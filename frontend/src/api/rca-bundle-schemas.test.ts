import { describe, expect, it } from "vitest";

import { remediationBundleResponseSchema } from "./rca-bundle-schemas";

function candidate() {
  return {
    action_id: "action-1",
    title: "메모리 제한 조정",
    description: "관측된 메모리 사용량에 맞춰 제한을 조정합니다.",
    draft: {
      action_type: "patch_workload",
      namespace: "target",
      resource_kind: "StatefulSet",
      resource_name: "tempo",
      reason: "OOMKilled 재발 방지",
      risk_level: "medium",
      dry_run: true,
      source_evidence: ["metrics:telemetry_metrics"],
      params: { memory_limit: "1Gi" },
    },
    route: "draft_pr",
    rank: 1,
    score: 0.9,
    risk_level: "medium",
    blast_radius: "target_workload",
    approval_required: true,
    prerequisites: ["현재 workload manifest 확인"],
    validation_checks: ["OOMKilled 재발 없음"],
    rollback_plan: "이전 메모리 제한으로 복원합니다.",
    evidence_refs: ["metrics:telemetry_metrics"],
    recommendation_reason: "원인에 직접 대응하는 최소 변경입니다.",
    expected_outcome: "Pod가 안정적으로 Ready 상태를 유지합니다.",
    risk_explanation: "재시작이 한 차례 발생할 수 있습니다.",
    rollback_reason: "메모리 사용량이 개선되지 않으면 복원합니다.",
  };
}

function bundle(recoveryCandidate: ReturnType<typeof candidate>) {
  return {
    meta: {
      correlation_id: "correlation-1",
      incident_id: "incident-1",
      cluster_id: "target-1",
      workspace_id: "default",
      created_at: "2026-07-24T00:00:00Z",
    },
    diagnosis: {
      root_cause: "oom_killed",
      confidence: 0.9,
      supporting_evidence: [],
      missing_evidence: [],
      supporting_evidence_refs: [],
      missing_evidence_checks: [],
      selected_candidate_id: "oom_killed",
    },
    remediation: {
      status: "selection_requested",
      selected_action_id: null,
      selected_by: null,
      candidates: [recoveryCandidate],
      evidence_ref: "object://evidence/correlation-1.json",
    },
  };
}

describe("remediationBundleRecoveryCandidateSchema", () => {
  it("preserves the canonical recovery explanation fields", () => {
    const source = candidate();

    const parsed = remediationBundleResponseSchema.parse(bundle(source));
    const parsedCandidate = parsed.remediation?.candidates[0];

    expect(parsedCandidate?.recommendation_reason).toBe(source.recommendation_reason);
    expect(parsedCandidate?.expected_outcome).toBe(source.expected_outcome);
    expect(parsedCandidate?.risk_explanation).toBe(source.risk_explanation);
    expect(parsedCandidate?.rollback_reason).toBe(source.rollback_reason);
  });

  it("accepts legacy candidates that omit the explanation fields", () => {
    const source = candidate();
    const {
      recommendation_reason: _recommendationReason,
      expected_outcome: _expectedOutcome,
      risk_explanation: _riskExplanation,
      rollback_reason: _rollbackReason,
      ...legacy
    } = source;

    const parsed = remediationBundleResponseSchema.parse(bundle(legacy as ReturnType<typeof candidate>));

    expect(parsed.remediation?.candidates[0]?.recommendation_reason).toBeUndefined();
  });

  it("rejects unknown candidate fields", () => {
    const withInternalField = {
      ...candidate(),
      internal_signal: "must-not-leak",
    };

    expect(
      remediationBundleResponseSchema.safeParse(bundle(withInternalField)),
    ).toMatchObject({ success: false });
  });
});
