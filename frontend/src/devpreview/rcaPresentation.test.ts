import { describe, expect, it } from "vitest";

import type { RcaReport } from "../api/evidence-schemas";
import {
  evidencePreviewLines,
  evidenceReferenceMatches,
  mergeEvidenceReferences,
  missingEvidencePresentations,
  missingEvidencePresentation,
  rcaSummaryPresentation,
} from "./rcaPresentation";

const baseReference: RcaReport["supporting_evidence_refs"][number] = {
  source: "metadata",
  name: "current_workload_snapshots",
  check_id: null,
  summary: "workload state",
  query: null,
  evidence_ref: null,
  schema_version: null,
  source_version: null,
  collector: null,
  collector_version: null,
  query_version: null,
  collected_at: null,
  evidence_key: null,
  source_id: null,
  agent_id: null,
  window_start: null,
};

describe("missingEvidencePresentation", () => {
  it.each([
    ["signal:probe_timeout_signal", "probe 응답 시간이 timeoutSeconds를 초과했습니다."],
    ["signal:startup_probe_window_signal", "startup probe 허용 시간과 초기화 시간을 비교해야 합니다."],
    ["signal:app_health_failure_signal", "health endpoint 내부 오류를 확인해야 합니다."],
    ["traces", "Tempo trace query가 아직 완료되지 않았습니다."],
  ])("uses the structured check reason for %s", (item, reason) => {
    const presentation = missingEvidencePresentation(item, [{
      check_id: item === "traces" ? "evidence:traces:required" : item,
      source: item === "traces" ? "traces" : "signals",
      status: "missing",
      reason,
    }]);

    expect(presentation.message).toBe(reason);
    expect(presentation.metadata).toContain("누락");
  });

  it("keeps unknown legacy signal identifiers out of the operator UI", () => {
    const presentation = missingEvidencePresentation("signal:new_internal_check", []);

    expect(presentation.message).toBe("원인 확정에 필요한 추가 진단 신호를 수집해 확인하세요.");
    expect(presentation.message).not.toContain("new_internal_check");
    expect(presentation.metadata).toBeNull();
  });

  it("renders source-qualified legacy refs as a provider-level operator action", () => {
    const presentation = missingEvidencePresentation("traces:related_traces", []);

    expect(presentation.message).toBe("트레이스 근거를 추가로 수집해 확인하세요.");
    expect(presentation.message).not.toContain("traces");
    expect(presentation.message).not.toContain("related_traces");
  });

  it("deduplicates repeated generic signals while preserving a provider action", () => {
    const presentations = missingEvidencePresentations([
      "signal:probe_path_failure_signal",
      "signal:probe_port_failure_signal",
      "signal:probe_timeout_signal",
      "signal:startup_probe_window_signal",
      "traces:related_traces",
    ], []);

    expect(presentations.map((item) => item.message)).toEqual([
      "원인 확정에 필요한 추가 진단 신호를 수집해 확인하세요.",
      "트레이스 근거를 추가로 수집해 확인하세요.",
    ]);
  });
});

describe("mergeEvidenceReferences", () => {
  it("enriches a report ref with the keyed lineage from an old metadata object ref", () => {
    const oldObjectRef =
      "object://evidence/correlation-1.json#metadata:current_workload_snapshots";
    const merged = mergeEvidenceReferences([
      baseReference,
      {
        ...baseReference,
        summary: null,
        evidence_ref: oldObjectRef,
        schema_version: 1,
        source_version: "metadata-v2",
        collector: "cluster-agent",
        collector_version: "2",
        query_version: "1",
        collected_at: "2026-07-24T00:01:00Z",
        evidence_key: "workspace:target:metadata:window-1",
        source_id: "metadata",
        agent_id: "agent-1",
        window_start: "2026-07-24T00:00:30Z",
      },
    ]);

    expect(merged).toHaveLength(1);
    expect(merged[0]).toMatchObject({
      summary: "workload state",
      evidence_ref: oldObjectRef,
      evidence_key: "workspace:target:metadata:window-1",
      collector: "cluster-agent",
      window_start: "2026-07-24T00:00:30Z",
    });
    // Candidate token click resolution must still find the enriched detail row.
    expect(evidenceReferenceMatches(oldObjectRef, merged[0]!)).toBe(true);
  });

  it("keeps separately keyed windows for the same source and evidence name", () => {
    expect(mergeEvidenceReferences([
      { ...baseReference, evidence_key: "window-1" },
      { ...baseReference, evidence_key: "window-2" },
    ])).toHaveLength(2);
  });
});

