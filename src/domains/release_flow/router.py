"""Release-flow management API."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query, Response
from sqlalchemy.exc import IntegrityError

from domains.alert.events import AlertRequestedBody
from domains.gitops.events import GitWebhookReceivedBody
from domains.gitops.repository import (
    derive_workflow_run_id,
)
from domains.identity.dependencies import (
    require_cluster_access,
    require_resource_access,
    require_session,
)
from domains.release_flow import _support as release_support
from domains.release_flow import policy as release_policy
from domains.release_flow import readiness as release_readiness
from domains.release_flow import report as release_report
from domains.release_flow import verification as release_verification
from domains.release_flow.execution import (
    dry_run_correlation_id,
    dry_run_event_id,
    execution_profile,
)
from domains.release_flow.manifest import render_release_step_manifest
from domains.release_flow.preview import build_release_plan_preview
from domains.release_flow.repository import (
    ReleasePlanWorkspaceMismatchError,
    derive_release_plan_id,
)
from packages.config.constants import Sandbox, Target
from packages.contracts.auth import Actor
from packages.contracts.gateway import limits as gateway_limits
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.requests import (
    ReleaseManifestRenderRequest,
    ReleaseManifestSafePrRequest,
    ReleasePlanArchiveRequest,
    ReleasePlanRestoreRequest,
    ReleasePlanUpsertRequest,
    ReleaseRunActionRequest,
)
from packages.contracts.gateway.responses import (
    ReleaseAuditListResponse,
    ReleaseManifestRenderResponse,
    ReleaseManifestSafePrResponse,
    ReleasePlanDispatchResponse,
    ReleasePlanListResponse,
    ReleasePlanPreviewResponse,
    ReleasePlanResponse,
    ReleaseReadinessResponse,
    ReleaseRunAlertResponse,
    ReleaseRunHandoffResponse,
    ReleaseRunListResponse,
    ReleaseRunReportResponse,
    ReleaseRunResponse,
    ReleaseRunSummaryResponse,
)
from packages.contracts.identity import (
    DEFAULT_WORKSPACE_ID,
    AccessResourceType,
    Permission,
    ServiceRole,
)
from packages.runtime.dependencies import get_db, get_events
from packages.storage.engine import unit_of_work_or_null

router = APIRouter()
HTTP_NOT_FOUND = 404
HTTP_CONFLICT = 409
HTTP_UNPROCESSABLE_ENTITY = 422
RELEASE_PLAN_NOT_FOUND = "release plan not found"
EXPLICIT_RELEASE_PLAN_ID_NOT_ALLOWED = "plan_id must not be provided when creating a release plan"
EMPTY_RELEASE_PLAN_NOT_ALLOWED = "release plan must contain at least one application step"
RELEASE_PLAN_WORKSPACE_MUTATION_LOCK = "\x00workspace-mutation"
RELEASE_RUN_NOT_FOUND = "release run not found"
RELEASE_PLAN_BLOCKED = "release plan has blockers"
RELEASE_RUN_BLOCKED = "release run cannot advance"
BLOCKING_RUN_STATUSES = {"running", "paused", "rollback_requested", "waiting_for_approval"}

# Compatibility exports: callers historically import these helpers from router.
DEFAULT_RELEASE_VERIFICATION_TIMEOUT_MINUTES = (
    release_verification.DEFAULT_RELEASE_VERIFICATION_TIMEOUT_MINUTES
)
VERIFICATION_JOB_FAILED_STATUSES = release_verification.VERIFICATION_JOB_FAILED_STATUSES
VERIFICATION_JOB_PENDING_STATUSES = release_verification.VERIFICATION_JOB_PENDING_STATUSES
first_environment = release_support.first_environment
int_field = release_support.int_field
parse_release_window_time = release_support.parse_release_window_time
plan_settings_value = release_support.plan_settings_value
release_step_index = release_support.release_step_index
release_step_index_in_steps = release_support.release_step_index_in_steps
release_window_bound_label = release_support.release_window_bound_label
step_config = release_support.step_config
unique_non_empty = release_support.unique_non_empty
release_current_wave_health_blockers = release_verification.release_current_wave_health_blockers
release_health_check_path = release_verification.release_health_check_path
release_live_evidence_url_is_valid = release_verification.release_live_evidence_url_is_valid
release_run_has_failed_verification = release_verification.release_run_has_failed_verification
release_run_has_timed_out_verification = release_verification.release_run_has_timed_out_verification
release_run_verification_jobs = release_verification.release_run_verification_jobs
release_verification_evidence_key = release_verification.release_verification_evidence_key
release_verification_evidence_present = release_verification.release_verification_evidence_present
release_verification_job_advance_blockers = (
    release_verification.release_verification_job_advance_blockers
)
release_verification_job_id = release_verification.release_verification_job_id
release_verification_job_pending_timeouts = (
    release_verification.release_verification_job_pending_timeouts
)
release_verification_job_specs = release_verification.release_verification_job_specs
release_verification_job_status = release_verification.release_verification_job_status
release_verification_override_reason = release_verification.release_verification_override_reason
release_verification_timeout_alert_summary = (
    release_verification.release_verification_timeout_alert_summary
)
release_verification_timeout_minutes = release_verification.release_verification_timeout_minutes
release_verification_url = release_verification.release_verification_url
release_verification_url_is_valid = release_verification.release_verification_url_is_valid
release_execution_blockers = release_policy.release_execution_blockers
plan_with_safe_pr_evidence = release_policy.plan_with_safe_pr_evidence
safe_pr_gate_required = release_policy.safe_pr_gate_required
safe_pr_created_evidence_for_step = release_policy.safe_pr_created_evidence_for_step
release_safe_pr_evidence_blockers = release_policy.release_safe_pr_evidence_blockers
safe_pr_evidence_candidates = release_policy.safe_pr_evidence_candidates
safe_pr_workflow_run_id = release_policy.safe_pr_workflow_run_id
safe_pr_workflow_run_ids = release_policy.safe_pr_workflow_run_ids
safe_pr_expected_evidence = release_policy.safe_pr_expected_evidence
safe_pr_evidence_matches = release_policy.safe_pr_evidence_matches
safe_pr_evidence_mismatch_reasons = release_policy.safe_pr_evidence_mismatch_reasons
safe_pr_evidence_field_reason = release_policy.safe_pr_evidence_field_reason
safe_pr_short_values = release_policy.safe_pr_short_values
safe_pr_short_value = release_policy.safe_pr_short_value
safe_pr_evidence_is_current = release_policy.safe_pr_evidence_is_current
safe_pr_evidence_max_age = release_policy.safe_pr_evidence_max_age
safe_pr_evidence_field_matches = release_policy.safe_pr_evidence_field_matches
safe_pr_evidence_pr_url_matches_provider = release_policy.safe_pr_evidence_pr_url_matches_provider
safe_pr_required_evidence_field_matches = release_policy.safe_pr_required_evidence_field_matches
generated_safe_pr_manifest_path = release_policy.generated_safe_pr_manifest_path
generated_safe_pr_patch_sha256 = release_policy.generated_safe_pr_patch_sha256
generated_safe_pr_rollback_patch_available = (
    release_policy.generated_safe_pr_rollback_patch_available
)
safe_pr_workflow_basis = release_policy.safe_pr_workflow_basis
release_application_context = release_policy.release_application_context
required_release_input_blockers = release_policy.required_release_input_blockers
release_diagnostics_blockers = release_policy.release_diagnostics_blockers
release_diagnostics_override_reason = release_policy.release_diagnostics_override_reason
release_diagnostics_bypassed = release_policy.release_diagnostics_bypassed
release_rollback_policy_blockers = release_policy.release_rollback_policy_blockers
release_rollback_override_reason = release_policy.release_rollback_override_reason
release_rollback_policy_bypassed = release_policy.release_rollback_policy_bypassed
release_production_approval_evidence_blockers = (
    release_policy.release_production_approval_evidence_blockers
)
release_approval_granted_by = release_policy.release_approval_granted_by
release_approval_reason = release_policy.release_approval_reason
release_approval_granted_at = release_policy.release_approval_granted_at
release_approval_granted_at_label = release_policy.release_approval_granted_at_label
release_approval_is_expired = release_policy.release_approval_is_expired
release_approval_is_in_future = release_policy.release_approval_is_in_future
approval_max_age = release_policy.approval_max_age
approval_max_age_label = release_policy.approval_max_age_label
release_production_change_ticket_blockers = release_policy.release_production_change_ticket_blockers
release_production_change_ticket_bypassed = release_policy.release_production_change_ticket_bypassed
release_production_change_override_reason = release_policy.release_production_change_override_reason
release_production_window_blockers = release_policy.release_production_window_blockers
release_production_window_bypassed = release_policy.release_production_window_bypassed
release_production_freeze_blockers = release_policy.release_production_freeze_blockers
release_production_freeze_bypassed = release_policy.release_production_freeze_bypassed
release_freeze_override_reason = release_policy.release_freeze_override_reason
release_window_override_reason = release_policy.release_window_override_reason
release_production_runbook_blockers = release_policy.release_production_runbook_blockers
release_production_runbook_bypassed = release_policy.release_production_runbook_bypassed
release_runbook_url = release_policy.release_runbook_url
release_runbook_url_is_valid = release_policy.release_runbook_url_is_valid
release_runbook_override_reason = release_policy.release_runbook_override_reason
release_production_owner_blockers = release_policy.release_production_owner_blockers
release_owner_contact = release_policy.release_owner_contact
release_production_abort_criteria_blockers = (
    release_policy.release_production_abort_criteria_blockers
)
release_production_abort_criteria_bypassed = (
    release_policy.release_production_abort_criteria_bypassed
)
release_abort_criteria = release_policy.release_abort_criteria
release_abort_criteria_override_reason = release_policy.release_abort_criteria_override_reason
release_window_bounds = release_policy.release_window_bounds
release_freeze_window_bounds = release_policy.release_freeze_window_bounds
release_freeze_window_is_active = release_policy.release_freeze_window_is_active
release_production_steps_for_wave = release_policy.release_production_steps_for_wave
release_step_targets_production = release_policy.release_step_targets_production
active_release_run_blockers = release_policy.active_release_run_blockers
steps_for_wave = release_policy.steps_for_wave
generated_manifest_safe_pr_body = release_policy.generated_manifest_safe_pr_body
generated_manifest_safe_pr_blockers = release_policy.generated_manifest_safe_pr_blockers
generated_manifest_rollback_patches = release_policy.generated_manifest_rollback_patches
rollback_manifest_image = release_policy.rollback_manifest_image
generated_manifest_rollback_path = release_policy.generated_manifest_rollback_path
first_preview_wave = release_readiness.first_preview_wave
release_readiness_from_plan = release_readiness.release_readiness_from_plan
readiness_check = release_readiness.readiness_check
release_readiness_impact = release_readiness.release_readiness_impact
readiness_impact_step = release_readiness.readiness_impact_step
release_readiness_impact_summary = release_readiness.release_readiness_impact_summary
release_readiness_next_actions = release_readiness.release_readiness_next_actions
release_production_verification_blockers = (
    release_readiness.release_production_verification_blockers
)
release_production_verification_bypassed = (
    release_readiness.release_production_verification_bypassed
)
release_dispatch_guard_snapshot = release_readiness.release_dispatch_guard_snapshot
release_dispatch_readiness_snapshot = release_readiness.release_dispatch_readiness_snapshot
release_dispatch_readiness_warning_checks = (
    release_readiness.release_dispatch_readiness_warning_checks
)
release_dispatch_context_blockers = release_readiness.release_dispatch_context_blockers
dispatch_context_value = release_readiness.dispatch_context_value
enabled_alert_channels = release_readiness.enabled_alert_channels
release_live_alert_channel_blockers = release_readiness.release_live_alert_channel_blockers
release_live_alert_channels = release_readiness.release_live_alert_channels
release_validated_live_alert_channels = release_readiness.release_validated_live_alert_channels
alert_channel_validation_is_current = release_readiness.alert_channel_validation_is_current
alert_channel_tested_at = release_readiness.alert_channel_tested_at
alert_channel_validation_max_age = release_readiness.alert_channel_validation_max_age
alert_channel_validation_window_label = release_readiness.alert_channel_validation_window_label
TERMINAL_RELEASE_RUN_STATUSES = release_report.TERMINAL_RELEASE_RUN_STATUSES
RELEASE_NOTIFY_COOLDOWN_MINUTES_ENV = release_report.RELEASE_NOTIFY_COOLDOWN_MINUTES_ENV
DEFAULT_RELEASE_NOTIFY_COOLDOWN_MINUTES = release_report.DEFAULT_RELEASE_NOTIFY_COOLDOWN_MINUTES
release_notify_cooldown_blocker = release_report.release_notify_cooldown_blocker
release_notify_cooldown = release_report.release_notify_cooldown
release_run_rollback_policy = release_report.release_run_rollback_policy
first_release_run_step = release_report.first_release_run_step
filter_release_runs = release_report.filter_release_runs
release_run_has_live_side_effects = release_report.release_run_has_live_side_effects
release_run_has_unhealthy_health = release_report.release_run_has_unhealthy_health
release_run_change_freeze_snapshot = release_report.release_run_change_freeze_snapshot
release_run_has_change_freeze_override = release_report.release_run_has_change_freeze_override
release_run_has_active_change_freeze = release_report.release_run_has_active_change_freeze
release_run_has_policy_override = release_report.release_run_has_policy_override
release_policy_override_source_key = release_report.release_policy_override_source_key
release_run_has_policy_override_source = release_report.release_run_has_policy_override_source
release_run_policy_overrides = release_report.release_run_policy_overrides
release_run_summary_from_runs = release_report.release_run_summary_from_runs
release_run_handoff = release_report.release_run_handoff
release_run_report = release_report.release_run_report
release_run_report_markdown = release_report.release_run_report_markdown
release_run_report_audit_summary_lines = release_report.release_run_report_audit_summary_lines
release_run_report_approval_lines = release_report.release_run_report_approval_lines
release_run_report_target_lines = release_report.release_run_report_target_lines
safe_release_report_filename = release_report.safe_release_report_filename
release_run_handoff_verification = release_report.release_run_handoff_verification
release_run_handoff_abort_criteria = release_report.release_run_handoff_abort_criteria
release_run_handoff_change_freeze = release_report.release_run_handoff_change_freeze
release_run_latest_guard = release_report.release_run_latest_guard
release_run_handoff_actions = release_report.release_run_handoff_actions
release_handoff_check = release_report.release_handoff_check
release_run_handoff_headline = release_report.release_run_handoff_headline
release_run_handoff_severity = release_report.release_run_handoff_severity
release_run_is_retryable = release_report.release_run_is_retryable
release_run_last_event = release_report.release_run_last_event
release_attention_reasons = release_report.release_attention_reasons
public_release_audit_event = release_report.public_release_audit_event
release_audit_csv = release_report.release_audit_csv


@router.get(gateway_routes.RELEASE_PLANS_PATH, response_model=ReleasePlanListResponse)
async def list_release_plans(
    limit: int = Query(
        default=gateway_limits.RELEASE_PLAN_DEFAULT_LIMIT,
        ge=1,
        le=gateway_limits.RELEASE_PLAN_MAX_LIMIT,
    ),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ReleasePlanListResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    return ReleasePlanListResponse(plans=db.list_release_plans(workspace_id, limit=limit))


@router.get(gateway_routes.RELEASE_RUNS_PATH, response_model=ReleaseRunListResponse)
async def list_release_runs(
    plan_id: str | None = Query(default=None),
    status: str | None = Query(default=None, max_length=80),
    attention_only: bool = Query(default=False),
    stale_only: bool = Query(default=False),
    active_only: bool = Query(default=False),
    live_only: bool = Query(default=False),
    unhealthy_only: bool = Query(default=False),
    verification_failed_only: bool = Query(default=False),
    verification_pending_timeout_only: bool = Query(default=False),
    policy_override_only: bool = Query(default=False),
    policy_override_source: str | None = Query(default=None, max_length=120),
    active_change_freeze_only: bool = Query(default=False),
    change_freeze_override_only: bool = Query(default=False),
    limit: int = Query(
        default=gateway_limits.RELEASE_RUN_DEFAULT_LIMIT,
        ge=1,
        le=gateway_limits.RELEASE_RUN_MAX_LIMIT,
    ),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ReleaseRunListResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    runs = db.list_release_runs(workspace_id, plan_id=plan_id, limit=limit)
    for run in runs:
        require_plan_application_read_access(db, current, workspace_id, run.get("steps", []))
    runs = filter_release_runs(
        runs,
        status=status,
        attention_only=attention_only,
        stale_only=stale_only,
        active_only=active_only,
        live_only=live_only,
        unhealthy_only=unhealthy_only,
        verification_failed_only=verification_failed_only,
        verification_pending_timeout_only=verification_pending_timeout_only,
        policy_override_only=policy_override_only,
        policy_override_source=policy_override_source,
        active_change_freeze_only=active_change_freeze_only,
        change_freeze_override_only=change_freeze_override_only,
    )
    return ReleaseRunListResponse(runs=runs)


@router.get(gateway_routes.RELEASE_RUN_SUMMARY_PATH, response_model=ReleaseRunSummaryResponse)
async def summarize_release_runs(
    plan_id: str | None = Query(default=None),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ReleaseRunSummaryResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    runs = db.list_release_runs(
        workspace_id,
        plan_id=plan_id,
        limit=gateway_limits.RELEASE_RUN_SUMMARY_LIMIT,
    )
    for run in runs:
        require_plan_application_read_access(db, current, workspace_id, run.get("steps", []))
    return ReleaseRunSummaryResponse(**release_run_summary_from_runs(runs))


@router.get(gateway_routes.RELEASE_AUDIT_PATH, response_model=ReleaseAuditListResponse)
async def list_release_audit(
    plan_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    limit: int = Query(
        default=gateway_limits.RELEASE_AUDIT_DEFAULT_LIMIT,
        ge=1,
        le=gateway_limits.RELEASE_AUDIT_MAX_LIMIT,
    ),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ReleaseAuditListResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    events = release_audit_events_for_current(
        db,
        current,
        workspace_id,
        plan_id=plan_id,
        run_id=run_id,
        event_type=event_type,
        limit=limit,
    )
    return ReleaseAuditListResponse(events=[public_release_audit_event(event) for event in events])


@router.get(gateway_routes.RELEASE_AUDIT_EXPORT_PATH)
async def export_release_audit(
    plan_id: str | None = Query(default=None),
    run_id: str | None = Query(default=None),
    event_type: str | None = Query(default=None),
    limit: int = Query(
        default=gateway_limits.RELEASE_AUDIT_EXPORT_DEFAULT_LIMIT,
        ge=1,
        le=gateway_limits.RELEASE_AUDIT_MAX_LIMIT,
    ),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> Response:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    events = release_audit_events_for_current(
        db,
        current,
        workspace_id,
        plan_id=plan_id,
        run_id=run_id,
        event_type=event_type,
        limit=limit,
    )
    csv_body = release_audit_csv(public_release_audit_event(event) for event in events)
    return Response(
        content=csv_body,
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="release-audit.csv"'},
    )


@router.get(gateway_routes.RELEASE_RUN_PATH, response_model=ReleaseRunResponse)
async def get_release_run(
    run_id: str,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ReleaseRunResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    run = db.get_release_run(workspace_id, run_id)
    if run is None:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=RELEASE_RUN_NOT_FOUND)
    require_plan_application_read_access(db, current, workspace_id, run.get("steps", []))
    return ReleaseRunResponse(run=run)


@router.get(gateway_routes.RELEASE_RUN_HANDOFF_PATH, response_model=ReleaseRunHandoffResponse)
async def get_release_run_handoff(
    run_id: str,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ReleaseRunHandoffResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    run = db.get_release_run(workspace_id, run_id)
    if run is None:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=RELEASE_RUN_NOT_FOUND)
    require_plan_application_read_access(db, current, workspace_id, run.get("steps", []))
    return ReleaseRunHandoffResponse(handoff=release_run_handoff(run))


@router.get(gateway_routes.RELEASE_RUN_REPORT_PATH, response_model=ReleaseRunReportResponse)
async def get_release_run_report(
    run_id: str,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ReleaseRunReportResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    run = db.get_release_run(workspace_id, run_id)
    if run is None:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=RELEASE_RUN_NOT_FOUND)
    require_plan_application_read_access(db, current, workspace_id, run.get("steps", []))
    audit_events = release_audit_events_for_current(
        db,
        current,
        workspace_id,
        plan_id=None,
        run_id=run_id,
        event_type=None,
        limit=50,
    )
    public_events = [public_release_audit_event(event) for event in audit_events]
    return ReleaseRunReportResponse(report=release_run_report(run, public_events))


@router.get(gateway_routes.RELEASE_RUN_REPORT_EXPORT_PATH)
async def export_release_run_report(
    run_id: str,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> Response:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    run = db.get_release_run(workspace_id, run_id)
    if run is None:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=RELEASE_RUN_NOT_FOUND)
    require_plan_application_read_access(db, current, workspace_id, run.get("steps", []))
    audit_events = release_audit_events_for_current(
        db,
        current,
        workspace_id,
        plan_id=None,
        run_id=run_id,
        event_type=None,
        limit=50,
    )
    report = release_run_report(run, [public_release_audit_event(event) for event in audit_events])
    filename = f"release-run-{safe_release_report_filename(run_id)}.md"
    return Response(
        content=str(report.get("markdown") or ""),
        media_type="text/markdown; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{filename}"'},
    )


@router.get(gateway_routes.RELEASE_PLAN_PATH, response_model=ReleasePlanResponse)
async def get_release_plan(
    plan_id: str,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ReleasePlanResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    plan = db.get_release_plan(workspace_id, plan_id)
    if plan is None:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=RELEASE_PLAN_NOT_FOUND)
    require_plan_application_read_access(db, current, workspace_id, plan.get("steps", []))
    return ReleasePlanResponse(plan=plan)


@router.post(gateway_routes.RELEASE_PLAN_PREVIEW_PATH, response_model=ReleasePlanPreviewResponse)
async def preview_release_plan(
    payload: ReleasePlanUpsertRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ReleasePlanPreviewResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    body = persisted_release_plan_for_execution(payload, db, workspace_id)
    require_plan_application_read_access(db, current, workspace_id, body["steps"])
    return ReleasePlanPreviewResponse(preview=build_release_plan_preview(body))


@router.post(gateway_routes.RELEASE_READINESS_PATH, response_model=ReleaseReadinessResponse)
async def check_release_readiness(
    payload: ReleasePlanUpsertRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ReleaseReadinessResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    body = {**payload.model_dump(), "workspace_id": workspace_id}
    require_plan_application_read_access(db, current, workspace_id, body["steps"])
    preview = build_release_plan_preview(body)
    return ReleaseReadinessResponse(
        **release_readiness_from_plan(body, preview, workspace_id=workspace_id, db=db)
    )


@router.post(
    gateway_routes.RELEASE_MANIFEST_RENDER_PATH,
    response_model=ReleaseManifestRenderResponse,
)
async def render_release_manifest(
    payload: ReleaseManifestRenderRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ReleaseManifestRenderResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    body = {**payload.plan.model_dump(), "workspace_id": workspace_id}
    require_plan_application_read_access(db, current, workspace_id, body["steps"])
    steps = [step for step in body.get("steps", []) if isinstance(step, dict)]
    application = release_step_application(db, workspace_id, steps, payload.step_index)
    return ReleaseManifestRenderResponse(
        **render_release_step_manifest(body, payload.step_index, application)
    )


@router.post(
    gateway_routes.RELEASE_MANIFEST_SAFE_PR_PATH,
    response_model=ReleaseManifestSafePrResponse,
)
async def submit_release_manifest_safe_pr(
    payload: ReleaseManifestSafePrRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
) -> ReleaseManifestSafePrResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    body = {**payload.plan.model_dump(), "workspace_id": workspace_id}
    require_plan_application_manage_access(db, current, workspace_id, body["steps"])
    steps = [step for step in body.get("steps", []) if isinstance(step, dict)]
    if payload.step_index < 0 or payload.step_index >= len(steps):
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={
                "message": RELEASE_PLAN_BLOCKED,
                "blockers": ["selected release step is invalid"],
            },
        )
    step = steps[payload.step_index]
    context_blockers = release_dispatch_context_blockers(body, [step], db, workspace_id)
    if context_blockers:
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={"message": RELEASE_PLAN_BLOCKED, "blockers": context_blockers},
        )
    application = release_step_application(db, workspace_id, steps, payload.step_index)
    rendered = render_release_step_manifest(body, payload.step_index, application)
    errors = [
        diagnostic.message
        for diagnostic in rendered["diagnostics"]
        if getattr(diagnostic, "severity", "") == "error"
    ]
    if errors:
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={"message": RELEASE_PLAN_BLOCKED, "blockers": errors},
        )
    files = [file for file in rendered["files"] if file.get("content")]
    if not files:
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={
                "message": RELEASE_PLAN_BLOCKED,
                "blockers": ["generated manifest has no file content to submit"],
            },
        )

    safe_pr = generated_manifest_safe_pr_body(
        body,
        step,
        application,
        rendered,
        payload.title,
        payload.body,
        workspace_id,
    )
    safe_pr_blockers = generated_manifest_safe_pr_blockers(body, step, safe_pr)
    if safe_pr_blockers:
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={"message": RELEASE_PLAN_BLOCKED, "blockers": safe_pr_blockers},
        )
    accepted = await events.accept_body(safe_pr, actor=Actor(current.user_id, tuple(current.roles)))
    return ReleaseManifestSafePrResponse(
        **rendered,
        accepted=True,
        event_id=accepted.event.event_id,
        correlation_id=accepted.event.correlation_id,
        workflow_run_id=safe_pr.workflow_run_id,
        application_id=safe_pr.application_id,
        repo_ref=safe_pr.repo_ref,
        base_branch=safe_pr.base_branch,
        manifest_path=safe_pr.manifest_path,
        commit_sha=safe_pr.commit_sha,
        patch_sha256=safe_pr.patch_sha256,
    )


@router.post(
    gateway_routes.RELEASE_PLAN_DISPATCH_PATH,
    response_model=ReleasePlanDispatchResponse,
)
async def dispatch_release_plan(
    payload: ReleasePlanUpsertRequest,
    wave: int = Query(default=1, ge=1, le=50),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
) -> ReleasePlanDispatchResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    body = {**payload.model_dump(), "workspace_id": workspace_id}
    require_plan_application_manage_access(db, current, workspace_id, body["steps"])
    require_no_active_release_run(db, workspace_id, body)
    preview = build_release_plan_preview(body)
    dispatch_plan = plan_with_safe_pr_evidence(
        body, preview, wave, workspace_id=workspace_id, db=db
    )
    blockers = list(preview.get("blockers", []))
    blockers.extend(release_plan_execution_status_blockers(body, db, workspace_id))
    blockers.extend(
        release_dispatch_context_blockers(
            dispatch_plan,
            steps_for_wave(dispatch_plan, preview, wave),
            db,
            workspace_id,
        )
    )
    blockers.extend(
        release_execution_blockers(dispatch_plan, preview, wave, workspace_id=workspace_id, db=db)
    )
    blockers.extend(release_production_approval_evidence_blockers(dispatch_plan, preview, wave))
    blockers.extend(release_production_change_ticket_blockers(dispatch_plan, preview, wave))
    blockers.extend(release_production_window_blockers(dispatch_plan, preview, wave))
    blockers.extend(release_production_freeze_blockers(dispatch_plan, preview, wave))
    blockers.extend(release_production_runbook_blockers(dispatch_plan, preview, wave))
    blockers.extend(release_production_owner_blockers(dispatch_plan, preview, wave))
    blockers.extend(release_production_verification_blockers(dispatch_plan, preview, wave))
    blockers.extend(release_production_abort_criteria_blockers(dispatch_plan, preview, wave))
    blockers.extend(release_diagnostics_blockers(dispatch_plan))
    blockers.extend(release_rollback_policy_blockers(dispatch_plan))
    blockers.extend(release_live_alert_channel_blockers(dispatch_plan, db, workspace_id))
    if blockers:
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={"message": RELEASE_PLAN_BLOCKED, "blockers": blockers},
        )
    accepted_events, run = await _create_and_dispatch_release_run(
        db,
        workspace_id,
        current,
        events,
        dispatch_plan,
        preview,
        wave,
    )
    return ReleasePlanDispatchResponse(
        accepted=True,
        wave=wave,
        events=accepted_events,
        run=run,
    )


@router.post(gateway_routes.RELEASE_PLAN_START_PATH, response_model=ReleaseRunResponse)
async def start_release_plan(
    payload: ReleasePlanUpsertRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
) -> ReleaseRunResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    body = persisted_release_plan_for_execution(payload, db, workspace_id)
    require_plan_application_manage_access(db, current, workspace_id, body["steps"])
    require_no_active_release_run(db, workspace_id, body)
    preview = build_release_plan_preview(body)
    first_wave = first_preview_wave(preview)
    dispatch_plan = plan_with_safe_pr_evidence(
        body, preview, first_wave, workspace_id=workspace_id, db=db
    )
    blockers = list(preview.get("blockers", []))
    blockers.extend(release_plan_execution_status_blockers(body, db, workspace_id))
    blockers.extend(
        release_dispatch_context_blockers(
            dispatch_plan,
            steps_for_wave(dispatch_plan, preview, first_wave),
            db,
            workspace_id,
        )
    )
    blockers.extend(
        release_execution_blockers(
            dispatch_plan, preview, first_wave, workspace_id=workspace_id, db=db
        )
    )
    blockers.extend(
        release_production_approval_evidence_blockers(dispatch_plan, preview, first_wave)
    )
    blockers.extend(release_production_change_ticket_blockers(dispatch_plan, preview, first_wave))
    blockers.extend(release_production_window_blockers(dispatch_plan, preview, first_wave))
    blockers.extend(release_production_freeze_blockers(dispatch_plan, preview, first_wave))
    blockers.extend(release_production_runbook_blockers(dispatch_plan, preview, first_wave))
    blockers.extend(release_production_owner_blockers(dispatch_plan, preview, first_wave))
    blockers.extend(release_production_verification_blockers(dispatch_plan, preview, first_wave))
    blockers.extend(release_production_abort_criteria_blockers(dispatch_plan, preview, first_wave))
    blockers.extend(release_diagnostics_blockers(dispatch_plan))
    blockers.extend(release_rollback_policy_blockers(dispatch_plan))
    blockers.extend(release_live_alert_channel_blockers(dispatch_plan, db, workspace_id))
    if blockers:
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={"message": RELEASE_PLAN_BLOCKED, "blockers": blockers},
        )
    _, run = await _create_and_dispatch_release_run(
        db,
        workspace_id,
        current,
        events,
        dispatch_plan,
        preview,
        first_wave,
    )
    return ReleaseRunResponse(run=run)


async def _create_and_dispatch_release_run(
    db: Any,
    workspace_id: str,
    current: Any,
    events: Any,
    body: dict[str, Any],
    preview: dict[str, Any],
    wave: int,
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    run = db.create_release_run(
        {
            "workspace_id": workspace_id,
            "plan": body,
            "preview": preview,
            "started_by": current.user_id,
        }
    )
    if run is None:
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={"message": "release run could not be created"},
        )
    run_id = str(run["run_id"])
    accepted_events: list[dict[str, Any]] = []
    try:
        accepted_events = await dispatch_wave_steps(
            body,
            preview,
            wave,
            workspace_id,
            current,
            db,
            events,
            run_id=run_id,
        )
    except HTTPException:
        if not accepted_events:
            db.delete_release_run(workspace_id, run_id)
        else:
            db.update_release_run_status(
                workspace_id,
                run_id,
                "failed",
                actor=current.user_id,
                message="Release run failed while dispatching",
            )
        raise
    except Exception as exc:
        if not accepted_events:
            db.delete_release_run(workspace_id, run_id)
        else:
            db.update_release_run_status(
                workspace_id,
                run_id,
                "failed",
                actor=current.user_id,
                message="Release run failed while dispatching",
            )
        raise HTTPException(
            status_code=500,
            detail={"message": f"release run dispatch failed: {str(exc)}"},
        ) from exc
    return accepted_events, db.get_release_run(workspace_id, run_id) or run


@router.post(gateway_routes.RELEASE_PLAN_ARCHIVE_PATH, response_model=ReleasePlanResponse)
async def archive_release_plan(
    plan_id: str,
    payload: ReleasePlanArchiveRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ReleasePlanResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    plan = db.get_release_plan(workspace_id, plan_id)
    if plan is None:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=RELEASE_PLAN_NOT_FOUND)
    require_plan_application_plan_manage_access(db, current, workspace_id, plan.get("steps", []))
    require_release_plan_not_active(db, workspace_id, plan_id, action="archived")
    if str(plan.get("status") or "").lower() == "archived":
        raise HTTPException(status_code=HTTP_CONFLICT, detail="release plan is already archived")
    reason = require_release_plan_lifecycle_reason(payload.reason, action="archive")
    archived = db.archive_release_plan(workspace_id, plan_id, reason=reason, actor=current.user_id)
    if archived is None:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=RELEASE_PLAN_NOT_FOUND)
    return ReleasePlanResponse(plan=archived)


@router.post(gateway_routes.RELEASE_PLAN_RESTORE_PATH, response_model=ReleasePlanResponse)
async def restore_release_plan(
    plan_id: str,
    payload: ReleasePlanRestoreRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ReleasePlanResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    plan = db.get_release_plan(workspace_id, plan_id)
    if plan is None:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=RELEASE_PLAN_NOT_FOUND)
    require_plan_application_plan_manage_access(db, current, workspace_id, plan.get("steps", []))
    require_release_plan_not_active(db, workspace_id, plan_id, action="restored")
    if str(plan.get("status") or "").lower() != "archived":
        raise HTTPException(status_code=HTTP_CONFLICT, detail="release plan is not archived")
    reason = require_release_plan_lifecycle_reason(payload.reason, action="restore")
    restored = db.restore_release_plan(workspace_id, plan_id, reason=reason, actor=current.user_id)
    if restored is None:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=RELEASE_PLAN_NOT_FOUND)
    return ReleasePlanResponse(plan=restored)


@router.delete(gateway_routes.RELEASE_PLAN_PATH)
async def delete_release_plan(
    plan_id: str,
    force: bool = Query(default=False),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> Response:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    plan = db.get_release_plan(workspace_id, plan_id)
    if plan is None:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=RELEASE_PLAN_NOT_FOUND)
    require_plan_application_plan_manage_access(db, current, workspace_id, plan.get("steps", []))
    if not force and db.has_active_release_runs(workspace_id, plan_id):
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={
                "message": "release plan has active runs",
                "can_force_delete": True,
            },
        )
    if not db.delete_release_plan(workspace_id, plan_id):
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=RELEASE_PLAN_NOT_FOUND)
    return Response(status_code=204)


@router.delete(gateway_routes.RELEASE_RUN_PATH)
async def delete_release_run(
    run_id: str,
    force: bool = Query(default=False),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> Response:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    run = db.get_release_run(workspace_id, run_id)
    if run is None:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=RELEASE_RUN_NOT_FOUND)
    require_plan_application_cancel_access(db, current, workspace_id, run.get("steps", []))
    if str(run.get("status") or "") in BLOCKING_RUN_STATUSES and not force:
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={
                "message": "release run is still active",
                "can_force_delete": True,
            },
        )
    if not db.delete_release_run(workspace_id, run_id):
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=RELEASE_RUN_NOT_FOUND)
    return Response(status_code=204)


@router.post(gateway_routes.RELEASE_RUN_ADVANCE_PATH, response_model=ReleaseRunResponse)
async def advance_release_run(
    run_id: str,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
) -> ReleaseRunResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    run = db.get_release_run(workspace_id, run_id)
    if run is None:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=RELEASE_RUN_NOT_FOUND)
    require_plan_application_manage_access(db, current, workspace_id, run.get("steps", []))
    if str(run.get("status")) == "paused":
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={"message": RELEASE_RUN_BLOCKED, "blockers": ["release run is paused"]},
        )
    current_wave = int_field(run, "current_wave", 1)
    current_steps = [step for step in run.get("steps", []) if step.get("wave") == current_wave]
    current_wave_blockers = release_current_wave_health_blockers(current_steps, current_wave)
    if current_wave_blockers:
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={
                "message": RELEASE_RUN_BLOCKED,
                "blockers": current_wave_blockers,
            },
        )
    next_wave = current_wave + 1
    pending_steps = [step for step in run.get("steps", []) if step.get("wave") == next_wave]
    if not pending_steps:
        completed = db.update_release_run_status(
            workspace_id,
            run_id,
            "succeeded",
            actor=current.user_id,
            message="All release waves completed.",
        )
        return ReleaseRunResponse(run=completed or run)
    plan = release_plan_from_run(run, pending_steps)
    preview = {
        "steps": [
            {"application_id": step["application_id"], "wave": next_wave} for step in pending_steps
        ]
    }
    blockers = release_execution_blockers(
        plan, preview, next_wave, workspace_id=workspace_id, db=db
    )
    blockers.extend(release_production_approval_evidence_blockers(plan, preview, next_wave))
    blockers.extend(release_production_change_ticket_blockers(plan, preview, next_wave))
    blockers.extend(release_production_window_blockers(plan, preview, next_wave))
    blockers.extend(release_production_freeze_blockers(plan, preview, next_wave))
    blockers.extend(release_production_runbook_blockers(plan, preview, next_wave))
    blockers.extend(release_production_owner_blockers(plan, preview, next_wave))
    blockers.extend(release_production_verification_blockers(plan, preview, next_wave))
    blockers.extend(release_production_abort_criteria_blockers(plan, preview, next_wave))
    blockers.extend(release_diagnostics_blockers(plan))
    blockers.extend(release_rollback_policy_blockers(plan))
    blockers.extend(release_live_alert_channel_blockers(plan, db, workspace_id))
    if blockers:
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={"message": RELEASE_RUN_BLOCKED, "blockers": blockers},
        )
    await dispatch_wave_steps(
        plan,
        preview,
        next_wave,
        workspace_id,
        current,
        db,
        events,
        run_id=run_id,
    )
    advanced = db.update_release_run_status(
        workspace_id,
        run_id,
        "running",
        current_wave=next_wave,
        actor=current.user_id,
        message=f"Release run advanced to wave {next_wave}.",
    )
    return ReleaseRunResponse(run=advanced or db.get_release_run(workspace_id, run_id) or run)


@router.post(gateway_routes.RELEASE_RUN_PAUSE_PATH, response_model=ReleaseRunResponse)
async def pause_release_run(
    run_id: str,
    payload: ReleaseRunActionRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ReleaseRunResponse:
    return release_run_status_action(
        run_id,
        payload,
        current,
        db,
        "paused",
        "Release run paused.",
        "release run is already terminal",
    )


@router.post(gateway_routes.RELEASE_RUN_RESUME_PATH, response_model=ReleaseRunResponse)
async def resume_release_run(
    run_id: str,
    payload: ReleaseRunActionRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ReleaseRunResponse:
    return release_run_status_action(
        run_id,
        payload,
        current,
        db,
        "running",
        "Release run resumed.",
        "release run is already terminal",
    )


@router.post(gateway_routes.RELEASE_RUN_RETRY_PATH, response_model=ReleaseRunResponse)
async def retry_release_run(
    run_id: str,
    payload: ReleaseRunActionRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
) -> ReleaseRunResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    existing = db.get_release_run(workspace_id, run_id)
    if existing is None:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=RELEASE_RUN_NOT_FOUND)
    require_plan_application_manage_access(db, current, workspace_id, existing.get("steps", []))
    run_status = str(existing.get("derived_status") or existing.get("status") or "")
    if run_status in {"succeeded", "cancelled", "rollback_requested"}:
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={"message": RELEASE_RUN_BLOCKED, "blockers": ["release run cannot be retried"]},
        )
    retry_wave = int_field(existing, "current_wave", 1)
    retry_steps = retryable_steps_for_wave(existing, retry_wave)
    if not retry_steps:
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={
                "message": RELEASE_RUN_BLOCKED,
                "blockers": [f"wave {retry_wave} has no failed or unhealthy steps to retry"],
            },
        )
    retry_limit = retry_limit_for_steps(existing, retry_steps)
    previous_attempts = retry_attempts_for_wave(existing, retry_wave)
    if previous_attempts >= retry_limit:
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={
                "message": RELEASE_RUN_BLOCKED,
                "blockers": [f"wave {retry_wave} retry budget exhausted ({retry_limit})"],
            },
        )
    attempt = previous_attempts + 1
    plan = release_plan_from_run(existing, retry_steps)
    preview = {
        "steps": [
            {"application_id": step["application_id"], "wave": retry_wave} for step in retry_steps
        ]
    }
    blockers = release_execution_blockers(
        plan, preview, retry_wave, workspace_id=workspace_id, db=db
    )
    blockers.extend(release_production_approval_evidence_blockers(plan, preview, retry_wave))
    blockers.extend(release_production_change_ticket_blockers(plan, preview, retry_wave))
    blockers.extend(release_production_window_blockers(plan, preview, retry_wave))
    blockers.extend(release_production_freeze_blockers(plan, preview, retry_wave))
    blockers.extend(release_production_runbook_blockers(plan, preview, retry_wave))
    blockers.extend(release_production_owner_blockers(plan, preview, retry_wave))
    blockers.extend(release_production_verification_blockers(plan, preview, retry_wave))
    blockers.extend(release_production_abort_criteria_blockers(plan, preview, retry_wave))
    blockers.extend(release_diagnostics_blockers(plan))
    blockers.extend(release_rollback_policy_blockers(plan))
    blockers.extend(release_live_alert_channel_blockers(plan, db, workspace_id))
    if blockers:
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={"message": RELEASE_RUN_BLOCKED, "blockers": blockers},
        )
    db.mark_release_run_retry(
        workspace_id,
        run_id,
        retry_wave,
        attempt,
        "running",
        actor=current.user_id,
        reason=payload.reason or "operator requested retry",
    )
    try:
        await dispatch_wave_steps(
            plan,
            preview,
            retry_wave,
            workspace_id,
            current,
            db,
            events,
            run_id=run_id,
        )
    except Exception:
        db.mark_release_run_retry(
            workspace_id,
            run_id,
            retry_wave,
            attempt,
            "failed",
            actor=current.user_id,
            reason="retry dispatch failed",
        )
        raise
    run = db.get_release_run(workspace_id, run_id) or existing
    return ReleaseRunResponse(run=run)


@router.post(gateway_routes.RELEASE_RUN_ROLLBACK_PATH, response_model=ReleaseRunResponse)
async def rollback_release_run(
    run_id: str,
    payload: ReleaseRunActionRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ReleaseRunResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    existing = db.get_release_run(workspace_id, run_id)
    if existing is None:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=RELEASE_RUN_NOT_FOUND)
    run_status = str(existing.get("status") or "")
    if run_status in TERMINAL_RELEASE_RUN_STATUSES:
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={
                "message": RELEASE_RUN_BLOCKED,
                "blockers": ["release run is already terminal"],
            },
        )
    require_plan_application_rollback_access(db, current, workspace_id, existing.get("steps", []))
    rollback_policy = release_run_rollback_policy(existing)
    if rollback_policy == "disabled":
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={
                "message": RELEASE_RUN_BLOCKED,
                "blockers": ["rollback is disabled for this release run"],
            },
        )
    run = db.request_release_run_rollback(
        workspace_id,
        run_id,
        actor=current.user_id,
        reason=operator_action_reason(payload, "operator requested rollback"),
    )
    if run is None:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=RELEASE_RUN_NOT_FOUND)
    return ReleaseRunResponse(run=run)


@router.post(gateway_routes.RELEASE_RUN_CANCEL_PATH, response_model=ReleaseRunResponse)
async def cancel_release_run(
    run_id: str,
    payload: ReleaseRunActionRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ReleaseRunResponse:
    return release_run_status_action(
        run_id,
        payload,
        current,
        db,
        "cancelled",
        "Release run cancelled.",
        "release run is already terminal",
        permission=Permission.RUNNER_JOB_CANCEL.value,
    )


@router.post(gateway_routes.RELEASE_RUN_NOTIFY_PATH, response_model=ReleaseRunAlertResponse)
async def notify_release_run_attention(
    run_id: str,
    payload: ReleaseRunActionRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
) -> ReleaseRunAlertResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    run = db.get_release_run(workspace_id, run_id)
    if run is None:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=RELEASE_RUN_NOT_FOUND)
    require_plan_application_manage_access(db, current, workspace_id, run.get("steps", []))
    blocker = release_notify_cooldown_blocker(run)
    if blocker:
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={"message": RELEASE_RUN_BLOCKED, "blockers": [blocker]},
        )
    reason = operator_action_reason(payload, "operator requested release run notification")
    alert = release_run_attention_alert_body(
        run,
        workspace_id,
        reason=reason,
    )
    accepted = await events.accept_body(
        alert,
        actor=Actor(current.user_id, tuple(current.roles)),
    )
    event_type = f"release.notify.{release_window_bound_label(datetime.now(UTC))}"
    recorded = record_release_notify_event(
        db,
        workspace_id,
        run_id,
        event_type,
        alert,
        actor=current.user_id,
        reason=reason,
        accepted_event=accepted.event.to_dict(),
    )
    return ReleaseRunAlertResponse(
        accepted=True,
        event=accepted.event.to_dict(),
        run=recorded or run,
    )


@router.post(gateway_routes.RELEASE_PLANS_PATH, response_model=ReleasePlanResponse)
async def create_release_plan(
    payload: ReleasePlanUpsertRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ReleasePlanResponse:
    if payload.plan_id is not None:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE_ENTITY,
            detail=EXPLICIT_RELEASE_PLAN_ID_NOT_ALLOWED,
        )
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    if payload.status == "archived":
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail="create the release plan first, then archive it with an archive reason",
        )
    body = {**payload.model_dump(), "workspace_id": workspace_id, "user_id": current.user_id}
    with unit_of_work_or_null(db):
        lock_identity = getattr(db, "lock_release_plan_identity", None)
        if not callable(lock_identity):
            raise HTTPException(status_code=503, detail="release plan storage unavailable")
        lock_identity(workspace_id, RELEASE_PLAN_WORKSPACE_MUTATION_LOCK)
        lock_identity(workspace_id, str(body["name"]))
        get_by_name = getattr(db, "get_release_plan_by_name", None)
        if callable(get_by_name):
            existing = get_by_name(
                workspace_id,
                str(body["name"]),
                for_update=True,
            )
        else:
            existing_plan_id = derive_release_plan_id(body)
            existing = db.get_release_plan(workspace_id, existing_plan_id, for_update=True)
        if existing is not None:
            require_plan_application_plan_manage_access(
                db,
                current,
                workspace_id,
                existing.get("steps", []),
            )
            body["plan_id"] = str(existing["plan_id"])
        elif not body["steps"]:
            raise HTTPException(
                status_code=HTTP_UNPROCESSABLE_ENTITY,
                detail=EMPTY_RELEASE_PLAN_NOT_ALLOWED,
            )
        if body["steps"]:
            require_plan_application_plan_manage_access(db, current, workspace_id, body["steps"])
        plan = upsert_release_plan_or_404(db, body)
    return ReleasePlanResponse(plan=plan)


@router.put(gateway_routes.RELEASE_PLAN_PATH, response_model=ReleasePlanResponse)
async def update_release_plan(
    plan_id: str,
    payload: ReleasePlanUpsertRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ReleasePlanResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    with unit_of_work_or_null(db):
        lock_identity = getattr(db, "lock_release_plan_identity", None)
        if not callable(lock_identity):
            raise HTTPException(status_code=503, detail="release plan storage unavailable")
        lock_identity(workspace_id, RELEASE_PLAN_WORKSPACE_MUTATION_LOCK)
        existing = db.get_release_plan(workspace_id, plan_id, for_update=True)
        if existing is None:
            raise HTTPException(status_code=HTTP_NOT_FOUND, detail=RELEASE_PLAN_NOT_FOUND)
        require_plan_application_plan_manage_access(
            db,
            current,
            workspace_id,
            existing.get("steps", []),
        )
        body = {**payload.model_dump(), "plan_id": plan_id, "workspace_id": workspace_id}
        require_release_plan_status_transition(existing, str(body.get("status") or ""))
        if str(body["name"]) != str(existing.get("name") or ""):
            get_by_name = getattr(db, "get_release_plan_by_name", None)
            if not callable(get_by_name):
                raise HTTPException(status_code=503, detail="release plan storage unavailable")
            lock_identity(workspace_id, str(body["name"]))
            name_owner = get_by_name(
                workspace_id,
                str(body["name"]),
                for_update=True,
            )
            if name_owner is not None and str(name_owner.get("plan_id")) != plan_id:
                raise HTTPException(status_code=HTTP_CONFLICT, detail="release plan name conflict")
        if body["steps"]:
            require_plan_application_plan_manage_access(db, current, workspace_id, body["steps"])
        plan = upsert_release_plan_or_404(db, body)
    return ReleasePlanResponse(plan=plan)


def require_release_plan_not_active(
    db: Any, workspace_id: str, plan_id: str, *, action: str
) -> None:
    has_active_runs = getattr(db, "has_active_release_runs", None)
    if callable(has_active_runs) and has_active_runs(workspace_id, plan_id):
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail=f"release plan has active runs and cannot be {action}",
        )


def require_release_plan_lifecycle_reason(reason: str | None, *, action: str) -> str:
    normalized = str(reason or "").strip()
    if normalized:
        return normalized
    raise HTTPException(
        status_code=HTTP_UNPROCESSABLE_ENTITY, detail=f"release plan {action} reason is required"
    )


def require_release_plan_status_transition(existing: dict[str, Any], requested_status: str) -> None:
    current_status = str(existing.get("status") or "").lower()
    requested = requested_status.lower()
    if current_status == "archived" and requested != "archived":
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail="restore an archived release plan through the restore action",
        )
    if current_status != "archived" and requested == "archived":
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail="archive a release plan through the archive action with a reason",
        )


def release_plan_execution_status_blockers(
    plan: dict[str, Any],
    db: Any,
    workspace_id: str,
) -> list[str]:
    plan_id = str(plan.get("plan_id") or "").strip()
    get_plan = getattr(db, "get_release_plan", None)
    if not plan_id or not callable(get_plan):
        return []
    persisted_plan = get_plan(workspace_id, plan_id)
    if not isinstance(persisted_plan, dict):
        return []
    status = str(persisted_plan.get("status") or "").lower()
    if status in {"", "active"}:
        return []
    if status == "draft":
        return [
            f"Release plan {plan_id} is draft and cannot be dispatched. Activate the plan after review."
        ]
    if status == "paused":
        return [
            f"Release plan {plan_id} is paused and cannot be dispatched. Resume the plan when it is safe."
        ]
    if status == "archived":
        return [f"Release plan {plan_id} is archived and cannot be dispatched."]
    return [f"Release plan {plan_id} is not active and cannot be dispatched."]


def persisted_release_plan_for_execution(
    payload: ReleasePlanUpsertRequest,
    db: Any,
    workspace_id: str,
) -> dict[str, Any]:
    plan_id = str(payload.plan_id or "").strip()
    if not plan_id:
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={
                "message": RELEASE_PLAN_BLOCKED,
                "blockers": ["Save the release plan before starting or dispatching it."],
            },
        )
    get_plan = getattr(db, "get_release_plan", None)
    if not callable(get_plan):
        # Lightweight test adapters do not persist plans; production storage always does.
        return {**payload.model_dump(), "workspace_id": workspace_id}
    persisted_plan = get_plan(workspace_id, plan_id)
    if not isinstance(persisted_plan, dict):
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=RELEASE_PLAN_NOT_FOUND)
    return {**persisted_plan, "workspace_id": workspace_id}


async def dispatch_wave_steps(
    plan: dict[str, Any],
    preview: dict[str, Any],
    wave: int,
    workspace_id: str,
    current: Any,
    db: Any,
    events: Any,
    *,
    run_id: str | None = None,
) -> list[dict[str, Any]]:
    dispatch_plan = plan_with_safe_pr_evidence(
        plan, preview, wave, workspace_id=workspace_id, db=db
    )
    selected_steps = steps_for_wave(dispatch_plan, preview, wave)
    if not selected_steps:
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={"message": RELEASE_PLAN_BLOCKED, "blockers": [f"wave {wave} has no steps"]},
        )

    blockers = release_plan_execution_status_blockers(dispatch_plan, db, workspace_id)
    blockers.extend(
        release_dispatch_context_blockers(dispatch_plan, selected_steps, db, workspace_id)
    )
    blockers.extend(
        release_execution_blockers(dispatch_plan, preview, wave, workspace_id=workspace_id, db=db)
    )
    blockers.extend(release_production_approval_evidence_blockers(dispatch_plan, preview, wave))
    blockers.extend(release_production_change_ticket_blockers(dispatch_plan, preview, wave))
    blockers.extend(release_production_window_blockers(dispatch_plan, preview, wave))
    blockers.extend(release_production_freeze_blockers(dispatch_plan, preview, wave))
    blockers.extend(release_production_runbook_blockers(dispatch_plan, preview, wave))
    blockers.extend(release_production_owner_blockers(dispatch_plan, preview, wave))
    blockers.extend(release_production_verification_blockers(dispatch_plan, preview, wave))
    blockers.extend(release_production_abort_criteria_blockers(dispatch_plan, preview, wave))
    blockers.extend(release_diagnostics_blockers(dispatch_plan))
    blockers.extend(release_rollback_policy_blockers(dispatch_plan))
    blockers.extend(release_live_alert_channel_blockers(dispatch_plan, db, workspace_id))
    if blockers:
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={"message": RELEASE_PLAN_BLOCKED, "blockers": blockers},
        )

    accepted_events: list[dict[str, Any]] = []
    profile = execution_profile(dispatch_plan)
    release_guard = release_dispatch_guard_snapshot(dispatch_plan, db, workspace_id, preview, wave)
    for step in selected_steps:
        application_id = str(step["application_id"])
        application = db.get_application(workspace_id, application_id) or {}
        request = dispatch_request_for_step(dispatch_plan, step, application, workspace_id)
        require_cluster_access(
            db,
            current,
            workspace_id,
            request.cluster_id,
            Permission.DEPLOY_RUN.value,
        )
        if profile.side_effects:
            accepted = await events.accept_body(
                request,
                actor=Actor(current.user_id, tuple(current.roles)),
            )
            event_id = accepted.event.event_id
            correlation_id = accepted.event.correlation_id
            event = accepted.event.to_dict()
        else:
            event_id = dry_run_event_id(run_id, application_id, wave)
            correlation_id = dry_run_correlation_id(run_id, application_id, wave)
            event = dry_run_event_body(request, event_id, correlation_id, profile.to_body())
        accepted_events.append(
            {
                "event_id": event_id,
                "correlation_id": correlation_id,
                "event": event,
            }
        )
        if run_id:
            db.mark_release_run_step_dispatched(
                workspace_id,
                run_id,
                application_id,
                workflow_run_id=request.workflow_run_id,
                event_id=event_id,
                correlation_id=correlation_id,
                actor=current.user_id,
                details={
                    "runtime_mode": profile.runtime_mode,
                    "provider_mode": profile.provider_mode,
                    "side_effects": profile.side_effects,
                    "wave": wave,
                    "cluster_id": request.cluster_id,
                    "environment": request.environment,
                    "manifest_path": request.manifest_path,
                    "repo_ref": request.repo_ref,
                    "branch": request.branch,
                    "commit_sha": request.commit_sha,
                    "release_guard": release_guard,
                },
            )
    return accepted_events


def dry_run_event_body(
    request: GitWebhookReceivedBody,
    event_id: str,
    correlation_id: str,
    profile: dict[str, Any],
) -> dict[str, Any]:
    return {
        "event_id": event_id,
        "correlation_id": correlation_id,
        "subject": "git.webhook.received",
        "dry_run": True,
        "execution_profile": profile,
        "body": request.to_body(),
    }


def release_run_status_action(
    run_id: str,
    payload: ReleaseRunActionRequest,
    current: Any,
    db: Any,
    status: str,
    default_message: str,
    terminal_block_message: str,
    *,
    permission: str = Permission.DEPLOY_RUN.value,
) -> ReleaseRunResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    existing = db.get_release_run(workspace_id, run_id)
    if existing is None:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=RELEASE_RUN_NOT_FOUND)
    run_status = str(existing.get("status") or "")
    if run_status in TERMINAL_RELEASE_RUN_STATUSES:
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={"message": RELEASE_RUN_BLOCKED, "blockers": [terminal_block_message]},
        )
    require_plan_application_permission_access(
        db,
        current,
        workspace_id,
        existing.get("steps", []),
        permission,
    )
    reason = operator_action_reason(payload, default_message)
    run = db.update_release_run_status(
        workspace_id,
        run_id,
        status,
        actor=current.user_id,
        message=default_message,
        details={"reason": reason, "operator_action": status},
    )
    return ReleaseRunResponse(run=run or existing)


def operator_action_reason(payload: ReleaseRunActionRequest, fallback: str) -> str:
    reason = str(payload.reason or "").strip()
    return reason or fallback


def record_release_notify_event(
    db: Any,
    workspace_id: str,
    run_id: str,
    event_type: str,
    alert: AlertRequestedBody,
    *,
    actor: str,
    reason: str,
    accepted_event: dict[str, Any],
) -> dict[str, Any] | None:
    if not hasattr(db, "record_release_run_event"):
        return None
    return db.record_release_run_event(
        workspace_id,
        run_id,
        event_type,
        f"Release notification requested ({alert.severity}).",
        actor=actor,
        details={
            "reason": reason,
            "operator_action": "notify",
            "alert": {
                "severity": alert.severity,
                "cluster_id": alert.cluster_id,
                "namespace": alert.namespace,
                "application_id": alert.application_id,
                "workflow_run_id": alert.workflow_run_id,
                "message": alert.message,
                "reason": alert.reason,
            },
            "accepted_event": accepted_event,
        },
    )


def release_run_attention_alert_body(
    run: dict[str, Any],
    workspace_id: str,
    *,
    reason: str,
) -> AlertRequestedBody:
    status = str(run.get("derived_status") or run.get("status") or "unknown")
    attention = run.get("attention") if isinstance(run.get("attention"), dict) else {}
    reasons = (
        [str(item) for item in attention.get("reasons", []) if str(item).strip()]
        if isinstance(attention.get("reasons"), list)
        else []
    )
    first_step = first_release_run_step(run)
    step_details = first_step.get("details") if isinstance(first_step.get("details"), dict) else {}
    step_config = step_details.get("config") if isinstance(step_details.get("config"), dict) else {}
    timed_out_jobs = release_verification_job_pending_timeouts(run)
    failed_verification = release_run_has_failed_verification(run)
    severity = (
        "critical"
        if status in {"failed", "rollback_requested"}
        or attention.get("stale") is True
        or failed_verification
        or bool(timed_out_jobs)
        else "warning"
    )
    application_id = str(first_step.get("application_id") or "release")
    message_parts = [f"{str(run.get('plan_name') or 'Release run')} is {status}"]
    if reasons:
        message_parts.append("; ".join(reasons[:3]))
    if failed_verification:
        message_parts.append("post-deploy verification failed")
    if timed_out_jobs:
        message_parts.append(release_verification_timeout_alert_summary(timed_out_jobs))
    message_parts.append(reason)
    return AlertRequestedBody(
        workspace_id=workspace_id,
        cluster_id=str(
            step_details.get("cluster_id")
            or step_config.get("cluster_id")
            or Target.DEFAULT_CLUSTER_ID
        ),
        namespace=str(
            step_details.get("namespace") or step_config.get("namespace") or Sandbox.NAMESPACE
        ),
        severity=severity,
        application_id=application_id,
        workflow_run_id=str(first_step.get("workflow_run_id") or run.get("run_id") or ""),
        environment=str(
            step_details.get("environment") or step_config.get("environment") or "production"
        ),
        message=f"{application_id}: {' | '.join(message_parts)}",
        reason="release run needs attention",
    )


def require_plan_application_manage_access(
    db: Any,
    current: Any,
    workspace_id: str,
    steps: list[dict[str, Any]],
) -> None:
    require_plan_application_permission_access(
        db,
        current,
        workspace_id,
        steps,
        Permission.DEPLOY_RUN.value,
    )


def require_plan_application_plan_manage_access(
    db: Any,
    current: Any,
    workspace_id: str,
    steps: list[dict[str, Any]],
) -> None:
    has_application_scope = any(str(step.get("application_id") or "").strip() for step in steps)
    if not has_application_scope:
        roles = tuple(getattr(current, "roles", ()) or ())
        if ServiceRole.SERVICE_ADMIN.value in roles:
            return
        raise HTTPException(status_code=403, detail="resource access denied")
    require_plan_application_permission_access(
        db,
        current,
        workspace_id,
        steps,
        Permission.APPLICATION_MANAGE.value,
    )


def upsert_release_plan_or_404(db: Any, body: dict[str, Any]) -> dict[str, Any]:
    try:
        return db.upsert_release_plan(body)
    except ReleasePlanWorkspaceMismatchError as exc:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=RELEASE_PLAN_NOT_FOUND) from exc
    except IntegrityError as exc:
        raise HTTPException(status_code=HTTP_CONFLICT, detail="release plan conflict") from exc


def require_plan_application_rollback_access(
    db: Any,
    current: Any,
    workspace_id: str,
    steps: list[dict[str, Any]],
) -> None:
    require_plan_application_permission_access(
        db,
        current,
        workspace_id,
        steps,
        Permission.ROLLBACK_RUN.value,
    )


def require_plan_application_cancel_access(
    db: Any,
    current: Any,
    workspace_id: str,
    steps: list[dict[str, Any]],
) -> None:
    require_plan_application_permission_access(
        db,
        current,
        workspace_id,
        steps,
        Permission.RUNNER_JOB_CANCEL.value,
    )


def require_plan_application_audit_access(
    db: Any,
    current: Any,
    workspace_id: str,
    steps: list[dict[str, Any]],
) -> None:
    require_plan_application_permission_access(
        db,
        current,
        workspace_id,
        steps,
        Permission.EVIDENCE_READ.value,
    )


def require_plan_application_permission_access(
    db: Any,
    current: Any,
    workspace_id: str,
    steps: list[dict[str, Any]],
    permission: str,
) -> None:
    for step in steps:
        application_id = str(step.get("application_id") or "")
        if not application_id:
            continue
        require_resource_access(
            db,
            current,
            workspace_id,
            AccessResourceType.APPLICATION.value,
            application_id,
            permission,
        )


def require_no_active_release_run(db: Any, workspace_id: str, plan: dict[str, Any]) -> None:
    blockers = active_release_run_blockers(db, workspace_id, plan)
    if blockers:
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={
                "message": RELEASE_PLAN_BLOCKED,
                "blockers": blockers,
            },
        )


def release_plan_from_run(run: dict[str, Any], steps: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "plan_id": run.get("plan_id"),
        "name": run.get("plan_name") or "Release run",
        "settings": dict(run.get("settings") or {}),
        "steps": [
            {
                "application_id": str(step["application_id"]),
                "name": str(step.get("name") or step["application_id"]),
                "position": index,
                "depends_on": [],
                "config": dict(dict(step.get("details") or {}).get("config") or {}),
            }
            for index, step in enumerate(steps)
        ],
    }


def retryable_steps_for_wave(run: dict[str, Any], wave: int) -> list[dict[str, Any]]:
    retryable_statuses = {"failed", "cancelled", "timeout"}
    steps = run.get("steps") if isinstance(run.get("steps"), list) else []
    return [
        step
        for step in steps
        if isinstance(step, dict)
        and int_field(step, "wave", 0) == wave
        and (
            str(step.get("status") or "") in retryable_statuses
            or (
                isinstance(step.get("health"), dict) and step["health"].get("status") == "unhealthy"
            )
        )
    ]


def retry_limit_for_steps(run: dict[str, Any], steps: list[dict[str, Any]]) -> int:
    settings = dict(run.get("settings") or {})
    default_limit = max(0, int_field(settings, "retry_attempts", 1))
    limits: list[int] = []
    for step in steps:
        details = step.get("details") if isinstance(step.get("details"), dict) else {}
        config = details.get("config") if isinstance(details.get("config"), dict) else {}
        limits.append(max(0, int_field(config, "retry_attempts", default_limit)))
    return min(limits) if limits else default_limit


def retry_attempts_for_wave(run: dict[str, Any], wave: int) -> int:
    events = run.get("events") if isinstance(run.get("events"), list) else []
    prefix = f"release.retry.wave.{wave}.attempt."
    attempts: set[int] = set()
    for event in events:
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "")
        if not event_type.startswith(prefix):
            continue
        suffix = event_type.removeprefix(prefix).split(".", 1)[0]
        if suffix.isdigit():
            attempts.add(int(suffix))
    return len(attempts)


def require_plan_application_read_access(
    db: Any,
    current: Any,
    workspace_id: str,
    steps: list[dict[str, Any]],
) -> None:
    require_plan_application_permission_access(
        db,
        current,
        workspace_id,
        steps,
        Permission.APPLICATION_READ.value,
    )


def release_audit_events_for_current(
    db: Any,
    current: Any,
    workspace_id: str,
    *,
    plan_id: str | None,
    run_id: str | None,
    event_type: str | None,
    limit: int,
) -> list[dict[str, Any]]:
    events = db.list_release_audit_events(
        workspace_id,
        plan_id=plan_id,
        run_id=run_id,
        event_type=event_type,
        limit=limit,
    )
    for event in events:
        require_plan_application_audit_access(db, current, workspace_id, event.get("_steps", []))
    return events


def dispatch_request_for_step(
    plan: dict[str, Any],
    step: dict[str, Any],
    application: dict[str, Any],
    workspace_id: str,
) -> GitWebhookReceivedBody:
    config = step_config(step)
    plan_settings = plan_settings_value(plan)
    commit_sha = required_str(config, plan_settings, "commit_sha")
    image = required_str(config, plan_settings, "image")
    application_id = str(step["application_id"])
    request_payload = {
        "commit_sha": commit_sha,
        "image": image,
        "replicas": int_field(config, "replicas", int_field(application, "replicas", 2)),
        "workspace_id": workspace_id,
        "repo_ref": str(config.get("repo_ref") or application.get("repo_ref") or ""),
        "branch": str(config.get("branch") or application.get("branch") or "main"),
        "application_id": application_id,
        "environment": str(
            config.get("environment") or first_environment(plan_settings) or "sandbox"
        ),
        "cluster_id": str(
            config.get("cluster_id") or application.get("cluster_id") or Target.DEFAULT_CLUSTER_ID
        ),
        "manifest_path": str(
            config.get("manifest_path") or application.get("manifest_path") or "deploy.yaml"
        ),
        "force": True,
    }
    request_payload["workflow_run_id"] = derive_workflow_run_id(request_payload)
    return GitWebhookReceivedBody(**request_payload)


def release_step_application(
    db: Any,
    workspace_id: str,
    steps: list[dict[str, Any]],
    step_index: int,
) -> dict[str, Any]:
    if step_index < 0 or step_index >= len(steps):
        return {}
    application_id = str(steps[step_index].get("application_id") or "")
    get_application = getattr(db, "get_application", None)
    if not application_id or not callable(get_application):
        return {}
    return dict(get_application(workspace_id, application_id) or {})


def required_str(
    config: dict[str, Any],
    settings: dict[str, Any],
    field: str,
) -> str:
    value = str(config.get(field) or settings.get(field) or "").strip()
    if not value:
        raise HTTPException(
            status_code=HTTP_CONFLICT,
            detail={
                "message": RELEASE_PLAN_BLOCKED,
                "blockers": [f"{field} is required before dispatch"],
            },
        )
    return value
