import type { AuditTimelineItem } from "../api/audit-timeline-schemas";
import type { RecoveryPlan } from "../api/recovery-schemas";

export type RecoveryProgressPhase =
  | "waiting"
  | "approval"
  | "submitting"
  | "executing"
  | "verifying"
  | "blocked"
  | "completed"
  | "failed";

export interface RecoveryProgressState {
  phase: RecoveryProgressPhase;
  label: "복구 대기" | "자동 복구 요청됨" | "PR 생성 요청됨" | "복구 요청됨" | "복구 실행 중" | "복구 배포 중" | "검증 중" | "안정화 검증 중" | "PR 생성됨" | "PR 검토 필요" | "추가 설정 필요" | "정책 검증에서 거부" | "복구 완료" | "복구 실패";
  step: number;
  tone: "waiting" | "approval" | "active" | "completed" | "failed";
  latestEvent: AuditTimelineItem | null;
}

export interface RecoveryProgressOverride {
  actionRoute: string | null;
  selectionPending: boolean;
  selectionAccepted: boolean;
  selectionFailed: boolean;
  reasonCode: string | null;
}

function lifecycleObject(value: unknown): Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value)
    ? value as Record<string, unknown>
    : {};
}

/** Returns only the PR durably bound to the plan's current selection attempt. */
export function currentRecoveryAttemptPrUrl(
  plan: RecoveryPlan | null | undefined,
): string | null {
  const lifecycle = lifecycleObject(plan?.lifecycle);
  const attempt = lifecycleObject(lifecycle.attempt);
  const pr = lifecycleObject(lifecycle.pr);
  const attemptId = typeof attempt.id === "string" ? attempt.id.trim() : "";
  const prAttemptId = typeof pr.attempt_id === "string" ? pr.attempt_id.trim() : "";
  const url = typeof pr.url === "string" ? pr.url.trim() : "";
  return attemptId && prAttemptId === attemptId && url ? url : null;
}

export function recoveryDisplayedStep(progress: RecoveryProgressState): number {
  if (progress.phase === "waiting") return 0;
  if (progress.phase === "completed") return 5;
  return Math.min(5, progress.step + 1);
}

/** Aligns the continuous bar with the center of each of the five stage markers. */
export function recoveryProgressPercent(progress: RecoveryProgressState): number {
  const displayedStep = recoveryDisplayedStep(progress);
  if (displayedStep === 0) return 0;
  return 10 + (displayedStep - 1) * 20;
}

export function withCreatedPullRequest(
  progress: RecoveryProgressState,
  prUrl: string | null | undefined,
  label: "PR 생성됨" | "PR 검토 필요",
): RecoveryProgressState {
  if (
    !prUrl?.trim()
    || !["waiting", "submitting", "approval"].includes(progress.phase)
  ) {
    return progress;
  }
  return {
    ...progress,
    phase: "verifying",
    label,
    step: Math.max(progress.step, 3),
    tone: "approval",
  };
}

const ANALYSIS_COMPLETED_STATUSES = new Set([
  "rca_completed",
  "followup_required",
  "action_required",
  "recovery_planned",
  "selection_required",
  "recovery_selected",
  "approval_recommended",
]);
const FAILED_STATUSES = new Set(["command_rejected", "pr_failed", "failed"]);
const COMPLETED_STATUSES = new Set(["incident_resolved"]);
const VERIFYING_STATUSES = new Set([
  "command_completed",
  "pr_requested",
  "pr_patch_prepared",
  "pr_diff_explained",
  "pr_ready_for_creation",
  "pr_created",
]);
const EXECUTING_STATUSES = new Set(["command_requested", "command_dispatched", "command_queued"]);
const SELECTED_STATUSES = new Set(["recovery_selected"]);
const PR_OPEN_STATUSES = new Set(["pr_open"]);
const DEPLOY_PENDING_STATUSES = new Set(["deploy_pending"]);
const VERIFICATION_PENDING_STATUSES = new Set(["verification_pending"]);

