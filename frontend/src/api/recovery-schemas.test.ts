import { describe, expect, it } from "vitest";

import { recoveryPlanSchema } from "./recovery-schemas";

function unselectedPlan() {
  return {
    plan_id: "recovery:evidence-1",
    correlation_id: "correlation-1",
    incident_id: "incident-1",
    evidence_ref: "object://evidence/evidence-1.json",
    status: "selection_requested",
    summary: "확정된 원인에 대응하는 복구 조치를 제안합니다.",
    target: {
      cluster_id: "target-1",
      namespace: "sandbox",
      resource_kind: "ReplicaSet",
      resource_name: "canary-room",
    },
    recommended_action_id: "action-1",
    execution_route: "draft_pr",
    selection_required: true,
    candidates: [
      {
        action_id: "action-1",
        title: "리소스 요청값 조정 PR",
        description: "CPU request를 스케줄 가능한 범위로 조정합니다.",
        route: "draft_pr",
        rank: 1,
        score: 0.64,
        risk_level: "medium",
        blast_radius: "target_workload",
        approval_required: true,
        prerequisites: ["현재 request와 노드 allocatable 확인"],
        validation_checks: ["Pod Scheduled 전환"],
        rollback_plan: "변경 commit을 되돌립니다.",
        evidence_refs: ["kubernetes:cluster_resource_state"],
      },
    ],
  };
}

describe("recoveryPlanSchema", () => {
  it("treats omitted selection fields as an unselected recovery plan", () => {
    const parsed = recoveryPlanSchema.parse(unselectedPlan());

    expect(parsed.selected_action_id).toBeNull();
    expect(parsed.selected_by).toBeNull();
    expect(parsed.selected_action).toBeNull();
    expect(parsed.candidates).toHaveLength(1);
  });

  it("preserves explicit null selection fields", () => {
    const parsed = recoveryPlanSchema.parse({
      ...unselectedPlan(),
      selected_action_id: null,
      selected_by: null,
      selected_action: null,
    });

    expect(parsed.selected_action_id).toBeNull();
    expect(parsed.selected_by).toBeNull();
    expect(parsed.selected_action).toBeNull();
  });
});
