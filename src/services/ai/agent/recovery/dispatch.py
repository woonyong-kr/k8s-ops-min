from __future__ import annotations

import hashlib
import math
import re
from collections.abc import Mapping
from dataclasses import dataclass, field, replace
from typing import Any, Protocol

from domains.command.actions import command_action_for_recovery, command_action_spec
from domains.command.events import CommandRequestedBody
from domains.gitops.events import Diff
from domains.gitops.source_patch import (
    ManifestScalarPatchPlan,
    ManifestSourcePatchError,
    ScalarFieldReplacement,
    memory_quantity_bytes,
    scalar_patch_content,
)
from domains.rca.events import (
    RcaActionRequiredBody,
    RecoveryActionCandidate,
    RecoveryActionSelectedBody,
    RecoveryPlan,
)
from domains.rca.recovery_verification import (
    CONTINUITY_SAMPLE_MAX_AGE_SECONDS,
    STANDARD_SLI_ALERT_NAME,
    before_alert_snapshot,
    finite_float,
    metric_sample_with_identity,
    nonnegative_int,
    protected_active_session_series,
    protected_workload_baseline,
    protected_workloads,
    trusted_evidence_window_start,
)
from domains.scm.events import (
    SAFE_PR_KIND_PATCH,
    SAFE_PR_KIND_REVIEW_DOC,
    SafePrFilePatch,
    SafePrRequestedBody,
)
from packages.config.constants import Command, GitHub, Sandbox
from packages.config.control import (
    CONTROL_NAMESPACE_DENIED_CODE,
    control_allowed_namespaces,
    control_namespace_allowed,
)
from packages.contracts.event_bus.bodies import EventBody, JsonObject
from packages.contracts.gitops_authority import (
    GitOpsAuthorityContext,
    GitOpsAuthorityQuery,
    GitOpsAuthorityReadPort,
)
from services.ai.agent.defaults import ActionRoutes
from services.ai.agent.workload_target import resolved_target_from_metadata

UNKNOWN_ROUTE_REASON = "선택된 복구 후보의 route를 처리할 수 없습니다."
UNSUPPORTED_AUTO_ACTION_REASON = "자동 실행 대상 command action으로 변환할 수 없습니다."
MISSING_SAFE_PR_PATCH_REASON = "Safe PR에 적용할 구체적인 파일 패치가 없습니다."
SAFE_PR_FALLBACK_PATCH_DIR = ".gitops/recovery"
SAFE_PR_STRUCTURED_PATCH_DIR = ".gitops/safe-pr/patches"
GITOPS_REVIEW_ACTION = "gitops_recovery_review"
RESOURCE_REQUEST_TUNING_ACTION = "resource_request_tuning"
CONFIG_FIX_ACTION = "config_fix"
SCHEDULING_CONSTRAINT_FIX_ACTION = "scheduling_constraint_fix"
REVIEW_DOC_ACTIONS = frozenset(
    {
        GITOPS_REVIEW_ACTION,
        RESOURCE_REQUEST_TUNING_ACTION,
        CONFIG_FIX_ACTION,
        SCHEDULING_CONSTRAINT_FIX_ACTION,
    }
)
RESOURCE_REQUEST_MIN_CPU = "100m"
RESOURCE_REQUEST_MIN_MEMORY = "256Mi"
AUTHORITY_PATCH_ACTIONS = frozenset(
    {
        "oom_memory",
        "image_rollback",
        "image_tag_fix",
        "replica_scale",
        "probe_fix",
        "selector_fix",
    }
)
APPROVAL_REQUIRED_CONTEXTS: dict[str, JsonObject] = {
    "image_pull_secret_fix": {
        "reason_code": "security_boundary",
        "label": "보안 경계 확인",
        "reason": "registry 인증 정보나 Secret 참조 변경은 자동으로 결정하지 않고 운영자 확인이 필요합니다.",
        "next_action": "verify_image_pull_secret",
    },
    "registry_recovery": {
        "reason_code": "external_dependency",
        "label": "외부 의존성 확인",
        "reason": "외부 registry 장애 또는 mirror 전환은 플랫폼 밖 상태와 운영 정책 확인이 필요합니다.",
        "next_action": "verify_registry_status",
    },
    "container_port_review": {
        "reason_code": "configuration_boundary",
        "label": "포트 설정 확인",
        "reason": "컨테이너 포트 충돌은 manifest, service, probe 설정을 함께 확인한 뒤 수정해야 합니다.",
        "next_action": "verify_container_port_config",
    },
    "startup_security_context_review": {
        "reason_code": "security_boundary",
        "label": "보안 컨텍스트 확인",
        "reason": "권한 오류 복구는 securityContext, volume 권한, 실행 사용자 정책 확인이 필요합니다.",
        "next_action": "verify_startup_security_context",
    },
    "config_key_review": {
        "reason_code": "configuration_boundary",
        "label": "설정 key 확인",
        "reason": "누락된 ConfigMap key는 기대 값과 배포 정책을 운영자가 확인해야 합니다.",
        "next_action": "verify_config_key_reference",
    },
    "secret_reference_fix": {
        "reason_code": "security_boundary",
        "label": "Secret 참조 확인",
        "reason": "Secret 참조 보정은 민감 정보 경계와 namespace 권한 확인이 필요합니다.",
        "next_action": "verify_secret_reference",
    },
    "service_reference_review": {
        "reason_code": "traffic_routing",
        "label": "Service 참조 확인",
        "reason": "Service 이름이나 namespace 보정은 트래픽 라우팅 대상 변경이므로 운영자 확인이 필요합니다.",
        "next_action": "verify_service_reference",
    },
    "network_policy_review": {
        "reason_code": "traffic_routing",
        "label": "NetworkPolicy 확인",
        "reason": "NetworkPolicy 변경은 namespace 간 통신 허용 범위를 바꿀 수 있어 운영자 판단이 필요합니다.",
        "next_action": "verify_network_policy",
    },
    "autoscaling_metrics_recovery": {
        "reason_code": "platform_dependency",
        "label": "Autoscaling metrics 확인",
        "reason": "metrics-server 또는 custom metrics adapter 상태는 플랫폼 의존성 확인이 필요합니다.",
        "next_action": "verify_autoscaling_metrics",
    },
    "dependency_connection_review": {
        "reason_code": "external_dependency",
        "label": "외부 의존성 연결 확인",
        "reason": "DB 연결 복구는 애플리케이션 밖의 endpoint, 네트워크, pool 상태 확인이 필요합니다.",
        "next_action": "verify_dependency_connectivity",
    },
    "dependency_config_review": {
        "reason_code": "external_dependency",
        "label": "외부 의존성 설정 확인",
        "reason": "DB 인증/설정 복구는 Secret/ConfigMap 참조와 외부 서비스 설정 확인이 필요합니다.",
        "next_action": "verify_dependency_config",
    },
    "pvc_binding_fix": {
        "reason_code": "data_safety",
        "label": "데이터 안전성 확인",
        "reason": "PVC와 StorageClass 변경은 데이터 보존과 바인딩 정책에 영향을 줄 수 있어 운영자 판단이 필요합니다.",
        "next_action": "verify_storage_binding",
    },
    "manual_analysis": {
        "reason_code": "manual_only",
        "label": "수동 분석 필요",
        "reason": "자동 복구 후보가 충분하지 않아 운영자 RCA 검토가 필요합니다.",
        "next_action": "review_rca_findings",
    },
}
DEFAULT_APPROVAL_REQUIRED_CONTEXT: JsonObject = {
    "reason_code": "manual_review_required",
    "label": "운영자 승인 필요",
    "reason": "선택된 복구 조치는 자동 실행 조건을 충족하지 않아 운영자 확인이 필요합니다.",
    "next_action": "review_recovery_action",
}
SAFE_PR_TITLE_MAX_LENGTH = 120
PROTECTED_WORKLOAD_CONTINUITY_CONTRACT = "protected_workload_continuity"


