"""플랫폼 조회 도구 — LLM 이 대화 중 호출 가능한 읽기 전용 능력을 @ai.tool 로 등록.

모든 도구는 안전(읽기 전용)하고 JSON 직렬화 가능한 dict 를 반환함.
저장소 접근은 ToolContext.db(AsyncDb)로만 — 도구가 연결/트랜잭션을 직접 알지 않음.
새 도구 추가 = 이 파일(또는 다른 도메인의 tools.py)에 함수 1개(엔진 수정 없음).
"""

from __future__ import annotations

import inspect
from typing import Any

from domains.command.actions import registered_command_actions
from domains.rca.report_projection import rca_report_summary
from packages.ai.tools import ToolContext, ai

DEFAULT_INCIDENT_LIMIT = 5
DEFAULT_MESSAGE_LIMIT = 10
MAX_ROWS = 20
CONTENT_PREVIEW_CHARS = 300
RISK_ORDER = {"low": 0, "medium": 1, "high": 2}
ROUTE_ORDER = {"auto": 0, "command": 1, "draft_pr": 2, "approval_required": 3}
RECOMMENDABLE_RISKS = {"low", "medium"}
AUTOMATION_ROUTES = {"auto", "command"}
GITOPS_DIFF_STEP = "diff"
SAFE_PR_DIFF_SUBJECTS = {
    "safe_pr.patch_prepared",
    "diff.explained",
    "safe_pr.ready_for_creation",
}
INVENTORY_PUBLIC_FIELDS = (
    "inventory_key",
    "workspace_id",
    "cluster_id",
    "resource_type",
    "api_version",
    "kind",
    "namespace",
    "name",
    "uid",
    "status",
    "health",
    "labels",
    "annotations",
    "summary",
    "observed_at",
    "last_seen_at",
)


def _clamp(value: Any, default: int) -> int:
    try:
        return max(1, min(int(value), MAX_ROWS))
    except (TypeError, ValueError):
        return default


def _ctx_or_arg(value: str | None, fallback: str | None) -> str:
    return str(value or fallback or "").strip()


def _context_value(context: ToolContext, key: str) -> str:
    value = getattr(context, key, None)
    if value not in (None, ""):
        return str(value).strip()
    resource_context = context.resource_context or {}
    raw = resource_context.get(key)
    return str(raw).strip() if raw not in (None, "") else ""


async def _db_call(context: ToolContext, name: str, *args: Any, **kwargs: Any) -> Any:
    method = getattr(context.db, name, None)
    if not callable(method):
        return None
    result = method(*args, **kwargs)
    if inspect.isawaitable(result):
        return await result
    return result


def _public_inventory_resource(row: dict[str, Any]) -> dict[str, Any]:
    return {key: row.get(key) for key in INVENTORY_PUBLIC_FIELDS if key in row}


def _row_payload(row: dict[str, Any]) -> dict[str, Any]:
    payload = row.get("payload")
    return payload if isinstance(payload, dict) else {}


def _public_diff_payload(diff: dict[str, Any]) -> dict[str, Any]:
    return {
        key: diff.get(key)
        for key in (
            "resource",
            "namespace",
            "desired_image",
            "actual_image",
            "risk",
            "workspace_id",
            "repository_id",
            "binding_id",
            "application_id",
            "workflow_run_id",
            "environment",
            "cluster_id",
            "manifest_path",
            "resource_class",
            "status",
            "has_changes",
            "changes",
            "basis",
        )
        if key in diff
    }


def _event_payload(event: dict[str, Any]) -> dict[str, Any]:
    payload = event.get("payload")
    return payload if isinstance(payload, dict) else {}


def _subject_event(events: list[dict[str, Any]], subject: str) -> dict[str, Any] | None:
    return next((event for event in events if str(event.get("subject") or "") == subject), None)


