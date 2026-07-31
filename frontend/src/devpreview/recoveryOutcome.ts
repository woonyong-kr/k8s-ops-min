import type { AuditTimelineItem } from "../api/audit-timeline-schemas";
import { isSafePrRoute } from "./recoveryRoute";

const RESOLVED_ISSUE_STATUSES = new Set([
  "closed",
  "completed",
  "incident_resolved",
  "resolved",
]);

const FAILED_SUBJECTS = new Set([
  "command_rejected",
  "safe_pr_failed",
  "workflow_run_failed",
  "workflow_failed",
]);
const BLOCKED_SUBJECTS = new Set([
  "rca_action_required",
  "rca_followup_required",
]);

export type RecoveryOutcomeNoticeKind =
  | "pull_request_created"
  | "execution_completed"
  | "recovery_completed"
  | "recovery_blocked"
  | "recovery_failed";

export interface RecoveryOutcomeNotice {
  key: string;
  kind: RecoveryOutcomeNoticeKind;
  terminal: boolean;
  tone: "healthy" | "warning" | "critical";
  title: string;
  summary: string;
  detail: string | null;
  prUrl: string | null;
}

export function recoveryOutcomeNotices({
  actionRoute,
  audit,
  issueStatus,
  selectionEventId,
  submittedAt,
}: {
  actionRoute: string;
  audit: readonly AuditTimelineItem[];
  issueStatus?: string | null;
  selectionEventId: string;
  submittedAt: string;
}): RecoveryOutcomeNotice[] {
  const relevant = relevantAuditItems(audit, selectionEventId, submittedAt);
  const notices: RecoveryOutcomeNotice[] = [];
  const blocked = findLast(
    relevant,
    (item) => normalize(item.subject) === "rca_action_required",
  ) ?? findLast(
    relevant,
    (item) => BLOCKED_SUBJECTS.has(normalize(item.subject)),
  );
  if (blocked) {
    const reasonCode = summaryString(blocked, "reason_code");
    notices.push({
      key: blocked.event_id,
      kind: "recovery_blocked",
      terminal: true,
      tone: "critical",
      title: isSafePrRoute(actionRoute)
        ? "복구 PR 생성을 시작할 수 없습니다."
        : "복구 조치를 시작할 수 없습니다.",
      summary: blockerSummary(reasonCode),
      detail: summaryString(blocked, "reason") ?? summaryString(blocked, "summary"),
      prUrl: null,
    });
    return notices;
  }
  const failed = findLast(relevant, (item) => FAILED_SUBJECTS.has(normalize(item.subject)));
  if (failed) {
    const policyRejected = isPolicyRejection(failed);
    notices.push({
      key: failed.event_id,
      kind: "recovery_failed",
      terminal: true,
      tone: "critical",
      title: policyRejected
        ? "복구 명령이 정책 검증에서 거부되었습니다."
        : "복구 조치를 완료하지 못했습니다.",
      summary: failureSummary(failed),
      detail: failureDetail(failed),
      prUrl: null,
    });
    return notices;
  }

  const prCreated = findLast(relevant, (item) => normalize(item.subject) === "safe_pr_created");
  if (prCreated) {
    const prUrl = summaryString(prCreated, "pr_url");
    notices.push({
      key: prCreated.event_id,
      kind: "pull_request_created",
      terminal: false,
      tone: "warning",
      title: "복구 PR 생성이 완료되었습니다.",
      summary: "Kyro가 선택한 복구 조치로 Pull Request를 생성했습니다.",
      detail: "PR을 검토하고 병합한 뒤 실제 운영 상태가 정상화되는지 확인합니다.",
      prUrl,
    });
  }

  const workflowCompleted = findLast(
    relevant,
    (item) => normalize(item.subject) === "workflow_run_completed",
  );
  const rolloutDiagnosed = findLast(
    relevant,
    (item) => normalize(item.subject) === "rollout_diagnosed",
  );
  const commandCompleted = findLast(
    relevant,
    (item) => normalize(item.subject) === "command_completed",
  );
  const executionResult = workflowCompleted ?? rolloutDiagnosed ?? commandCompleted;
  if (executionResult && normalize(actionRoute) === "auto") {
    notices.push({
      key: executionResult.event_id,
      kind: "execution_completed",
      terminal: false,
      tone: "healthy",
      title: "복구 조치 실행이 완료되었습니다.",
      summary: executionSummary(executionResult),
      detail: "실행 완료는 장애 해결 확정과 다릅니다. 운영 상태가 정상화되는지 계속 확인합니다.",
      prUrl: null,
    });
  }

  if (isResolvedIssueStatus(issueStatus)) {
    notices.push({
      key: `resolved:${normalize(issueStatus)}`,
      kind: "recovery_completed",
      terminal: true,
      tone: "healthy",
      title: "복구가 완료되었습니다.",
      summary: "실시간 운영 상태가 정상화되어 이슈를 해결됨으로 전환했습니다.",
      detail: "복구 플랜의 성공 조건은 검증 기준으로 보존되며, 현재 계약에서는 조건별 판정 결과를 따로 제공하지 않습니다.",
      prUrl: prCreated ? summaryString(prCreated, "pr_url") : null,
    });
  }
  return dedupeNotices(notices);
}