class RecoveryEvidenceReadPort(Protocol):
    async def get_evidence_payload(
        self,
        workspace_id: str,
        correlation_id: str,
        kind: str,
    ) -> JsonObject | None: ...

    async def get_cluster_registration(
        self,
        workspace_id: str,
        cluster_id: str,
    ) -> JsonObject | None: ...

    async def list_alert_events(
        self,
        workspace_id: str,
        *,
        rule_name: str | None = None,
        source: str | None = None,
        incident_ids: tuple[str, ...] | None = None,
        limit: int,
    ) -> list[JsonObject]: ...


@dataclass(frozen=True)
class RecoveryDispatcher:
    routes: ActionRoutes = field(default_factory=ActionRoutes)

    async def dispatch_body(
        self,
        evt: RecoveryActionSelectedBody,
        *,
        authority: GitOpsAuthorityReadPort | None = None,
        correlation_id: str = "",
    ) -> EventBody:
        selected = evt.selected
        if selected.route == self.routes.auto:
            command = build_command_request_body(
                evt.plan,
                selected,
                selected_by=evt.selected_by,
                auto_selected=evt.auto_selected,
            )
            if command is None:
                return RcaActionRequiredBody(
                    reason=f"{UNSUPPORTED_AUTO_ACTION_REASON}: {selected.draft.action_type}",
                    evidence_ref=evt.plan.evidence_ref,
                    workspace_id=evt.workspace_id,
                )
            return command
        if selected.route == self.routes.safe_pr:
            return await dispatch_safe_pr_body(evt, authority, correlation_id)
        if selected.route == self.routes.approval_required:
            return approval_required_body(evt)
        if selected.route == self.routes.forbidden:
            return RcaActionRequiredBody(
                reason=f"자동 조치 차단: {selected.title}",
                evidence_ref=evt.plan.evidence_ref,
                workspace_id=evt.workspace_id,
            )
        return RcaActionRequiredBody(
            reason=f"{UNKNOWN_ROUTE_REASON}: {selected.route}",
            evidence_ref=evt.plan.evidence_ref,
            workspace_id=evt.workspace_id,
        )