def _gitops_diff_response(
    *,
    workflow_run_id: str,
    approval_id: str,
    diff: dict[str, Any],
    approval: dict[str, Any] | None = None,
    source: str,
) -> dict[str, Any]:
    risk = str(diff.get("risk") or "unknown")
    resource = str(diff.get("resource") or "unknown resource")
    namespace = str(diff.get("namespace") or "")
    changes = diff.get("changes") if isinstance(diff.get("changes"), list) else []
    return {
        "found": True,
        "source": "gitops",
        "lookup_source": source,
        "workflow_run_id": workflow_run_id,
        "approval_id": approval_id or None,
        "summary": f"{resource} GitOps diff 위험도는 {risk}입니다.",
        "reasoning": {
            "current_context": {
                "resource": resource,
                "namespace": namespace,
                "manifest_path": diff.get("manifest_path"),
                "status": diff.get("status"),
                "has_changes": diff.get("has_changes"),
            },
            "evidence": {
                "diff": _public_diff_payload(diff),
                "approval_status": approval.get("status") if approval else None,
                "approval_reason": approval.get("reason") if approval else None,
            },
            "risk_notes": [
                "이 diff는 승인되면 command.requested를 거쳐 target agent apply로 이어질 수 있습니다.",
                f"변경 항목은 {len(changes)}건입니다."
                if changes
                else "세부 changes가 없거나 아직 투영되지 않았습니다.",
            ],
        },
        "next_checks": [
            "변경 대상 namespace/resource가 의도한 배포 대상인지 확인합니다.",
            "approval_id가 있으면 승인 전 diff와 정책 사유를 함께 검토합니다.",
            "승인 후 command/agent 단계에서 실패 여부를 확인합니다.",
        ],
        "possible_actions": {
            "explain_only": True,
            "can_execute": False,
            "handoff": "승인/적용은 별도 UI 또는 command flow에서 사용자 확인 후 진행해야 합니다.",
        },
        "caution": {
            "risk_level": risk,
            "approval_required": bool(
                approval and str(approval.get("status") or "") == "requested"
            ),
            "applies_to_cluster": True,
        },
    }


def _safe_pr_diff_response(
    *,
    workflow_run_id: str,
    events: list[dict[str, Any]],
) -> dict[str, Any]:
    patch_event = _subject_event(events, "safe_pr.patch_prepared")
    explained_event = _subject_event(events, "diff.explained")
    ready_event = _subject_event(events, "safe_pr.ready_for_creation")
    patch_payload = _event_payload(patch_event or {})
    explained_payload = _event_payload(explained_event or {})
    ready_payload = _event_payload(ready_event or {})
    patch = patch_payload.get("patch") if isinstance(patch_payload.get("patch"), dict) else {}
    patches = patch.get("patches") if isinstance(patch.get("patches"), list) else []
    risk = str(explained_payload.get("risk") or ready_payload.get("risk") or "unknown")
    return {
        "found": True,
        "source": "safe_pr",
        "workflow_run_id": workflow_run_id,
        "summary": str(
            explained_payload.get("summary")
            or ready_payload.get("summary")
            or "Safe PR patch 초안 위험도를 설명할 수 있습니다."
        ),
        "reasoning": {
            "current_context": {
                "title": patch_payload.get("title"),
                "provider": patch_payload.get("provider") or patch.get("provider"),
                "manifest_path": patch_payload.get("manifest_path") or patch.get("manifest_path"),
                "ready_for_creation": explained_payload.get("ready_for_creation"),
            },
            "evidence": {
                "patch_count": len(patches),
                "patch_paths": [item.get("path") for item in patches if isinstance(item, dict)],
                "patch_sha256": patch.get("patch_sha256"),
                "approval_ref": patch_payload.get("approval_ref") or patch.get("approval_ref"),
                "policy_decision_ref": patch_payload.get("policy_decision_ref")
                or patch.get("policy_decision_ref"),
                "diff_explained": explained_payload,
                "ready": ready_payload if ready_payload else None,
            },
            "risk_notes": [
                "이 설명은 PR 생성 전 patch 초안 기준이며, 아직 target cluster에 apply된 상태가 아닙니다.",
                "PR 생성 가능 여부는 diff.explained 또는 safe_pr.ready_for_creation 이벤트 기준입니다.",
            ],
        },
        "next_checks": [
            "patch_paths가 의도한 manifest 파일인지 확인합니다.",
            "diff.explained의 risk와 reason을 PR 리뷰 기준으로 확인합니다.",
            "PR 생성 후에는 safe_pr.created의 pr_url과 patch_sha256을 확인합니다.",
        ],
        "possible_actions": {
            "explain_only": True,
            "can_execute": False,
            "handoff": "PR 생성/머지는 scm-worker와 Git provider 리뷰 흐름에서 진행해야 합니다.",
        },
        "caution": {
            "risk_level": risk,
            "approval_required": bool(
                patch_payload.get("approval_ref") or patch.get("approval_ref")
            ),
            "applies_to_cluster": False,
        },
    }