const FAILED_SUBJECTS = new Set([
  "command.rejected",
  "safe_pr.failed",
  "workflow.failed",
  "workflow.run.failed",
  "recovery.verification.failed",
]);
const BLOCKED_SUBJECTS = new Set([
  "rca.action_required",
  "rca.followup.required",
]);
const RETRYABLE_BLOCKER_CODES = new Set([
  "command_action_namespace_not_allowed",
  "gitops_authority_unavailable",
  "gitops_authority_mismatch",
  "recovery_action_preflight_unavailable",
  "recovery_target_identity_invalid",
  "safe_pr_patch_missing",
  "safe_pr_patch_unsupported",
  "safe_pr_preflight_failed",
  "safe_pr_preflight_unavailable",
  "pre_recovery_continuity_baseline_missing",
  "unsupported_auto_action",
  "control_namespace_not_allowed",
]);
const POLICY_REJECTION_CODES = new Set([
  "command_action_namespace_not_allowed",
  "control_namespace_not_allowed",
  "recovery_target_identity_invalid",
  "unsupported_auto_action",
]);
const COMPLETED_SUBJECTS = new Set(["incident.resolved"]);
const PR_OPEN_SUBJECTS = new Set(["recovery.pr.tracked"]);
const DEPLOY_PENDING_SUBJECTS = new Set(["recovery.pr.merged"]);
const VERIFICATION_PENDING_SUBJECTS = new Set([
  "recovery.verification.started",
  "recovery.verification.updated",
]);
const VERIFYING_SUBJECTS = new Set([
  "command.completed",
  "rollout.diagnosed",
  "workflow.run.completed",
  "safe_pr.requested",
  "safe_pr.patch_prepared",
  "safe_pr.ready_for_creation",
  "safe_pr.created",
]);
const EXECUTING_SUBJECTS = new Set([
  "command.requested",
  "command.dispatched",
  "command.queued_for_agent",
]);
const SELECTED_SUBJECTS = new Set(["recovery.action_selected", "recovery.selected"]);

export function recoveryProgressState({
  status,
  currentSubject,
  plan,
  audit = [],
  actionRoute,
  selectionPending = false,
  selectionAccepted = false,
  selectionFailed = false,
  reasonCode,
}: {
  status?: string | null;
  currentSubject?: string | null;
  plan?: RecoveryPlan | null;
  audit?: readonly AuditTimelineItem[];
  actionRoute?: string | null;
  selectionPending?: boolean;
  selectionAccepted?: boolean;
  selectionFailed?: boolean;
  reasonCode?: string | null;
}): RecoveryProgressState {
  const normalizedStatus = normalize(status);
  const planStatus = normalize(plan?.status);
  const effectiveStatus = planStatus || normalizedStatus;
  const normalizedSubject = normalize(currentSubject);
  const normalizedRoute = normalize(actionRoute);
  const normalizedReasonCode = normalize(reasonCode);
  const currentAudit = currentRecoveryAttemptAudit(audit);
  const latestEvent = currentAudit[0] ?? null;
  const subjects = currentAudit.map((event) => normalize(event.subject));
  const hasRetryableBlocker = (
    RETRYABLE_BLOCKER_CODES.has(normalizedReasonCode)
    || currentAudit.some(isRetryableRecoveryBlocker)
  );
  const hasPolicyRejection = currentAudit.some(isPolicyRecoveryRejection);
  const commandRejected = effectiveStatus === "command_rejected"
    || normalizedSubject === "command.rejected"
    || subjects.includes("command.rejected");

  if (hasPolicyRejection || commandRejected) {
    return state("blocked", "정책 검증에서 거부", 1, "failed", latestEvent);
  }
  if (
    (
      BLOCKED_SUBJECTS.has(normalizedSubject)
      && currentAudit.some((event) => normalize(event.subject) === normalizedSubject)
      && hasRetryableBlocker
    )
    || hasRetryableBlocker
  ) {
    return state("blocked", "추가 설정 필요", 1, "failed", latestEvent);
  }
  if (
    selectionFailed
    || FAILED_STATUSES.has(effectiveStatus)
    || FAILED_SUBJECTS.has(normalizedSubject)
    || subjects.some((subject) => FAILED_SUBJECTS.has(subject))
  ) {
    return state("failed", "복구 실패", failureStep(normalizedStatus, normalizedSubject, subjects), "failed", latestEvent);
  }
  if (
    COMPLETED_STATUSES.has(effectiveStatus)
    || COMPLETED_SUBJECTS.has(normalizedSubject)
    || subjects.some((subject) => COMPLETED_SUBJECTS.has(subject))
  ) {
    return state("completed", "복구 완료", 5, "completed", latestEvent);
  }
  if (
    VERIFICATION_PENDING_STATUSES.has(effectiveStatus)
    || VERIFICATION_PENDING_SUBJECTS.has(normalizedSubject)
    || subjects.some((subject) => VERIFICATION_PENDING_SUBJECTS.has(subject))
  ) {
    return state("verifying", "안정화 검증 중", 3, "active", latestEvent);
  }
  if (
    DEPLOY_PENDING_STATUSES.has(effectiveStatus)
    || DEPLOY_PENDING_SUBJECTS.has(normalizedSubject)
    || subjects.some((subject) => DEPLOY_PENDING_SUBJECTS.has(subject))
  ) {
    return state("executing", "복구 배포 중", 2, "active", latestEvent);
  }
  if (
    PR_OPEN_STATUSES.has(effectiveStatus)
    || PR_OPEN_SUBJECTS.has(normalizedSubject)
    || subjects.some((subject) => PR_OPEN_SUBJECTS.has(subject))
  ) {
    return state("approval", "PR 검토 필요", 2, "approval", latestEvent);
  }
  if (
    VERIFYING_STATUSES.has(effectiveStatus)
    || VERIFYING_SUBJECTS.has(normalizedSubject)
    || subjects.some((subject) => VERIFYING_SUBJECTS.has(subject))
  ) {
    return state("verifying", "검증 중", 3, "active", latestEvent);
  }
  if (
    EXECUTING_STATUSES.has(effectiveStatus)
    || EXECUTING_SUBJECTS.has(normalizedSubject)
    || subjects.some((subject) => EXECUTING_SUBJECTS.has(subject))
  ) {
    return state("executing", "복구 실행 중", 2, "active", latestEvent);
  }
  if (
    selectionPending
    || selectionAccepted
    || plan?.selected_action_id
    || SELECTED_STATUSES.has(effectiveStatus)
    || SELECTED_SUBJECTS.has(normalizedSubject)
    || subjects.some((subject) => SELECTED_SUBJECTS.has(subject))
  ) {
    if (normalizedRoute === "approval_required") {
      return state("submitting", "복구 요청됨", 1, "active", latestEvent);
    }
    if (normalizedRoute === "draft_pr" || normalizedRoute === "safe_pr") {
      return state("submitting", "PR 생성 요청됨", 1, "active", latestEvent);
    }
    if (normalizedRoute === "auto") {
      return state("submitting", "자동 복구 요청됨", 1, "active", latestEvent);
    }
    return state("submitting", "복구 요청됨", 1, "active", latestEvent);
  }
  if (
    plan
    || ANALYSIS_COMPLETED_STATUSES.has(effectiveStatus)
    || normalizedSubject === "recovery_review_required"
  ) {
    return state("waiting", "복구 대기", 0, "waiting", latestEvent);
  }
  return state("waiting", "복구 대기", 0, "waiting", latestEvent);
}