@dataclass(frozen=True)
class RecoveryActionPreflight:
    """HTTP 선택 단계에서 worker와 같은 실행 계약을 미리 검증한다."""

    authority: GitOpsAuthorityReadPort | None
    evidence: RecoveryEvidenceReadPort | None = None
    routes: ActionRoutes = field(default_factory=ActionRoutes)

    async def prepare(
        self,
        evt: RecoveryActionSelectedBody,
        correlation_id: str,
    ) -> RecoveryActionCandidate | RcaActionRequiredBody:
        if evt.selected.route == self.routes.auto:
            return auto_action_preflight(evt)
        if evt.selected.route != self.routes.safe_pr:
            return evt.selected
        outcome = await dispatch_safe_pr_body(evt, self.authority, correlation_id)
        if isinstance(outcome, RcaActionRequiredBody):
            return outcome
        if not isinstance(outcome, SafePrRequestedBody):
            return authority_required_body(
                evt,
                "safe_pr_preflight_failed",
                "Safe PR 사전 검증 결과를 해석할 수 없습니다.",
                ["gitops_authority_context"],
            )
        draft = evt.selected.draft
        verification_params: JsonObject = {}
        verification_blockers: list[str] = []
        expected_replicas: int | None = None
        if self.authority is not None:
            query = authority_query(evt, correlation_id)
            resolved_authority = await self.authority.load_authority(query)
            if (
                resolved_authority is not None
                and authority_matches_query(resolved_authority, query)
            ):
                replacements = scalar_replacements_for(
                    evt.selected.draft.action_type,
                    evt.selected,
                    resolved_authority,
                )
                if replacements:
                    verification_params["authorized_changes"] = [
                        {
                            "field_path": replacement.field_path,
                            "current_value": replacement.current_value,
                            "desired_value": replacement.desired_value,
                        }
                        for replacement in replacements
                    ]
                if (
                    evt.selected.draft.action_type == "replica_scale"
                    and len(replacements) == 1
                    and replacements[0].field_path == "spec.replicas"
                    and isinstance(replacements[0].desired_value, int)
                    and not isinstance(replacements[0].desired_value, bool)
                    and replacements[0].desired_value > 0
                ):
                    expected_replicas = replacements[0].desired_value
        if (
            draft.params.get("verification_contract")
            == PROTECTED_WORKLOAD_CONTINUITY_CONTRACT
        ):
            evidence: dict[str, object] = {}
            if self.evidence is None:
                verification_blockers.append(
                    "metadata:current_workload_snapshots"
                )
            else:
                loaded_evidence = await self.evidence.get_evidence_payload(
                    evt.workspace_id,
                    correlation_id,
                    "rca_bundle",
                )
                if isinstance(loaded_evidence, dict):
                    evidence = loaded_evidence
            target = {
                "cluster_id": str(
                    draft.params.get("cluster_id")
                    or draft.params.get("cluster")
                    or evt.plan.target.get("cluster_id")
                    or ""
                ),
                "namespace": str(
                    draft.params.get("namespace")
                    or evt.plan.target.get("namespace")
                    or ""
                ),
                "resource_kind": str(
                    draft.params.get("resource_kind")
                    or evt.plan.target.get("resource_kind")
                    or ""
                ),
                "resource_name": str(
                    draft.params.get("resource_name")
                    or evt.plan.target.get("resource_name")
                    or ""
                ),
            }
            observed = (
                protected_workloads(evidence, target)
                if evidence
                else None
            )
            baseline = protected_workload_baseline(observed or [])
            if (
                not observed
                or any(workload.get("healthy") is not True for workload in observed)
                or len(baseline) != len(observed)
            ):
                verification_blockers.append(
                    "metadata:current_workload_snapshots"
                )
                baseline = []
            evidence_observed_at = (
                trusted_evidence_window_start(
                    evidence,
                    expected_workspace_id=evt.workspace_id,
                    expected_cluster_id=target["cluster_id"],
                )
                if evidence
                else None
            )
            session_baseline = (
                protected_active_session_series(
                    evidence,
                    baseline,
                    evidence_observed_at=evidence_observed_at,
                    max_sample_age_seconds=CONTINUITY_SAMPLE_MAX_AGE_SECONDS,
                )
                if baseline and evidence_observed_at is not None
                else []
            )
            if not session_baseline:
                verification_blockers.append(
                    "metrics:opsia_continuity_active_sessions"
                )
            failure_ratio_before, failure_ratio_identity = metric_sample_with_identity(
                evidence,
                "opsia_sli_failure_ratio",
                target,
            )
            request_rate_baseline, request_rate_identity = metric_sample_with_identity(
                evidence,
                "opsia_sli_request_rate",
                target,
            )
            if (
                failure_ratio_before is None
                or failure_ratio_identity is None
                or request_rate_baseline is None
                or request_rate_baseline <= 0
                or request_rate_identity is None
            ):
                verification_blockers.extend(
                    [
                        "metrics:opsia_sli_failure_ratio",
                        "metrics:opsia_sli_request_rate",
                    ]
                )
            alerts = (
                await self.evidence.list_alert_events(
                    evt.workspace_id,
                    rule_name=STANDARD_SLI_ALERT_NAME,
                    source="alertmanager",
                    incident_ids=tuple(
                        sorted(
                            {
                                value
                                for value in (
                                    correlation_id,
                                    evt.plan.incident_id,
                                )
                                if value
                            }
                        )
                    ),
                    limit=10,
                )
                if self.evidence is not None
                else []
            )
            alert_before = before_alert_snapshot(
                alerts,
                target=target,
                correlation_id=correlation_id,
                incident_id=evt.plan.incident_id,
                expected_series_identity=failure_ratio_identity,
            )
            threshold = finite_float(alert_before.get("threshold"))
            registration = (
                await self.evidence.get_cluster_registration(
                    evt.workspace_id,
                    target["cluster_id"],
                )
                if self.evidence is not None
                else None
            )
            settings = (
                registration.get("settings")
                if isinstance(registration, Mapping)
                and isinstance(registration.get("settings"), Mapping)
                else {}
            )
            evidence_cadence_seconds = nonnegative_int(
                settings.get("evidence_interval_seconds")
            )
            if alert_before.get("available") is not True or threshold is None:
                verification_blockers.append(
                    "alertmanager:original_exact_alert"
                )
            if evidence_cadence_seconds is None or evidence_cadence_seconds <= 0:
                verification_blockers.append("cluster:evidence_cadence")
            if expected_replicas is None:
                verification_blockers.append(
                    "gitops:approved_replica_baseline"
                )
            verification_params["protected_baseline"] = baseline
            verification_params["protected_session_baseline"] = session_baseline
            verification_params["verification_failure_ratio_before"] = (
                failure_ratio_before
            )
            verification_params["verification_failure_ratio_metric_identity"] = (
                failure_ratio_identity
            )
            verification_params["verification_request_rate_baseline"] = (
                request_rate_baseline
            )
            verification_params["verification_request_rate_metric_identity"] = (
                request_rate_identity
            )
            verification_params["verification_alert_before"] = alert_before
            verification_params["verification_evidence_cadence_seconds"] = (
                evidence_cadence_seconds
            )
            verification_params["expected_replicas"] = expected_replicas
            if verification_blockers:
                verification_params["verification_blockers"] = sorted(
                    set(verification_blockers)
                )
                verification_params["verification_merge_blocked"] = True
        return replace(
            evt.selected,
            draft=replace(
                draft,
                params={
                    **draft.params,
                    "workspace_id": outcome.workspace_id,
                    "repository_id": outcome.repository_id,
                    "binding_id": outcome.binding_id,
                    "application_id": outcome.application_id,
                    "workflow_run_id": outcome.workflow_run_id,
                    "environment": outcome.environment,
                    "manifest_path": outcome.manifest_path,
                    "repo_ref": outcome.repo_ref,
                    "base_branch": outcome.base_branch,
                    "commit_sha": outcome.commit_sha,
                    **verification_params,
                },
            ),
        )


def approval_required_body(evt: RecoveryActionSelectedBody) -> RcaActionRequiredBody:
    selected = evt.selected
    context = APPROVAL_REQUIRED_CONTEXTS.get(
        selected.draft.action_type,
        DEFAULT_APPROVAL_REQUIRED_CONTEXT,
    )
    reason_code = str(context["reason_code"])
    label = str(context["label"])
    reason = str(context["reason"])
    next_action = str(context["next_action"])
    return RcaActionRequiredBody(
        reason=f"승인 필요({label}): {selected.title}. {reason}",
        evidence_ref=evt.plan.evidence_ref,
        workspace_id=evt.workspace_id,
        reason_code=reason_code,
        next_actions=[
            {
                "action_type": next_action,
                "reason": reason,
                "target": evt.plan.target,
            }
        ],
        diagnostics={
            "plan_id": evt.plan.plan_id,
            "incident_id": evt.plan.incident_id,
            "action_id": selected.action_id,
            "action_type": selected.draft.action_type,
            "route": selected.route,
            "risk_level": selected.risk_level,
            "blast_radius": selected.blast_radius,
            "approval_reason": reason_code,
            "approval_label": label,
        },
    )