describe("evidencePreviewLines", () => {
  it("renders actual trace identities instead of an empty preview", () => {
    const lines = evidencePreviewLines("traces", {
      traces: {
        source: "tempo",
        results: {
          cluster_recent_traces: {
            analysis: {
              trace_summaries: [{
                trace_id: "4bf92f3577b34da6a3ce929d0e0e4736",
                service: "checkout",
                operation: "GET /health",
                status: "error",
                duration_ms: 83.4,
              }],
            },
          },
        },
      },
    });

    expect(lines).toEqual([
      "checkout · GET /health · 오류 · 83.4ms · trace 4bf92f3577b3",
    ]);
  });

  it("renders workload identity and readiness from metadata snapshots", () => {
    const lines = evidencePreviewLines("metadata", {
      metadata: {
        change_context: {
          current_workload_snapshots: [{
            workload: {
              kind: "Deployment",
              namespace: "sandbox",
              name: "checkout",
            },
            deployment_status: {
              desired_replicas: 2,
              ready_replicas: 1,
            },
          }],
          referenced_config_objects: [{ kind: "ConfigMap", name: "checkout-config" }],
        },
      },
    });

    expect(lines).toContain("Deployment sandbox/checkout · Ready 1/2");
    expect(lines).toContain("참조 ConfigMap·Secret 1건");
  });
});

describe("rcaSummaryPresentation", () => {
  it("prefers fresh report copy over stale issue timeline copy", () => {
    const copy = rcaSummaryPresentation({
      report: {
        narrativeExecutiveSummary: "새 RCA 상황 요약",
        narrativeRecommendedAction: "새 RCA 권장 조치",
        narrativeReasoning: "새 RCA narrative 근거",
        reason: "새 RCA 판단 근거",
        evidenceSummary: "새 RCA 근거 요약",
        evidenceBundleSummary: "새 RCA 근거 묶음",
        rawAction: "plan_recovery",
      },
      issue: {
        situationSummary: "오래된 타임라인 상황 요약",
        recommendedActionSummary: "오래된 타임라인 권장 조치",
        evidenceSummary: "오래된 타임라인 근거 요약",
        evidenceBundleSummary: "오래된 타임라인 근거 묶음",
      },
      recovery: {
        summary: "복구 플랜 요약",
        candidateDescription: "복구 후보 설명",
        candidateTitle: "복구 후보 제목",
      },
    });

    expect(copy).toEqual({
      situation: "새 RCA 상황 요약",
      recommendedAction: "새 RCA 권장 조치",
      evidence: "새 RCA narrative 근거",
      evidenceBundle: "새 RCA 근거 묶음",
    });
    expect(Object.values(copy)).not.toContain("plan_recovery");
  });

  it("falls back to issue summaries and then recovery copy without rendering raw actions", () => {
    expect(rcaSummaryPresentation({
      report: { rawAction: "plan_recovery" },
      issue: { recommendedActionSummary: "타임라인 권장 조치" },
      recovery: {
        summary: "복구 플랜 요약",
        candidateDescription: "복구 후보 설명",
        candidateTitle: "복구 후보 제목",
      },
    }).recommendedAction).toBe("타임라인 권장 조치");

    const recoveryFallback = rcaSummaryPresentation({
      report: { rawAction: "plan_recovery" },
      issue: {},
      recovery: {
        summary: "복구 플랜 요약",
        candidateDescription: "복구 후보 설명",
        candidateTitle: "복구 후보 제목",
      },
    });
    expect(recoveryFallback.recommendedAction).toBe("복구 후보 설명");
    expect(recoveryFallback.situation).toBe("복구 플랜 요약");
    expect(recoveryFallback.evidence).toBe("복구 플랜 요약");
    expect(Object.values(recoveryFallback)).not.toContain("plan_recovery");
  });
});