def _report_matches_context(row: dict[str, Any], context: ToolContext) -> bool:
    payload = _row_payload(row)
    cluster_id = row.get("cluster_id") or payload.get("cluster_id")
    if context.cluster_id and cluster_id and str(cluster_id) != context.cluster_id:
        return False
    if not context.name:
        return True
    incident = payload.get("incident") if isinstance(payload.get("incident"), dict) else {}
    detail = payload.get("rca_detail") if isinstance(payload.get("rca_detail"), dict) else {}
    candidates = [
        row.get("resource_name"),
        row.get("resource"),
        incident.get("resource_name"),
        incident.get("resource"),
        detail.get("resource_name"),
    ]
    return any(str(candidate) == context.name for candidate in candidates if candidate)


def _string_set(value: Any) -> set[str]:
    if value is None:
        return set()
    if isinstance(value, str):
        return {value} if value else set()
    if isinstance(value, (list, tuple, set)):
        return {str(item) for item in value if item not in (None, "")}
    return {str(value)}


def _plan_payload(record: dict[str, Any] | None) -> dict[str, Any]:
    payload = record.get("payload") if isinstance(record, dict) else None
    return payload if isinstance(payload, dict) else {}


def _candidate_draft(candidate: dict[str, Any]) -> dict[str, Any]:
    draft = candidate.get("draft")
    return draft if isinstance(draft, dict) else {}


def _candidate_action_type(candidate: dict[str, Any]) -> str:
    draft = _candidate_draft(candidate)
    return str(draft.get("action_type") or candidate.get("action_type") or "")


def _candidate_sort_key(candidate: dict[str, Any]) -> tuple[int, int, int, float, str]:
    risk = str(candidate.get("risk_level") or "").lower()
    route = str(candidate.get("route") or "")
    rank = candidate.get("rank")
    score = candidate.get("score")
    try:
        rank_value = int(rank)
    except (TypeError, ValueError):
        rank_value = 999
    try:
        score_value = float(score)
    except (TypeError, ValueError):
        score_value = 0.0
    return (
        RISK_ORDER.get(risk, 99),
        1 if candidate.get("approval_required") else 0,
        ROUTE_ORDER.get(route, 99),
        rank_value,
        f"{-score_value:.8f}:{candidate.get('action_id') or ''}",
    )


def _matching_command_action(action_type: str) -> Any | None:
    for spec in registered_command_actions():
        if spec.action == action_type or action_type in spec.recovery_aliases:
            return spec
    return None


def _target_is_explicit(plan: dict[str, Any], candidate: dict[str, Any]) -> bool:
    target = plan.get("target") if isinstance(plan.get("target"), dict) else {}
    draft = _candidate_draft(candidate)
    return bool(
        target.get("cluster_id")
        and (
            target.get("resource_name")
            or target.get("name")
            or draft.get("resource_name")
            or candidate.get("resource_name")
        )
    )


def _namespace_allowed(candidate: dict[str, Any]) -> bool:
    draft = _candidate_draft(candidate)
    namespace = str(draft.get("namespace") or "")
    spec = _matching_command_action(_candidate_action_type(candidate))
    if spec is None:
        return False
    allowed = set(spec.allowed_namespaces)
    return bool(namespace and namespace in allowed)