def build_safe_pr_request_body(
    plan: RecoveryPlan,
    selected: RecoveryActionCandidate,
    workspace_id: str,
    patches: list[SafePrFilePatch],
    authority: GitOpsAuthorityContext | None = None,
) -> SafePrRequestedBody | RcaActionRequiredBody:
    draft = selected.draft
    if authority is None:
        return RcaActionRequiredBody(
            reason="Safe PR 생성에 필요한 GitOps 권위 context가 없습니다.",
            evidence_ref=plan.evidence_ref,
            workspace_id=workspace_id,
            reason_code="gitops_authority_unavailable",
            missing_evidence=["gitops_authority_context"],
            next_actions=[
                {
                    "action_type": "collect_gitops_authority",
                    "reason": "repository·binding·manifest 권위를 먼저 확인해야 합니다.",
                    "target": plan.target,
                }
            ],
            diagnostics={
                "plan_id": plan.plan_id,
                "action_id": selected.action_id,
                "route": selected.route,
            },
        )
    if not patches:
        return RcaActionRequiredBody(
            reason=f"{MISSING_SAFE_PR_PATCH_REASON}: {selected.title}",
            evidence_ref=plan.evidence_ref,
            workspace_id=workspace_id,
            reason_code="safe_pr_patch_missing",
            missing_evidence=["manifest_patch"],
            next_actions=[
                {
                    "action_type": "collect_manifest_context",
                    "reason": "Recovery Safe PR requires concrete file patches before PR creation.",
                    "target": draft.params,
                }
            ],
            diagnostics={
                "plan_id": plan.plan_id,
                "action_id": selected.action_id,
                "route": selected.route,
            },
        )
    return SafePrRequestedBody(
        title=recovery_safe_pr_title(selected),
        body=recovery_safe_pr_body(plan, selected),
        provider=GitHub.PROVIDER,
        patches=patches,
        # ``safe_pr`` route는 이름과 사용자 계약 그대로 항상 리뷰 가능한 PR을
        # 만든다. 승인 완료는 변경 제안 권한이지 base branch 직접 쓰기 권한이
        # 아니다. 직접 커밋은 별도 route에서 명시적으로 요청해야 한다.
        delivery="pull_request",
        pr_kind=safe_pr_kind(selected),
        workspace_id=workspace_id,
        repository_id=authority.repository_id,
        binding_id=authority.binding_id,
        application_id=authority.application_id,
        workflow_run_id=authority.workflow_run_id,
        environment=authority.environment,
        manifest_path=authority.manifest_path,
        repo_ref=authority.repo_ref,
        base_branch=authority.base_branch,
        commit_sha=authority.commit_sha,
        cluster_id=authority.cluster_id,
        target_namespace=first_str(
            nested_value(authority.desired_manifest, "metadata", "namespace")
        ),
        target_resource=authority.resource,
        target_authority="completed_workload_change",
        approval_ref=as_optional_str(draft.params.get("approval_ref")),
        policy_decision_ref=as_optional_str(draft.params.get("policy_decision_ref")),
    )


def recovery_safe_pr_title(selected: RecoveryActionCandidate) -> str:
    action = one_line(selected.title) or "복구 설정 적용"
    resource = one_line(selected.draft.resource_name) or "대상 리소스"
    title = f"[복구] {resource} - {action}"
    if len(title) <= SAFE_PR_TITLE_MAX_LENGTH:
        return title
    return f"{title[: SAFE_PR_TITLE_MAX_LENGTH - 1].rstrip()}…"


def recovery_safe_pr_body(
    plan: RecoveryPlan,
    selected: RecoveryActionCandidate,
) -> str:
    draft = selected.draft
    target = " / ".join(
        part
        for part in (
            one_line(draft.namespace),
            one_line(draft.resource_kind),
            one_line(draft.resource_name),
        )
        if part
    )
    reason = selected.recommendation_reason or draft.reason or selected.description
    expected_outcome = selected.expected_outcome or "적용 후 검증 항목을 기준으로 정상화를 확인합니다."
    sections = [
        "## 복구 개요",
        "",
        paragraph(plan.summary, "선택한 복구 조치를 GitOps 변경으로 제안합니다."),
        "",
        f"- **복구 조치:** {one_line(selected.title) or '복구 설정 적용'}",
        f"- **대상:** `{target or '대상 미확인'}`",
        f"- **영향 범위:** {one_line(selected.blast_radius) or '확인 필요'}",
        f"- **조치 위험도:** {recovery_risk_label(selected.risk_level)}",
        "",
        "## 변경 내용",
        "",
        paragraph(selected.description, "선택한 복구 조치를 매니페스트에 반영합니다."),
        "",
        f"**선택 이유:** {paragraph(reason, '복구 후보의 근거를 확인해 주세요.')}",
        "",
        f"**기대 결과:** {paragraph(expected_outcome, '적용 후 정상화 여부를 확인합니다.')}",
        "",
        "## 사전 확인",
        "",
        *checklist(selected.prerequisites, "별도로 정의된 사전 확인 항목이 없습니다."),
        "",
        "## 적용 후 검증",
        "",
        *checklist(selected.validation_checks, "별도로 정의된 검증 항목이 없습니다."),
        "",
        "## 실패 시 복원",
        "",
        paragraph(selected.rollback_plan, "복원 계획이 정의되지 않았습니다."),
        "",
        "<details>",
        "<summary>추적 정보</summary>",
        "",
        f"- 복구 계획: `{plan.plan_id}`",
        f"- 장애: `{plan.incident_id}`",
        f"- 조치: `{selected.action_id}`",
        "",
        "</details>",
        "",
        "---",
        "",
        "> Kyro 복구 파이프라인에서 생성된 PR입니다. 적용 전 변경 내용과 검증 계획을 확인해 주세요.",
    ]
    return "\n".join(sections).strip()


def one_line(value: object) -> str:
    return " ".join(str(value or "").split())


def paragraph(value: object, fallback: str) -> str:
    normalized = str(value or "").strip()
    return normalized or fallback


def checklist(items: list[str], fallback: str) -> list[str]:
    normalized = [one_line(item) for item in items if one_line(item)]
    if not normalized:
        return [f"- {fallback}"]
    return [f"- [ ] {item}" for item in normalized]


def recovery_risk_label(value: str) -> str:
    normalized = one_line(value).casefold()
    labels = {
        "low": "낮음",
        "medium": "보통",
        "moderate": "보통",
        "high": "높음",
        "critical": "매우 높음",
    }
    return labels.get(normalized, one_line(value) or "확인 필요")