export function isResolvedIssueStatus(status?: string | null): boolean {
  return RESOLVED_ISSUE_STATUSES.has(normalize(status));
}

function relevantAuditItems(
  audit: readonly AuditTimelineItem[],
  selectionEventId: string,
  submittedAt: string,
): AuditTimelineItem[] {
  const ordered = [...audit].sort(
    (left, right) => Date.parse(left.created_at) - Date.parse(right.created_at),
  );
  const selectionIndex = ordered.findIndex((item) => item.event_id === selectionEventId);
  if (selectionIndex >= 0) return ordered.slice(selectionIndex + 1);
  const threshold = Date.parse(submittedAt) - 1_000;
  return ordered.filter((item) => {
    const createdAt = Date.parse(item.created_at);
    return Number.isFinite(createdAt) && createdAt >= threshold;
  });
}

function executionSummary(item: AuditTimelineItem): string {
  return summaryString(item, "summary")
    ?? summaryString(item, "diagnosis")
    ?? summaryString(item, "status")
    ?? "백엔드 실행 워크플로가 성공적으로 종료되었습니다.";
}

function failureSummary(item: AuditTimelineItem): string {
  const subject = normalize(item.subject);
  if (subject === "safe_pr_failed") return "복구 PR을 생성하지 못했습니다.";
  if (subject === "command_rejected") return "복구 명령이 정책 또는 실행 조건에 의해 거부되었습니다.";
  return "복구 실행 워크플로가 실패했습니다.";
}

function failureDetail(item: AuditTimelineItem): string | null {
  const reasonCode = summaryString(item, "reason_code");
  const reason = summaryString(item, "reason");
  if (reasonCode === "control_namespace_not_allowed") {
    return "대상 네임스페이스가 클러스터 연결 시 허용한 제어 범위 밖입니다. 클러스터 설정의 제어 네임스페이스를 확인해 주세요.";
  }
  if (reasonCode === "command_action_namespace_not_allowed") {
    return "선택한 복구 액션은 대상 네임스페이스에서 허용되지 않습니다. 허용되는 복구 후보를 선택해 주세요.";
  }
  return reason;
}

function blockerSummary(reasonCode: string | null): string {
  if (reasonCode === "gitops_authority_unavailable") {
    return "저장소·배포 바인딩·승인 스냅샷 연결이 필요합니다.";
  }
  if (reasonCode === "gitops_authority_mismatch") {
    return "복구 대상과 GitOps 배포 정보가 일치하지 않습니다.";
  }
  if (reasonCode === "control_namespace_not_allowed") {
    return "대상 네임스페이스가 제어 허용 범위 밖입니다.";
  }
  if (reasonCode === "command_action_namespace_not_allowed") {
    return "선택한 복구 액션은 대상 네임스페이스에서 허용되지 않습니다.";
  }
  return "복구 조치를 진행하기 위한 추가 설정이나 근거가 필요합니다.";
}

function isPolicyRejection(item: AuditTimelineItem): boolean {
  if (normalize(item.subject) === "command_rejected") return true;
  const reasonCode = summaryString(item, "reason_code");
  return reasonCode === "control_namespace_not_allowed"
    || reasonCode === "command_action_namespace_not_allowed";
}

function summaryString(item: AuditTimelineItem, key: string): string | null {
  const value = item.payload_summary[key];
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function findLast(
  items: readonly AuditTimelineItem[],
  predicate: (item: AuditTimelineItem) => boolean,
): AuditTimelineItem | null {
  for (let index = items.length - 1; index >= 0; index -= 1) {
    if (predicate(items[index])) return items[index];
  }
  return null;
}

function dedupeNotices(notices: readonly RecoveryOutcomeNotice[]): RecoveryOutcomeNotice[] {
  const seen = new Set<RecoveryOutcomeNoticeKind>();
  return notices.filter((notice) => {
    if (seen.has(notice.kind)) return false;
    seen.add(notice.kind);
    return true;
  });
}

function normalize(value?: string | null): string {
  return value?.trim().toLowerCase().replace(/[.\s-]+/gu, "_") ?? "";
}