def _automatic_recommendation(plan: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    route = str(candidate.get("route") or "")
    risk = str(candidate.get("risk_level") or "").lower()
    draft = _candidate_draft(candidate)
    explicit_target = _target_is_explicit(plan, candidate)
    namespace_allowed = _namespace_allowed(candidate)
    has_recovery_path = bool(candidate.get("rollback_plan") or candidate.get("validation_checks"))
    eligible = bool(
        risk == "low"
        and not candidate.get("approval_required")
        and route in AUTOMATION_ROUTES
        and namespace_allowed
        and explicit_target
        and has_recovery_path
    )
    return {
        "eligible": eligible,
        "why_safe": [
            reason
            for condition, reason in (
                (risk == "low", "risk_level이 low입니다."),
                (
                    not candidate.get("approval_required"),
                    "approval_required가 false입니다.",
                ),
                (route in AUTOMATION_ROUTES, "route가 자동/명령 실행 경로입니다."),
                (namespace_allowed, "대상 namespace가 허용 범위 안입니다."),
                (explicit_target, "대상 리소스가 명확합니다."),
                (
                    has_recovery_path,
                    "검증 또는 rollback/recovery 경로가 정의되어 있습니다.",
                ),
            )
            if condition
        ],
        "blocking_reasons": [
            reason
            for condition, reason in (
                (risk == "low", "risk_level이 low가 아닙니다."),
                (
                    not candidate.get("approval_required"),
                    "승인이 필요한 조치입니다.",
                ),
                (route in AUTOMATION_ROUTES, "자동/명령 실행 경로가 아닙니다."),
                (namespace_allowed, "대상 namespace가 허용 범위 밖이거나 확인되지 않았습니다."),
                (explicit_target, "대상 리소스가 명확하지 않습니다."),
                (
                    has_recovery_path,
                    "검증 또는 rollback/recovery 경로가 없습니다.",
                ),
            )
            if not condition
        ],
        "expected_impact": [
            f"{draft.get('resource_kind') or 'resource'} {draft.get('resource_name') or 'target'}에 {candidate.get('title') or candidate.get('action_id')} 조치를 적용합니다.",
            "Manifest나 Config 변경 여부는 recovery candidate의 route/action_type 기준으로 검토해야 합니다.",
        ],
        "pre_checks": list(candidate.get("prerequisites") or [])
        + list(candidate.get("validation_checks") or []),
        "rollback_or_recovery": [candidate.get("rollback_plan")]
        if candidate.get("rollback_plan")
        else [],
    }


def _candidate_summary(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "action_id": candidate.get("action_id"),
        "title": candidate.get("title"),
        "description": candidate.get("description"),
        "route": candidate.get("route"),
        "risk_level": candidate.get("risk_level"),
        "approval_required": candidate.get("approval_required"),
        "rank": candidate.get("rank"),
        "score": candidate.get("score"),
        "action_type": _candidate_action_type(candidate),
    }


def _recommendation_reason(candidate: dict[str, Any]) -> str:
    risk = candidate.get("risk_level") or "unknown"
    route = candidate.get("route") or "unknown"
    approval = (
        "승인이 필요합니다"
        if candidate.get("approval_required")
        else "승인 없이 진행 가능한 후보입니다"
    )
    return (
        f"{candidate.get('title') or candidate.get('action_id')} 후보가 현재 recovery plan에서 "
        f"risk_level={risk}, route={route}이며 {approval}."
    )


def _recommendation_summary(candidate: dict[str, Any], automation: dict[str, Any]) -> str:
    title = candidate.get("title") or candidate.get("action_id")
    suffix = "자동 실행 후보입니다." if automation["eligible"] else "검토 후 진행할 후보입니다."
    return f"추천 조치는 {title}입니다. {suffix}"


@ai.tool(
    name="list_recent_incidents",
    description="Recent RCA reports (root cause, recommended action) for this workspace.",
    parameters={
        "limit": {"type": "integer", "description": f"max rows (1-{MAX_ROWS}, default 5)"},
    },
)
async def list_recent_incidents(
    context: ToolContext, limit: int = DEFAULT_INCIDENT_LIMIT
) -> dict[str, Any]:
    rows = await context.db.list_rca_reports(
        context.workspace_id, limit=_clamp(limit, DEFAULT_INCIDENT_LIMIT)
    )
    return {
        "incidents": [
            {
                "root_cause": row.get("root_cause"),
                "action": row.get("action"),
                "correlation_id": row.get("correlation_id"),
                "created_at": str(row.get("created_at") or ""),
            }
            for row in rows
        ]
    }


@ai.tool(
    name="get_inventory_resource_detail",
    description="Kubernetes inventory detail for the current or requested resource, including related pods and involvedObject events.",
    parameters={
        "cluster_id": {"type": "string", "description": "cluster id; defaults to chat context"},
        "resource_type": {
            "type": "string",
            "description": "pod/node/service/workload; defaults to chat context",
        },
        "kind": {"type": "string", "description": "Kubernetes kind; defaults to chat context"},
        "name": {"type": "string", "description": "resource name; defaults to chat context"},
        "namespace": {"type": "string", "description": "namespace for namespaced resources"},
    },
)
async def get_inventory_resource_detail(
    context: ToolContext,
    cluster_id: str = "",
    resource_type: str = "",
    kind: str = "",
    name: str = "",
    namespace: str = "",
) -> dict[str, Any]:
    resolved_cluster = _ctx_or_arg(cluster_id, context.cluster_id)
    resolved_type = _ctx_or_arg(resource_type, context.resource_type)
    resolved_kind = _ctx_or_arg(kind, context.kind)
    resolved_name = _ctx_or_arg(name, context.name)
    resolved_namespace = _ctx_or_arg(namespace, context.namespace) or None
    if not all((resolved_cluster, resolved_type, resolved_kind, resolved_name)):
        return {
            "found": False,
            "error": "cluster_id, resource_type, kind and name are required",
        }
    resource = await context.db.get_inventory_resource(
        workspace_id=context.workspace_id,
        cluster_id=resolved_cluster,
        resource_type=resolved_type,
        kind=resolved_kind,
        namespace=resolved_namespace,
        name=resolved_name,
    )
    if resource is None:
        return {
            "found": False,
            "identity": {
                "cluster_id": resolved_cluster,
                "resource_type": resolved_type,
                "kind": resolved_kind,
                "namespace": resolved_namespace,
                "name": resolved_name,
            },
        }
    related = await context.db.list_related_inventory_resources(
        workspace_id=context.workspace_id,
        cluster_id=resolved_cluster,
        resource=resource,
        limit=20,
    )
    events = await context.db.list_resource_events(
        workspace_id=context.workspace_id,
        cluster_id=resolved_cluster,
        resource=resource,
        limit=20,
    )
    return {
        "found": True,
        "resource": _public_inventory_resource(dict(resource)),
        "related": {
            group: [_public_inventory_resource(dict(item)) for item in rows[:20]]
            for group, rows in related.items()
        },
        "events": [_public_inventory_resource(dict(item)) for item in events[:20]],
    }


@ai.tool(
    name="list_resource_rca_reports",
    description="Recent RCA reports filtered to the chat resource context when possible.",
    parameters={
        "limit": {"type": "integer", "description": f"max rows (1-{MAX_ROWS}, default 5)"},
    },
)
async def list_resource_rca_reports(
    context: ToolContext, limit: int = DEFAULT_INCIDENT_LIMIT
) -> dict[str, Any]:
    rows = await context.db.list_rca_reports(
        context.workspace_id, limit=_clamp(limit, DEFAULT_INCIDENT_LIMIT)
    )
    filtered = [dict(row) for row in rows if _report_matches_context(dict(row), context)]
    return {
        "reports": [
            {
                "root_cause": row.get("root_cause"),
                "action": row.get("action"),
                "correlation_id": row.get("correlation_id"),
                "created_at": str(row.get("created_at") or ""),
                "cluster_id": row.get("cluster_id") or _row_payload(row).get("cluster_id"),
            }
            for row in filtered[:MAX_ROWS]
        ]
    }


@ai.tool(
    name="get_incident_rca_context",
    description="RCA report and recovery plan for a specific incident correlation in the chat context.",
    parameters={
        "correlation_id": {
            "type": "string",
            "description": "incident correlation id; defaults to chat context",
        },
    },
)
async def get_incident_rca_context(
    context: ToolContext, correlation_id: str = ""
) -> dict[str, Any]:
    resolved = _ctx_or_arg(correlation_id, _context_value(context, "correlation_id"))
    if not resolved:
        return {"found": False, "error": "correlation_id is required"}
    rows = await context.db.list_rca_reports(context.workspace_id, limit=MAX_ROWS)
    reports = [
        rca_report_summary(dict(row))
        for row in rows
        if str(row.get("correlation_id") or "") == resolved
    ]
    recovery_record = await context.db.get_recovery_plan_by_correlation(
        resolved, context.workspace_id
    )
    plan = recovery_record.get("payload") if isinstance(recovery_record, dict) else None
    plan_payload = plan if isinstance(plan, dict) else {}
    return {
        "found": bool(reports or plan_payload),
        "correlation_id": resolved,
        "reports": reports,
        "recovery_plan": {
            "plan_id": plan_payload.get("plan_id"),
            "status": recovery_record.get("status") if isinstance(recovery_record, dict) else None,
            "recommended_action_id": plan_payload.get("recommended_action_id"),
            "selection_required": plan_payload.get("selection_required"),
            "target": plan_payload.get("target"),
            "candidates": [
                {
                    "action_id": candidate.get("action_id"),
                    "title": candidate.get("title"),
                    "description": candidate.get("description"),
                    "route": candidate.get("route"),
                    "risk_level": candidate.get("risk_level"),
                    "approval_required": candidate.get("approval_required"),
                }
                for candidate in plan_payload.get("candidates", [])
                if isinstance(candidate, dict)
            ],
        }
        if plan_payload
        else None,
    }


@ai.tool(
    name="recommend_recovery_action",
    description="Recommend the safest recovery action from a recovery plan without creating requests or executing commands.",
    parameters={
        "correlation_id": {
            "type": "string",
            "description": "incident correlation id; defaults to chat context",
        },
        "plan_id": {
            "type": "string",
            "description": "recovery plan id; preferred when available",
        },
        "exclude_action_ids": {
            "type": "array",
            "description": "recovery action ids to exclude from recommendation",
        },
        "exclude_action_types": {
            "type": "array",
            "description": "recovery action types to exclude, e.g. rollout_restart",
        },
    },
)
async def recommend_recovery_action(
    context: ToolContext,
    correlation_id: str = "",
    plan_id: str = "",
    exclude_action_ids: list[str] | None = None,
    exclude_action_types: list[str] | None = None,
) -> dict[str, Any]:
    resolved_plan_id = _ctx_or_arg(plan_id, "")
    resolved_correlation = _ctx_or_arg(correlation_id, _context_value(context, "correlation_id"))
    record = None
    if resolved_plan_id:
        record = await context.db.get_recovery_plan(resolved_plan_id, context.workspace_id)
    if record is None and resolved_correlation:
        record = await context.db.get_recovery_plan_by_correlation(
            resolved_correlation, context.workspace_id
        )
    if record is None:
        return {
            "found": False,
            "error": "plan_id or correlation_id is required and must match a recovery plan",
        }

    plan = _plan_payload(record)
    if not plan:
        return {"found": False, "error": "recovery plan payload is missing"}

    excluded_ids = _string_set(exclude_action_ids)
    excluded_types = _string_set(exclude_action_types)
    candidates = [
        dict(candidate) for candidate in plan.get("candidates", []) if isinstance(candidate, dict)
    ]
    excluded = [
        candidate
        for candidate in candidates
        if str(candidate.get("action_id") or "") in excluded_ids
        or _candidate_action_type(candidate) in excluded_types
    ]
    available = [candidate for candidate in candidates if candidate not in excluded]
    high_risk = [
        candidate
        for candidate in available
        if str(candidate.get("risk_level") or "").lower() not in RECOMMENDABLE_RISKS
    ]
    recommendable = [candidate for candidate in available if candidate not in high_risk]
    preferred_id = str(plan.get("recommended_action_id") or "")
    recommended = next(
        (
            candidate
            for candidate in recommendable
            if str(candidate.get("action_id") or "") == preferred_id
        ),
        None,
    )
    if recommended is None and recommendable:
        recommended = sorted(recommendable, key=_candidate_sort_key)[0]

    alternatives = [
        _candidate_summary(candidate)
        for candidate in sorted(recommendable, key=_candidate_sort_key)
        if candidate is not recommended
    ]
    not_recommended = [
        {
            **_candidate_summary(candidate),
            "reason": "high risk 또는 알 수 없는 risk_level 후보라 수동 검토가 필요합니다.",
        }
        for candidate in high_risk
    ] + [
        {
            **_candidate_summary(candidate),
            "reason": "사용자 요청으로 추천 후보에서 제외되었습니다.",
        }
        for candidate in excluded
    ]

    if recommended is None:
        return {
            "found": True,
            "plan_id": plan.get("plan_id") or record.get("plan_id"),
            "correlation_id": record.get("correlation_id") or resolved_correlation,
            "summary": "추천 가능한 recovery action이 없습니다.",
            "reasoning": {
                "current_context": plan.get("summary"),
                "evidence": [],
                "why_recommended": [],
                "risk_notes": ["남은 후보가 없거나 high risk/manual review 대상입니다."],
            },
            "next_checks": [],
            "possible_actions": {
                "recommended": None,
                "alternatives": alternatives,
                "not_recommended": not_recommended,
            },
            "caution": {
                "risk_level": None,
                "approval_required": None,
                "automatic_candidate": False,
                "automation": {"eligible": False, "why_safe": [], "blocking_reasons": []},
            },
        }

    automation = _automatic_recommendation(plan, recommended)
    recommendation = {
        **_candidate_summary(recommended),
        "automatic_candidate": automation["eligible"],
    }
    return {
        "found": True,
        "plan_id": plan.get("plan_id") or record.get("plan_id"),
        "correlation_id": record.get("correlation_id") or resolved_correlation,
        "status": record.get("status"),
        "target": plan.get("target"),
        "summary": _recommendation_summary(recommended, automation),
        "reasoning": {
            "current_context": plan.get("summary"),
            "evidence": list(recommended.get("evidence_refs") or []),
            "why_recommended": [_recommendation_reason(recommended)] + automation["why_safe"],
            "risk_notes": [
                note
                for note in (
                    recommended.get("blast_radius"),
                    recommended.get("description"),
                    "medium risk 후보는 승인 필요 여부와 영향 범위를 함께 검토해야 합니다."
                    if str(recommended.get("risk_level") or "").lower() == "medium"
                    else None,
                )
                if note
            ],
        },
        "next_checks": automation["pre_checks"],
        "possible_actions": {
            "recommended": recommendation,
            "alternatives": alternatives,
            "not_recommended": not_recommended,
        },
        "caution": {
            "risk_level": recommended.get("risk_level"),
            "approval_required": recommended.get("approval_required"),
            "automatic_candidate": automation["eligible"],
            "expected_impact": automation["expected_impact"],
            "automation": {
                "eligible": automation["eligible"],
                "why_safe": automation["why_safe"],
                "blocking_reasons": automation["blocking_reasons"],
                "handoff": {
                    "next_step": "create_command_request" if automation["eligible"] else None,
                    "requires_user_confirmation": True,
                },
            },
        },
    }


@ai.tool(
    name="explain_diff_risk",
    description="Explain a selected GitOps diff or Safe PR patch diff from screen context without approving or executing it.",
    parameters={
        "diff_source": {
            "type": "string",
            "description": "gitops or safe_pr; defaults to chat context",
        },
        "workflow_run_id": {
            "type": "string",
            "description": "workflow run id from the current diff/workflow screen",
        },
        "approval_id": {
            "type": "string",
            "description": "GitOps approval id; preferred for approval screens",
        },
    },
)
async def explain_diff_risk(
    context: ToolContext,
    diff_source: str = "",
    workflow_run_id: str = "",
    approval_id: str = "",
) -> dict[str, Any]:
    source = _ctx_or_arg(diff_source, _context_value(context, "diff_source")).lower()
    resolved_workflow = _ctx_or_arg(workflow_run_id, _context_value(context, "workflow_run_id"))
    resolved_approval = _ctx_or_arg(approval_id, _context_value(context, "approval_id"))
    application_id = _context_value(context, "application_id")

    if source not in {"gitops", "safe_pr"}:
        return {
            "found": False,
            "error": "diff_source must be gitops or safe_pr",
            "missing_context": ["diff_source"],
        }

    if source == "gitops":
        approval: dict[str, Any] | None = None
        diff: dict[str, Any] = {}
        lookup_source = ""
        if resolved_approval:
            approval_record = await _db_call(
                context,
                "get_workflow_approval",
                resolved_approval,
                context.workspace_id,
            )
            approval = dict(approval_record or {}) if isinstance(approval_record, dict) else None
            details = approval.get("details") if approval else None
            details_payload = details if isinstance(details, dict) else {}
            raw_diff = details_payload.get("diff")
            diff = dict(raw_diff) if isinstance(raw_diff, dict) else {}
            resolved_workflow = resolved_workflow or str(
                approval.get("workflow_run_id") if approval else ""
            )
            lookup_source = "approval"

        if not diff and resolved_workflow:
            step_details = await _db_call(
                context,
                "get_workflow_step_details",
                resolved_workflow,
                GITOPS_DIFF_STEP,
            )
            if isinstance(step_details, dict):
                raw_diff = (
                    step_details.get("diff")
                    if isinstance(step_details.get("diff"), dict)
                    else step_details
                )
                diff = dict(raw_diff) if isinstance(raw_diff, dict) else {}
            lookup_source = lookup_source or "workflow_step"

        if not diff:
            missing = []
            if not resolved_approval:
                missing.append("approval_id")
            if not resolved_workflow:
                missing.append("workflow_run_id")
            return {
                "found": False,
                "source": "gitops",
                "error": "GitOps diff context was not found",
                "missing_context": missing or ["diff"],
            }

        return _gitops_diff_response(
            workflow_run_id=resolved_workflow,
            approval_id=resolved_approval,
            diff=diff,
            approval=approval,
            source=lookup_source,
        )

    if not resolved_workflow:
        return {
            "found": False,
            "source": "safe_pr",
            "error": "workflow_run_id is required for Safe PR diff lookup",
            "missing_context": ["workflow_run_id"],
        }

    events = await _db_call(
        context,
        "list_release_safe_pr_diff_events",
        context.workspace_id,
        resolved_workflow,
        application_id=application_id or None,
        limit=20,
    )
    event_rows = [dict(event) for event in events or [] if isinstance(event, dict)]
    available_subjects = {str(event.get("subject") or "") for event in event_rows}
    if not (available_subjects & SAFE_PR_DIFF_SUBJECTS):
        return {
            "found": False,
            "source": "safe_pr",
            "workflow_run_id": resolved_workflow,
            "error": "Safe PR patch/diff events were not found",
            "missing_context": ["safe_pr.patch_prepared", "diff.explained"],
            "available_subjects": sorted(subject for subject in available_subjects if subject),
        }
    return _safe_pr_diff_response(workflow_run_id=resolved_workflow, events=event_rows)


@ai.tool(
    name="get_conversation_summary",
    description="Recent messages of an AI conversation in this workspace.",
    parameters={
        "conversation_id": {
            "type": "string",
            "description": "conversation id (e.g. aic-...)",
            "required": True,
        },
        "limit": {"type": "integer", "description": f"max messages (1-{MAX_ROWS}, default 10)"},
    },
)
async def get_conversation_summary(
    context: ToolContext, conversation_id: str, limit: int = DEFAULT_MESSAGE_LIMIT
) -> dict[str, Any]:
    rows = await context.db.list_ai_messages(
        context.workspace_id,
        str(conversation_id),
        newest=_clamp(limit, DEFAULT_MESSAGE_LIMIT),
    )
    return {
        "conversation_id": str(conversation_id),
        "messages": [
            {
                "role": row.get("role"),
                "content": str(row.get("content") or "")[:CONTENT_PREVIEW_CHARS],
                "created_at": str(row.get("created_at") or ""),
            }
            for row in rows
        ],
    }


@ai.tool(
    name="list_command_actions",
    description="Registered platform command actions with their policy metadata.",
)
async def list_command_actions(context: ToolContext) -> dict[str, Any]:
    return {
        "actions": [
            {
                "action": spec.action,
                "recovery_aliases": list(spec.recovery_aliases),
                "allowed_namespaces": list(spec.allowed_namespaces),
                "requires_approval": spec.requires_approval,
                "requires_approval_outside_sandbox": (spec.requires_approval_outside_sandbox),
            }
            for spec in registered_command_actions()
        ]
    }