def safe_pr_kind(selected: RecoveryActionCandidate) -> str:
    if selected.draft.action_type in REVIEW_DOC_ACTIONS:
        return SAFE_PR_KIND_REVIEW_DOC
    return SAFE_PR_KIND_PATCH


async def dispatch_safe_pr_body(
    evt: RecoveryActionSelectedBody,
    authority_port: GitOpsAuthorityReadPort | None,
    correlation_id: str,
) -> EventBody:
    selected = evt.selected
    if authority_port is None or not correlation_id:
        return authority_required_body(
            evt,
            "gitops_authority_unavailable",
            "patch 생성 시점의 GitOps 권위 context를 조회할 수 없습니다.",
            ["gitops_authority_context"],
        )
    query = authority_query(evt, correlation_id)
    authority = await authority_port.load_authority(query)
    if authority is None:
        return authority_required_body(
            evt,
            "gitops_authority_unavailable",
            "승인 snapshot·binding·repository 권위 context를 확보하지 못했습니다.",
            ["gitops_authority_context"],
        )
    if not authority_matches_query(authority, query):
        return authority_required_body(
            evt,
            "gitops_authority_mismatch",
            "조회된 GitOps 권위 context가 선택된 recovery target과 일치하지 않습니다.",
            ["matching_gitops_authority_context"],
        )
    if selected.draft.action_type in REVIEW_DOC_ACTIONS:
        return build_safe_pr_request_body(
            evt.plan,
            selected,
            evt.workspace_id,
            [fallback_recovery_patch(selected)],
            authority,
        )
    if selected.draft.action_type not in AUTHORITY_PATCH_ACTIONS:
        return authority_required_body(
            evt,
            "safe_pr_patch_unsupported",
            f"지원하지 않는 recovery patch action입니다: {selected.draft.action_type}",
            ["supported_patch_action"],
        )
    try:
        patches = authority_safe_pr_patches(selected, authority)
    except ManifestSourcePatchError:
        patches = []
    if not patches:
        return authority_required_body(
            evt,
            "safe_pr_patch_unsupported",
            "권위 snapshot에서 정책 범위 안의 실제 manifest patch를 만들 수 없습니다.",
            ["patchable_authority_snapshot"],
        )
    return build_safe_pr_request_body(
        evt.plan,
        selected,
        evt.workspace_id,
        patches,
        authority,
    )


def authority_query(
    evt: RecoveryActionSelectedBody,
    correlation_id: str,
) -> GitOpsAuthorityQuery:
    target = evt.plan.target
    draft = evt.selected.draft
    namespace = str(target.get("namespace") or draft.namespace)
    resource_kind = str(target.get("resource_kind") or draft.resource_kind)
    resource_name = str(target.get("resource_name") or draft.resource_name)
    resolved = resolved_target_from_metadata(
        namespace,
        resource_kind,
        resource_name,
        target,
        draft.params,
    )
    return GitOpsAuthorityQuery(
        correlation_id=correlation_id,
        workspace_id=evt.workspace_id,
        incident_id=evt.plan.incident_id,
        cluster_id=str(target.get("cluster_id") or ""),
        namespace=resolved.namespace,
        resource_kind=resolved.resource_kind,
        resource_name=resolved.resource_name,
    )


def authority_matches_query(
    authority: GitOpsAuthorityContext,
    query: GitOpsAuthorityQuery,
) -> bool:
    resource_kind, _, resource_name = authority.resource.partition("/")
    return bool(
        authority.workspace_id == query.workspace_id
        and authority.cluster_id == query.cluster_id
        and resource_kind.casefold() == query.resource_kind.casefold()
        and resource_name == query.resource_name
        and all(
            (
                authority.repository_id,
                authority.binding_id,
                authority.application_id,
                authority.workflow_run_id,
                authority.manifest_path,
                authority.repo_ref,
                authority.base_branch,
                authority.commit_sha,
                authority.source_manifest_sha256,
            )
        )
    )


def authority_required_body(
    evt: RecoveryActionSelectedBody,
    reason_code: str,
    reason: str,
    missing: list[str],
) -> RcaActionRequiredBody:
    return RcaActionRequiredBody(
        reason=reason,
        evidence_ref=evt.plan.evidence_ref,
        workspace_id=evt.workspace_id,
        reason_code=reason_code,
        missing_evidence=missing,
        next_actions=[
            {
                "action_type": "collect_gitops_authority",
                "reason": reason,
                "target": evt.plan.target,
            }
        ],
        diagnostics={
            "plan_id": evt.plan.plan_id,
            "action_id": evt.selected.action_id,
            "action_type": evt.selected.draft.action_type,
        },
    )


def auto_action_preflight(
    evt: RecoveryActionSelectedBody,
) -> RecoveryActionCandidate | RcaActionRequiredBody:
    """Reject an impossible command before the recovery selection is persisted."""

    selected = evt.selected
    if not selected.executable:
        return command_policy_required_body(
            evt,
            selected.blocked_reason_code or "recovery_candidate_not_executable",
            selected.blocked_reason or "선택한 복구 후보는 현재 정책에서 실행할 수 없습니다.",
        )
    action = command_action_for(selected)
    if action is None:
        return command_policy_required_body(
            evt,
            "unsupported_auto_action",
            UNSUPPORTED_AUTO_ACTION_REASON,
            missing=["command_action"],
        )
    spec = command_action_spec(action)
    if spec is None:
        return command_policy_required_body(
            evt,
            "unsupported_auto_action",
            UNSUPPORTED_AUTO_ACTION_REASON,
            missing=["command_action_spec"],
            diagnostics={"command_action": action},
        )
    namespace = exact_nonempty_value(
        evt.plan.target.get("namespace"),
        selected.draft.namespace,
        selected.draft.params.get("namespace"),
    )
    if not namespace:
        return command_policy_required_body(
            evt,
            "recovery_target_identity_invalid",
            "복구 대상 네임스페이스를 하나의 값으로 확인할 수 없습니다.",
            missing=["target:namespace"],
            diagnostics={"command_action": action},
        )
    diagnostics: JsonObject = {
        "command_action": action,
        "namespace": namespace,
        "control_allowed_namespaces": list(control_allowed_namespaces()),
        "action_allowed_namespaces": list(spec.allowed_namespaces),
    }
    if spec.enforce_control_namespace and not control_namespace_allowed(namespace):
        return command_policy_required_body(
            evt,
            CONTROL_NAMESPACE_DENIED_CODE,
            (
                f"{namespace} 네임스페이스는 현재 클러스터 제어 허용 범위에 "
                "포함되지 않아 복구 명령을 제출할 수 없습니다."
            ),
            diagnostics=diagnostics,
        )
    if not spec.allows_namespace(namespace):
        return command_policy_required_body(
            evt,
            "command_action_namespace_not_allowed",
            (
                f"{action} 액션은 {namespace} 네임스페이스에서 허용되지 않아 "
                "복구 명령을 제출할 수 없습니다."
            ),
            diagnostics=diagnostics,
        )
    return selected


