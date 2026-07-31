export type IssueAnalysisState = {
  label: "분석 중" | "추가 근거 필요" | "원인 분석 완료" | "해결됨";
  tone: "info" | "warn" | "ok";
};

const RESOLVED_STATUSES = new Set([
  "cancelled",
  "closed",
  "completed",
  "dismissed",
  "incident_resolved",
  "resolved",
]);

const UNCONFIRMED_ROOT_CAUSES = new Set([
  "",
  "unknown",
  "insufficient_evidence",
  "none",
  "미확인",
  "원인 미확인",
]);

export function hasConfirmedRootCause(value: string | null | undefined): boolean {
  return !UNCONFIRMED_ROOT_CAUSES.has((value ?? "").trim().toLocaleLowerCase());
}

export function issueAnalysisState(issue: {
  status?: string | null;
  rootCause?: string | null;
  analysisStatus?: "completed" | "blocked" | null;
}): IssueAnalysisState {
  const normalizedStatus = issue.status?.trim().toLowerCase() ?? "";
  if (RESOLVED_STATUSES.has(normalizedStatus)) {
    return { label: "해결됨", tone: "ok" };
  }
  if (issue.analysisStatus === "blocked" || normalizedStatus === "analysis_blocked") {
    return { label: "추가 근거 필요", tone: "warn" };
  }
  if (hasConfirmedRootCause(issue.rootCause)) {
    return { label: "원인 분석 완료", tone: "ok" };
  }
  return { label: "분석 중", tone: "info" };
}
