import { describe, expect, it } from "vitest";

import { issueAnalysisState } from "./issueAnalysisState";

describe("issueAnalysisState", () => {
  it("keeps analysis in progress when recovery advances without a root cause", () => {
    expect(issueAnalysisState({ status: "pr_requested", rootCause: null })).toEqual({
      label: "분석 중",
      tone: "info",
    });
    expect(issueAnalysisState({ status: "recovery_planned", rootCause: null })).toEqual({
      label: "분석 중",
      tone: "info",
    });
  });

  it("marks analysis complete only when a root cause is present", () => {
    expect(issueAnalysisState({
      status: "rca_completed",
      rootCause: "Readiness probe 설정 오류",
    })).toEqual({
      label: "원인 분석 완료",
      tone: "ok",
    });
  });

  it("keeps a ranked cause provisional when the report is blocked", () => {
    expect(issueAnalysisState({
      status: "recovery_planned",
      rootCause: "upstream_unavailable",
      analysisStatus: "blocked",
    })).toEqual({
      label: "추가 근거 필요",
      tone: "warn",
    });
  });

  it("does not treat an empty root cause as complete", () => {
    expect(issueAnalysisState({ status: "rca_completed", rootCause: "  " })).toEqual({
      label: "분석 중",
      tone: "info",
    });
    expect(issueAnalysisState({
      status: "rca_completed",
      rootCause: "insufficient_evidence",
    })).toEqual({
      label: "분석 중",
      tone: "info",
    });
    expect(issueAnalysisState({
      status: "rca_completed",
      rootCause: "원인 미확인",
    })).toEqual({
      label: "분석 중",
      tone: "info",
    });
  });

  it("keeps the resolved state independent from RCA data", () => {
    expect(issueAnalysisState({ status: "resolved", rootCause: null })).toEqual({
      label: "해결됨",
      tone: "ok",
    });
  });
});