def command_policy_required_body(
    evt: RecoveryActionSelectedBody,
    reason_code: str,
    reason: str,
    *,
    missing: list[str] | None = None,
    diagnostics: JsonObject | None = None,
) -> RcaActionRequiredBody:
    return RcaActionRequiredBody(
        reason=reason,
        evidence_ref=evt.plan.evidence_ref,
        workspace_id=evt.workspace_id,
        reason_code=reason_code,
        missing_evidence=missing or [],
        next_actions=[
            {
                "action_type": "review_control_namespace_policy",
                "reason": (
                    "클러스터 연결의 제어 허용 범위와 명령 액션 정책을 확인한 뒤 "
                    "허용되는 복구 후보를 선택하세요."
                ),
                "target": evt.plan.target,
            }
        ],
        diagnostics={
            "plan_id": evt.plan.plan_id,
            "action_id": evt.selected.action_id,
            "action_type": evt.selected.draft.action_type,
            **(diagnostics or {}),
        },
    )


def build_command_request_body(
    plan: RecoveryPlan,
    selected: RecoveryActionCandidate,
    *,
    selected_by: str,
    auto_selected: bool,
) -> CommandRequestedBody | None:
    draft = selected.draft
    action = command_action_for(selected)
    if action is None:
        return None
    workspace_id = exact_nonempty_value(
        plan.target.get("workspace_id"),
        draft.params.get("workspace_id"),
    )
    cluster_id = exact_nonempty_value(
        plan.target.get("cluster_id"),
        draft.params.get("cluster_id"),
    )
    environment = exact_nonempty_value(
        plan.target.get("environment"),
        draft.params.get("environment"),
    )
    namespace = exact_nonempty_value(
        plan.target.get("namespace"),
        draft.namespace,
        draft.params.get("namespace"),
    )
    resource_kind = exact_nonempty_value(
        plan.target.get("resource_kind"),
        draft.resource_kind,
    )
    resource_name = exact_nonempty_value(
        plan.target.get("resource_name"),
        draft.resource_name,
    )
    if not all(
        (
            workspace_id,
            cluster_id,
            environment,
            namespace,
            resource_kind,
            resource_name,
        )
    ):
        return None
    resolved = resolved_target_from_metadata(
        namespace,
        resource_kind,
        resource_name,
        plan.target,
        draft.params,
    )
    if not all((resolved.namespace, resolved.resource_kind, resolved.resource_name)):
        return None
    payload = command_payload_for(
        action,
        resolved.resource_name,
        resolved.namespace,
        draft.params,
    )
    if payload is None:
        return None
    return CommandRequestedBody(
        cluster_id=cluster_id,
        action=action,
        namespace=resolved.namespace,
        reason=selected.description,
        diff=Diff(
            resource=command_diff_resource(
                action,
                resolved.resource_name,
                resolved.resource_kind,
                resolved.resource_name,
            ),
            namespace=resolved.namespace,
            desired_image="",
            actual_image="",
            risk=Sandbox.RISK_TAG,
            workspace_id=workspace_id,
            status="recovery_action",
            has_changes=True,
            basis={
                "source": "rca_recovery",
                "plan_id": plan.plan_id,
                "action_id": selected.action_id,
                "root_cause": draft.params.get("root_cause"),
            },
        ),
        workspace_id=workspace_id,
        application_id=str(draft.params.get("application_id") or ""),
        workflow_run_id=str(draft.params.get("workflow_run_id") or ""),
        binding_id=str(draft.params.get("binding_id") or ""),
        environment=environment,
        requested_by=selected_by,
        approval_ref=as_optional_str(selected.draft.params.get("approval_ref")),
        policy_decision_ref=as_optional_str(selected.draft.params.get("policy_decision_ref")),
        actor={
            "plan_id": plan.plan_id,
            "action_id": selected.action_id,
            "auto_selected": auto_selected,
        },
        payload=payload,
    )


def command_action_for(selected: RecoveryActionCandidate) -> str | None:
    requested = str(selected.draft.params.get("command") or selected.draft.action_type)
    return command_action_for_recovery(requested)


def command_payload_for(
    action: str,
    resource_name: str,
    namespace: str,
    params: JsonObject,
) -> JsonObject | None:
    if action != Command.KUBERNETES_DEPLOYMENT_SCALE_ACTION:
        return {}
    replicas = params.get("replicas")
    if type(replicas) is not int or replicas <= 0:
        return None
    return {
        "namespace": namespace,
        "name": resource_name,
        "replicas": replicas,
    }


def exact_nonempty_value(*values: object) -> str:
    """Resolve one authoritative scalar, rejecting missing or conflicting values."""

    normalized = {
        value.strip()
        for value in values
        if isinstance(value, str) and value.strip()
    }
    return next(iter(normalized)) if len(normalized) == 1 else ""


def command_diff_resource(
    action: str,
    command_target: str,
    source_kind: str,
    source_name: str,
) -> str:
    if action in {Command.DEFAULT_ACTION, Command.KUBERNETES_DEPLOYMENT_SCALE_ACTION}:
        return f"deployment/{command_target}"
    return f"{source_kind}/{source_name}"


def command_target_name(kind: str, name: str, params: JsonObject) -> str:
    explicit = first_str(
        params.get("deployment"),
        params.get("deployment_name"),
        params.get("workload_name"),
        params.get("target_deployment"),
    )
    if explicit:
        return explicit
    normalized_kind = kind.strip().lower()
    normalized_name = name.strip()
    if normalized_kind in {"deployment", "deployments"}:
        return normalized_name
    if normalized_kind in {"replicaset", "replicasets"}:
        return deployment_from_replicaset_name(normalized_name) or normalized_name
    if normalized_kind in {"pod", "pods"}:
        return deployment_from_pod_name(normalized_name) or normalized_name
    return normalized_name