function isRetryableRecoveryBlocker(event: AuditTimelineItem): boolean {
  if (!BLOCKED_SUBJECTS.has(normalize(event.subject))) return false;
  const reasonCode = event.payload_summary.reason_code;
  return typeof reasonCode === "string"
    && RETRYABLE_BLOCKER_CODES.has(normalize(reasonCode));
}

function isPolicyRecoveryRejection(event: AuditTimelineItem): boolean {
  const subject = normalize(event.subject);
  if (
    !BLOCKED_SUBJECTS.has(subject)
    && subject !== "command.rejected"
  ) return false;
  const reasonCode = event.payload_summary.reason_code;
  return typeof reasonCode === "string"
    && POLICY_REJECTION_CODES.has(normalize(reasonCode));
}

export function currentRecoveryAttemptAudit(
  audit: readonly AuditTimelineItem[],
): AuditTimelineItem[] {
  const ordered = [...audit].sort((left, right) => {
    const time = Date.parse(right.created_at) - Date.parse(left.created_at);
    return time || right.event_id.localeCompare(left.event_id);
  });
  const latestSelection = ordered.findIndex((event) =>
    SELECTED_SUBJECTS.has(normalize(event.subject))
  );
  return latestSelection < 0 ? ordered : ordered.slice(0, latestSelection + 1);
}

function failureStep(status: string, subject: string, auditSubjects: readonly string[]): number {
  if (
    status === "pr_failed"
    || subject === "safe_pr.failed"
    || subject === "workflow.failed"
    || auditSubjects.some(
      (value) => value === "safe_pr.failed"
        || value === "workflow.failed"
        || value === "workflow.run.failed",
    )
  ) return 3;
  if (
    status === "command_rejected"
    || subject === "command.rejected"
    || auditSubjects.includes("command.rejected")
  ) return 2;
  return 1;
}

function state(
  phase: RecoveryProgressPhase,
  label: RecoveryProgressState["label"],
  step: number,
  tone: RecoveryProgressState["tone"],
  latestEvent: AuditTimelineItem | null,
): RecoveryProgressState {
  return { phase, label, step, tone, latestEvent };
}

function normalize(value?: string | null): string {
  return value?.trim().toLowerCase() ?? "";
}
