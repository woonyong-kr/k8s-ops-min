from __future__ import annotations

from dataclasses import dataclass, replace

from domains.command.actions import command_action_for_recovery, command_action_spec
from domains.rca.events import (
    HealingActionDraft,
    RcaActionRequiredBody,
    RcaCompletedBody,
    RecoveryActionCandidate,
    RecoveryPlan,
    RecoveryPlannedBody,
)
from packages.config.control import (
    CONTROL_NAMESPACE_DENIED_CODE,
    control_namespace_allowed,
)
from packages.contracts.event_bus.bodies import EventBody
from services.ai.agent.defaults import ActionRoutes, RcaMessages, RecoveryDefaults
from services.ai.agent.playbooks.recovery import RecoveryRule, build_recovery_context
from services.ai.agent.recovery.catalog import registered_recovery_rules

NO_RECOVERY_CANDIDATES = "복구 후보를 생성할 수 없습니다."


@dataclass(frozen=True)
class RecoveryPlanner:
    defaults: RecoveryDefaults = RecoveryDefaults()
    messages: RcaMessages = RcaMessages()
    routes: ActionRoutes = ActionRoutes()

    def plan_body(self, report: RcaCompletedBody) -> EventBody:
        context = build_recovery_context(report)
        if context is None:
            return RcaActionRequiredBody(
                reason=self.messages.missing_analysis_context,
                evidence_ref=report.evidence_ref,
                workspace_id=report.workspace_id,
            )
        candidates = ranked_candidates(context, registered_recovery_rules())
        candidates = [with_execution_eligibility(candidate) for candidate in candidates]
        candidates = sorted(candidates, key=recovery_candidate_sort_key)
        draft = first_draft_or_default(report, candidates, self.defaults)
        if not candidates:
            return RcaActionRequiredBody(
                reason=NO_RECOVERY_CANDIDATES,
                evidence_ref=report.evidence_ref,
                workspace_id=report.workspace_id,
            )
        executable_candidates = [candidate for candidate in candidates if candidate.executable]
        if not executable_candidates:
            blocked = candidates[0]
            return RcaActionRequiredBody(
                reason=blocked.blocked_reason or NO_RECOVERY_CANDIDATES,
                reason_code=blocked.blocked_reason_code or "recovery_candidate_not_executable",
                evidence_ref=report.evidence_ref,
                workspace_id=report.workspace_id,
                next_actions=[
                    {
                        "action_type": "review_control_namespace_policy",
                        "reason": blocked.blocked_reason or NO_RECOVERY_CANDIDATES,
                        "target": context.target,
                    }
                ],
                diagnostics={
                    "candidate_action_id": blocked.action_id,
                    "candidate_action_type": blocked.draft.action_type,
                    "namespace": blocked.draft.namespace,
                },
            )
        recommended = executable_candidates[0]
        return RecoveryPlannedBody(
            draft=draft,
            plan=RecoveryPlan(
                plan_id=f"recovery:{report.evidence_ref}",
                incident_id=context.incident.incident_id,
                evidence_ref=report.evidence_ref,
                summary=context.detail.reason,
                target=context.target,
                recommended_action_id=recommended.action_id,
                execution_route=recommended.route,
                selection_required=recommended.approval_required,
                candidates=candidates,
            ),
            workspace_id=report.workspace_id,
        )


def ranked_candidates(context, rules: tuple[RecoveryRule, ...]) -> list[RecoveryActionCandidate]:
    candidates: list[RecoveryActionCandidate] = []
    for rule in rules:
        if rule.supports(context):
            candidates.extend(rule.candidates(context))
    return sorted(candidates, key=recovery_candidate_sort_key)


def recovery_candidate_sort_key(
    candidate: RecoveryActionCandidate,
) -> tuple[bool, bool, int, float]:
    return (
        not candidate.executable,
        bool(candidate.draft.params.get("manual")),
        candidate.rank,
        -candidate.score,
    )


def with_execution_eligibility(
    candidate: RecoveryActionCandidate,
) -> RecoveryActionCandidate:
    """Expose deterministic command-policy blockers before operator selection."""

    if candidate.route != ActionRoutes().auto:
        return candidate
    requested = str(
        candidate.draft.params.get("command") or candidate.draft.action_type
    )
    action = command_action_for_recovery(requested)
    spec = command_action_spec(action) if action is not None else None
    if action is None or spec is None:
        return replace(
            candidate,
            executable=False,
            blocked_reason_code="unsupported_auto_action",
            blocked_reason=(
                f"{candidate.title}은 현재 에이전트 명령 카탈로그에서 지원되지 않습니다."
            ),
        )
    namespace = candidate.draft.namespace.strip()
    if not namespace:
        return replace(
            candidate,
            executable=False,
            blocked_reason_code="recovery_target_identity_invalid",
            blocked_reason="복구 대상 네임스페이스를 확인할 수 없습니다.",
        )
    if spec.enforce_control_namespace and not control_namespace_allowed(namespace):
        return replace(
            candidate,
            executable=False,
            blocked_reason_code=CONTROL_NAMESPACE_DENIED_CODE,
            blocked_reason=(
                f"{namespace} 네임스페이스는 현재 클러스터 제어 허용 범위에 "
                "포함되지 않아 이 자동 복구를 실행할 수 없습니다."
            ),
        )
    if not spec.allows_namespace(namespace):
        return replace(
            candidate,
            executable=False,
            blocked_reason_code="command_action_namespace_not_allowed",
            blocked_reason=(
                f"{action} 액션은 {namespace} 네임스페이스에서 허용되지 않습니다."
            ),
        )
    return candidate


def first_draft_or_default(
    report: RcaCompletedBody,
    candidates: list[RecoveryActionCandidate],
    defaults: RecoveryDefaults,
) -> HealingActionDraft:
    if candidates:
        return candidates[0].draft
    incident = report.incident
    detail = report.rca_detail
    return HealingActionDraft(
        action_type=defaults.action_type,
        namespace=incident.namespace
        if incident and incident.namespace
        else defaults.unknown_namespace,
        resource_kind=incident.resource_kind if incident else "unknown",
        resource_name=incident.resource_name if incident else "unknown",
        reason=detail.reason if detail else NO_RECOVERY_CANDIDATES,
        risk_level=defaults.risk_level,
        dry_run=defaults.dry_run,
        source_evidence=detail.supporting_evidence if detail else [],
        params={},
    )