def first_str(*values: object) -> str:
    for value in values:
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def deployment_from_pod_name(name: str) -> str:
    match = re.match(r"^(.+)-[a-f0-9]{8,10}-[a-z0-9]{5}$", name)
    return match.group(1) if match else ""


def deployment_from_replicaset_name(name: str) -> str:
    match = re.match(r"^(.+)-[a-f0-9]{8,10}$", name)
    return match.group(1) if match else ""


def authority_safe_pr_patches(
    selected: RecoveryActionCandidate,
    authority: GitOpsAuthorityContext,
) -> list[SafePrFilePatch]:
    action_type = selected.draft.action_type
    replacements = scalar_replacements_for(action_type, selected, authority)
    if not replacements:
        return []
    rollback = tuple(
        ScalarFieldReplacement(
            field_path=item.field_path,
            current_value=item.desired_value,
            desired_value=item.current_value,
        )
        for item in replacements
    )
    plan = ManifestScalarPatchPlan(
        action_type=action_type,
        source_type=authority.source_type,
        source_manifest_sha256=authority.source_manifest_sha256,
        expected_base_sha=authority.commit_sha,
        manifest_path=authority.manifest_path,
        replacements=tuple(replacements),
        rollback_replacements=rollback,
    )
    token = hashlib.sha256(f"{selected.action_id}:{authority.commit_sha}".encode()).hexdigest()[:24]
    return [
        SafePrFilePatch(
            path=f"{SAFE_PR_STRUCTURED_PATCH_DIR}/{token}.yaml",
            content=scalar_patch_content(plan),
            description=f"{selected.title} (exact-base patch + inverse rollback)",
        )
    ]


def scalar_replacements_for(
    action_type: str,
    selected: RecoveryActionCandidate,
    authority: GitOpsAuthorityContext,
) -> list[ScalarFieldReplacement]:
    manifest = authority.desired_manifest
    container = target_container(manifest, selected.draft.resource_name)
    if action_type in {"image_rollback", "image_tag_fix"}:
        return image_replacements(authority, container)
    if action_type == "replica_scale":
        replicas = nested_value(manifest, "spec", "replicas")
        if type(replicas) is not int or not 1 <= replicas < 10:
            return []
        # 원복 이력이 있으면 그 값을 우선한다. 용량 포화 규칙이 명시적으로
        # 허용한 경우에만 이력이 없거나 모호할 때 제한된 +1 증설로 전환한다.
        previous = previous_replicas_from_changes(authority, replicas)
        strategy = first_str(selected.draft.params.get("strategy"))
        root_cause = first_str(selected.draft.params.get("root_cause"))
        allow_scale_out = (
            selected.draft.params.get("allow_bounded_scale_out") is True
            or (
                strategy == "last_approved_snapshot"
                and root_cause == "lobby_capacity_saturation"
            )
        )
        if (
            strategy == "last_approved_snapshot"
            and previous is None
            and not allow_scale_out
        ):
            return []
        desired = previous if previous is not None else replicas + 1
        if desired == replicas or not 1 <= desired < 10:
            return []
        return [ScalarFieldReplacement("spec.replicas", replicas, desired)]
    if action_type == "oom_memory":
        return oom_memory_replacements(authority, container)
    if action_type == "probe_fix":
        return probe_replacements(container, authority)
    if action_type == "selector_fix":
        return selector_replacements(manifest)
    return []


def previous_replicas_from_changes(
    authority: GitOpsAuthorityContext,
    current_replicas: int,
) -> int | None:
    """권위 스냅샷 변경 이력에서 replicas 축소 직전의 승인 값을 찾는다.

    조건: field_path 가 spec.replicas 로 끝나고, 새 값(new_desired)이 현재 관측값과
    일치하며, 이전 값(old_desired)이 현재보다 큰 단 하나의 변경일 때만 신뢰한다.
    (여러 이력이 겹치면 특정 불가 → None → 호출부가 +1 증설로 폴백)
    """
    def as_int(value: object) -> int | None:
        if type(value) is int:
            return value
        if isinstance(value, str) and value.strip().isdigit():
            return int(value.strip())
        return None

    matches = [
        change
        for change in authority.changes
        if str(change.get("field_path") or "").endswith("spec.replicas")
        and as_int(change.get("new_desired", change.get("after"))) == current_replicas
        and (previous := as_int(change.get("old_desired"))) is not None
        and previous > current_replicas
    ]
    if len(matches) != 1:
        return None
    return as_int(matches[0].get("old_desired"))


def target_container(manifest: JsonObject, resource_name: str) -> dict[str, Any] | None:
    containers = nested_value(manifest, "spec", "template", "spec", "containers")
    if not isinstance(containers, list):
        return None
    values = [dict(item) for item in containers if isinstance(item, dict)]
    if len(values) == 1:
        return values[0]
    deployment = command_target_name("Deployment", resource_name, {})
    matches = [item for item in values if item.get("name") == deployment]
    return matches[0] if len(matches) == 1 else None


def image_replacements(
    authority: GitOpsAuthorityContext,
    container: dict[str, Any] | None,
) -> list[ScalarFieldReplacement]:
    if container is None:
        return []
    container_name = first_str(container.get("name"))
    current_image = first_str(container.get("image"))
    suffix = f"containers[name={container_name}].image"
    matches = [
        change
        for change in authority.changes
        if str(change.get("field_path") or "").endswith(suffix)
        and first_str(change.get("new_desired"), change.get("after")) == current_image
        and first_str(change.get("old_desired"))
        and first_str(change.get("old_desired")) != current_image
    ]
    if len(matches) != 1:
        return []
    previous = first_str(matches[0].get("old_desired"))
    return [
        ScalarFieldReplacement(
            f"spec.template.spec.containers[name={container_name}].image",
            current_image,
            previous,
        )
    ]


