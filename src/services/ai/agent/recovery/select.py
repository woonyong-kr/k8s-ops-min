from __future__ import annotations

from dataclasses import dataclass

from domains.command.actions import command_action_for_recovery, command_action_spec
from domains.rca.events import (
    RcaActionRequiredBody,
    RecoveryActionSelectedBody,
    RecoveryPlannedBody,
    RecoverySelectionRequestedBody,
)
from packages.contracts.event_bus.bodies import EventBody
from services.ai.agent.recovery.engine import recovery_candidate_sort_key

NO_PLAN_REASON = "복구 계획이 없습니다."
NO_CANDIDATES_REASON = "복구 후보가 없어 사용자 선택이 필요합니다."
AUTO_SELECTED_BY = "agent-select"
APPROVAL_REQUIRED_COMMAND_REASON = "선택 후보가 승인 필요한 command action입니다."
AUTO_ROUTE_APPROVAL_REQUIRED_REASON = (
    "선택 후보 route=auto는 command 실행 채널을 의미하지만 "
    "approval_required=true라 자동 실행하지 않습니다."
)
APPROVAL_REQUIRED_REASON = "선택 후보가 approval_required=true라 사용자 선택이 필요합니다."
NON_AUTO_ROUTE_REASON = (
    "선택 후보 route가 command 자동 실행 채널이 아니라 사용자 선택이 필요합니다."
)


@dataclass(frozen=True)
class RecoverySelector:
    def select_body(self, evt: RecoveryPlannedBody) -> EventBody:
        if evt.plan is None:
            return RcaActionRequiredBody(
                reason=NO_PLAN_REASON,
                evidence_ref=evt.draft.source_evidence[0]
                if evt.draft.source_evidence
                else "unknown",
                workspace_id=evt.workspace_id,
            )

        sorted_candidates = sorted(evt.plan.candidates, key=recovery_candidate_sort_key)
        selected = sorted_candidates[0] if sorted_candidates else None
        if selected is None:
            return RecoverySelectionRequestedBody(
                plan=evt.plan,
                reason=NO_CANDIDATES_REASON,
                workspace_id=evt.workspace_id,
            )
        if (
            selected.route == "auto"
            and not selected.approval_required
            and not requires_approval(selected)
        ):
            return RecoveryActionSelectedBody(
                plan=evt.plan,
                selected=selected,
                selected_by=AUTO_SELECTED_BY,
                auto_selected=True,
                reason=auto_selection_reason(selected),
                workspace_id=evt.workspace_id,
            )
        return RecoverySelectionRequestedBody(
            plan=evt.plan,
            reason=selection_reason(selected),
            workspace_id=evt.workspace_id,
        )


def requires_approval(candidate: object) -> bool:
    draft = getattr(candidate, "draft", None)
    action_type = getattr(draft, "action_type", "")
    params = getattr(draft, "params", {}) or {}
    namespace = str(getattr(draft, "namespace", "") or "")
    requested = str(params.get("command") or action_type)
    action = command_action_for_recovery(requested)
    spec = command_action_spec(action) if action else None
    return bool(spec is not None and spec.requires_approval_for(namespace))


def selection_reason(candidate: object) -> str:
    if requires_approval(candidate):
        return f"{APPROVAL_REQUIRED_COMMAND_REASON} {candidate_reason_suffix(candidate)}"
    if (
        bool(getattr(candidate, "approval_required", False))
        and getattr(candidate, "route", "") == "auto"
    ):
        return f"{AUTO_ROUTE_APPROVAL_REQUIRED_REASON} {candidate_reason_suffix(candidate)}"
    if bool(getattr(candidate, "approval_required", False)):
        return f"{APPROVAL_REQUIRED_REASON} {candidate_reason_suffix(candidate)}"
    if getattr(candidate, "route", "") != "auto":
        return f"{NON_AUTO_ROUTE_REASON} {candidate_reason_suffix(candidate)}"
    return (
        "자동 선택 조건을 충족하지 못해 사용자 선택이 필요합니다. "
        f"{candidate_reason_suffix(candidate)}"
    )


def auto_selection_reason(candidate: object) -> str:
    return (
        "자동 선택 조건을 충족했습니다: route=auto(command 실행 채널), "
        "approval_required=false, command_policy=approval_not_required. "
        f"{candidate_reason_suffix(candidate)}"
    )


def candidate_reason_suffix(candidate: object) -> str:
    draft = getattr(candidate, "draft", None)
    action_type = getattr(draft, "action_type", "unknown")
    return (
        f"후보={getattr(candidate, 'action_id', 'unknown')}, "
        f"action_type={action_type}, "
        f"route={getattr(candidate, 'route', 'unknown')}, "
        f"risk_level={getattr(candidate, 'risk_level', 'unknown')}, "
        f"rank={getattr(candidate, 'rank', 'unknown')}, "
        f"score={float(getattr(candidate, 'score', 0.0)):.2f}"
    )