def oom_memory_replacements(
    authority: GitOpsAuthorityContext,
    container: dict[str, Any] | None,
) -> list[ScalarFieldReplacement]:
    if container is None:
        return []
    container_name = first_str(container.get("name"))
    request = nested_value(container, "resources", "requests", "memory")
    limit = nested_value(container, "resources", "limits", "memory")
    request_bytes = memory_quantity_bytes(request)
    limit_bytes = memory_quantity_bytes(limit)
    usage_bytes = numeric_evidence_value(authority.evidence, "container_memory_working_set_bytes")
    if (
        not container_name
        or request_bytes is None
        or limit_bytes is None
        or usage_bytes is None
        or usage_bytes <= 0
    ):
        return []
    mib = 1024**2
    desired_limit_mib = max(
        math.ceil(limit_bytes * 1.25 / mib),
        math.ceil(usage_bytes * 1.25 / mib),
    )
    desired_request_mib = max(
        math.ceil(request_bytes * 1.25 / mib),
        math.ceil(usage_bytes * 0.8 / mib),
    )
    if desired_limit_mib > 4096:
        return []
    desired_request_mib = min(desired_request_mib, desired_limit_mib)
    values: list[ScalarFieldReplacement] = []
    prefix = f"spec.template.spec.containers[name={container_name}].resources"
    desired_request = f"{desired_request_mib}Mi"
    desired_limit = f"{desired_limit_mib}Mi"
    if memory_quantity_bytes(desired_request) > request_bytes:
        values.append(ScalarFieldReplacement(f"{prefix}.requests.memory", request, desired_request))
    if memory_quantity_bytes(desired_limit) > limit_bytes:
        values.append(ScalarFieldReplacement(f"{prefix}.limits.memory", limit, desired_limit))
    return values


def probe_replacements(
    container: dict[str, Any] | None,
    authority: GitOpsAuthorityContext,
) -> list[ScalarFieldReplacement]:
    if container is None:
        return []
    container_name = first_str(container.get("name"))
    prefix = f"spec.template.spec.containers[name={container_name}]"
    approved_candidates: list[ScalarFieldReplacement] = []
    for probe_name in ("readinessProbe", "livenessProbe"):
        probe = container.get(probe_name)
        if not isinstance(probe, dict):
            continue
        for suffix, current in (
            (f"{probe_name}.httpGet.path", nested_value(probe, "httpGet", "path")),
            (f"{probe_name}.httpGet.port", nested_value(probe, "httpGet", "port")),
            (f"{probe_name}.timeoutSeconds", probe.get("timeoutSeconds")),
        ):
            field_path = f"{prefix}.{suffix}"
            matches = [
                change
                for change in authority.changes
                if change.get("field_path") == field_path
                and type(change.get("new_desired", change.get("after"))) is type(current)
                and change.get("new_desired", change.get("after")) == current
                and type(change.get("old_desired")) is type(current)
                and change.get("old_desired") != current
            ]
            if len(matches) == 1:
                approved_candidates.append(
                    ScalarFieldReplacement(field_path, current, matches[0]["old_desired"])
                )
    if len(approved_candidates) == 1:
        return approved_candidates
    for probe_name in ("readinessProbe", "livenessProbe"):
        probe = container.get(probe_name)
        if not isinstance(probe, dict):
            continue
        timeout = probe.get("timeoutSeconds")
        if type(timeout) is int and 1 <= timeout < 30:
            return [
                ScalarFieldReplacement(
                    (
                        f"spec.template.spec.containers[name={container_name}]."
                        f"{probe_name}.timeoutSeconds"
                    ),
                    timeout,
                    min(timeout + 2, 30),
                )
            ]
    return []


def selector_replacements(manifest: JsonObject) -> list[ScalarFieldReplacement]:
    selector = nested_value(manifest, "spec", "selector", "matchLabels")
    labels = nested_value(manifest, "spec", "template", "metadata", "labels")
    if not isinstance(selector, dict) or not isinstance(labels, dict):
        return []
    matches = [
        (key, current, labels.get(key))
        for key, current in selector.items()
        if re.fullmatch(r"[A-Za-z0-9_-]+", str(key))
        and isinstance(current, str)
        and isinstance(labels.get(key), str)
        and current != labels.get(key)
    ]
    if len(matches) != 1:
        return []
    key, current, desired = matches[0]
    return [ScalarFieldReplacement(f"spec.selector.matchLabels.{key}", current, desired)]


def nested_value(value: object, *keys: str) -> object:
    current = value
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def numeric_evidence_value(value: object, key: str) -> float | None:
    if isinstance(value, dict):
        candidate = value.get(key)
        if type(candidate) in {int, float} and math.isfinite(float(candidate)):
            return float(candidate)
        for item in value.values():
            found = numeric_evidence_value(item, key)
            if found is not None:
                return found
    elif isinstance(value, list):
        for item in value:
            found = numeric_evidence_value(item, key)
            if found is not None:
                return found
    return None


def fallback_recovery_patch(selected: RecoveryActionCandidate) -> SafePrFilePatch:
    draft = selected.draft
    token = hashlib.sha256(selected.action_id.encode()).hexdigest()[:16]
    action = safe_path_segment(draft.action_type or "recovery")
    path = f"{SAFE_PR_FALLBACK_PATCH_DIR}/{token}-{action}.md"
    target = f"{draft.namespace}/{draft.resource_kind}/{draft.resource_name}"
    checks = "\n".join(f"- {check}" for check in selected.validation_checks) or "- 상태 확인"
    content = (
        f"# {selected.title}\n\n"
        "## 대상\n\n"
        f"- 리소스: `{target}`\n"
        f"- 원인: `{draft.params.get('root_cause', '')}`\n"
        f"- 위험도: `{selected.risk_level}`\n"
        f"- 영향 범위: `{selected.blast_radius}`\n\n"
        "## 조치\n\n"
        f"{selected.description}\n\n"
        "## 검증\n\n"
        f"{checks}\n\n"
        "## 롤백\n\n"
        f"{selected.rollback_plan}\n"
    )
    if draft.action_type == RESOURCE_REQUEST_TUNING_ACTION:
        content = (
            f"{content}\n\n"
            "## 참고 제안값\n\n"
            f"- CPU request: `{RESOURCE_REQUEST_MIN_CPU}`\n"
            f"- Memory request: `{RESOURCE_REQUEST_MIN_MEMORY}`\n\n"
            "이 값은 Opsia Safe PR v1의 최소 참고값이며 자동 적용값이 아닙니다. "
            "운영자는 실제 workload 부하, namespace quota, node capacity를 확인한 뒤 "
            "manifest에 적절한 값을 직접 반영해야 합니다.\n"
        )
    return SafePrFilePatch(
        path=path,
        content=content,
        description=f"{selected.title} 복구 검토 기록",
    )


def safe_path_segment(value: str) -> str:
    cleaned = re.sub(r"[^a-zA-Z0-9._-]+", "-", value.strip()).strip("-._")
    return cleaned or "recovery"


def as_optional_str(value: object) -> str | None:
    if value in (None, ""):
        return None
    return str(value)
