"""Release readiness projections and dispatch guard composition."""

from __future__ import annotations

import os
from datetime import UTC, datetime, timedelta
from typing import Any

from domains.alert.repository import severity_matches
from domains.diagnostics.router import release_plan_diagnostics
from domains.release_flow._support import (
    int_field,
    plan_settings_value,
    release_window_bound_label,
    step_config,
    unique_non_empty,
)
from domains.release_flow.execution import approval_granted, execution_profile, has_change_ticket
from domains.release_flow.policy import (
    active_release_run_blockers,
    approval_max_age_label,
    release_abort_criteria,
    release_abort_criteria_override_reason,
    release_approval_granted_at_label,
    release_diagnostics_blockers,
    release_diagnostics_bypassed,
    release_diagnostics_override_reason,
    release_execution_blockers,
    release_freeze_override_reason,
    release_freeze_window_bounds,
    release_freeze_window_is_active,
    release_owner_contact,
    release_production_abort_criteria_blockers,
    release_production_abort_criteria_bypassed,
    release_production_approval_evidence_blockers,
    release_production_change_override_reason,
    release_production_change_ticket_blockers,
    release_production_change_ticket_bypassed,
    release_production_freeze_blockers,
    release_production_freeze_bypassed,
    release_production_owner_blockers,
    release_production_runbook_blockers,
    release_production_runbook_bypassed,
    release_production_steps_for_wave,
    release_production_window_blockers,
    release_production_window_bypassed,
    release_rollback_override_reason,
    release_rollback_policy_blockers,
    release_rollback_policy_bypassed,
    release_runbook_url,
    release_runbook_url_is_valid,
    release_safe_pr_evidence_blockers,
    release_step_targets_production,
    release_window_bounds,
    release_window_override_reason,
    required_release_input_blockers,
)
from domains.release_flow.verification import (
    release_verification_evidence_present,
    release_verification_job_specs,
    release_verification_url,
)

ALERT_CHANNEL_VALIDATION_MAX_AGE_HOURS_ENV = "RELEASE_FLOW_ALERT_TEST_MAX_AGE_HOURS"
DEFAULT_ALERT_CHANNEL_VALIDATION_MAX_AGE_HOURS = 24


def first_preview_wave(preview: dict[str, Any]) -> int:
    waves = preview.get("waves")
    if isinstance(waves, list) and waves and isinstance(waves[0], dict):
        return int_field(waves[0], "wave", 1)
    return 1


def release_readiness_from_plan(
    plan: dict[str, Any],
    preview: dict[str, Any],
    *,
    workspace_id: str,
    db: Any,
) -> dict[str, Any]:
    first_wave = first_preview_wave(preview)
    profile = execution_profile(plan)
    preview_blockers = [str(item) for item in preview.get("blockers", [])]
    required_blockers = required_release_input_blockers(plan)
    context_blockers = release_dispatch_context_blockers(
        plan,
        [step for step in plan.get("steps", []) if isinstance(step, dict)],
        db,
        workspace_id,
    )
    active_run_blockers = active_release_run_blockers(db, workspace_id, plan)
    execution_blockers = release_execution_blockers(
        plan,
        preview,
        first_wave,
        workspace_id=workspace_id,
        db=db,
    )
    if any("requires a ready Safe PR" in blocker for blocker in execution_blockers):
        execution_blockers.extend(
            release_safe_pr_evidence_blockers(
                plan,
                preview,
                first_wave,
                workspace_id=workspace_id,
                db=db,
            )
        )
    approval_evidence_blockers = release_production_approval_evidence_blockers(
        plan, preview, first_wave
    )
    change_ticket_blockers = release_production_change_ticket_blockers(plan, preview, first_wave)
    change_ticket_bypassed = release_production_change_ticket_bypassed(plan, preview, first_wave)
    window_blockers = release_production_window_blockers(plan, preview, first_wave)
    window_bypassed = release_production_window_bypassed(plan, preview, first_wave)
    freeze_blockers = release_production_freeze_blockers(plan, preview, first_wave)
    freeze_bypassed = release_production_freeze_bypassed(plan, preview, first_wave)
    runbook_blockers = release_production_runbook_blockers(plan, preview, first_wave)
    runbook_bypassed = release_production_runbook_bypassed(plan, preview, first_wave)
    owner_blockers = release_production_owner_blockers(plan, preview, first_wave)
    verification_blockers = release_production_verification_blockers(plan, preview, first_wave)
    verification_bypassed = release_production_verification_bypassed(plan, preview, first_wave)
    abort_criteria_blockers = release_production_abort_criteria_blockers(plan, preview, first_wave)
    abort_criteria_bypassed = release_production_abort_criteria_bypassed(plan, preview, first_wave)
    diagnostic_blockers = release_diagnostics_blockers(plan)
    diagnostic_bypassed = release_diagnostics_bypassed(plan)
    rollback_blockers = release_rollback_policy_blockers(plan)
    rollback_bypassed = release_rollback_policy_bypassed(plan)
    alert_channels = enabled_alert_channels(db, workspace_id)
    live_alert_channels = release_live_alert_channels(db, workspace_id)
    validated_live_alert_channels = release_validated_live_alert_channels(db, workspace_id)
    alert_blockers = release_live_alert_channel_blockers(plan, db, workspace_id)
    retry_attempts = max(0, int_field(plan_settings_value(plan), "retry_attempts", 1))
    alert_validation_window = alert_channel_validation_window_label()
    if profile.side_effects and validated_live_alert_channels:
        alert_message = (
            f"{len(validated_live_alert_channels)} validated alert channel(s) can receive "
            f"warning-or-higher release events within {alert_validation_window}."
        )
    elif profile.side_effects and live_alert_channels:
        alert_message = (
            "Warning-capable alert channels exist, but none has a passing validation test "
            f"within {alert_validation_window}."
        )
    elif live_alert_channels:
        alert_message = (
            f"{len(live_alert_channels)} enabled alert channel(s) can receive "
            "warning-or-higher release events."
        )
    elif alert_channels:
        alert_message = "Enabled alert channels exist, but none receive warning release events."
    else:
        alert_message = (
            "No enabled alert channel is configured for release failure or approval events."
        )

    checks = [
        readiness_check(
            "plan.preview",
            "Plan graph",
            "blocked" if preview_blockers else "passed",
            "Resolve dependency graph blockers before execution."
            if preview_blockers
            else str(preview.get("summary") or "Plan graph can be executed."),
            preview_blockers,
        ),
        readiness_check(
            "plan.required_inputs",
            "Dispatch inputs",
            "blocked" if required_blockers else "passed",
            "Commit SHA and image are required before dispatch."
            if required_blockers
            else "All release steps have the required dispatch inputs.",
            required_blockers,
        ),
        readiness_check(
            "plan.application_context",
            "Application context",
            "blocked" if context_blockers else "passed",
            "Registered application, repository, manifest, and cluster context are required."
            if context_blockers
            else "All release steps resolve to registered application deployment context.",
            context_blockers,
        ),
        readiness_check(
            "plan.active_run_lock",
            "Active run lock",
            "blocked" if active_run_blockers else "passed",
            "This saved release plan already has an active run."
            if active_run_blockers
            else "No active run is blocking this release plan.",
            active_run_blockers,
        ),
        readiness_check(
            "live.dispatch_gate",
            "Live dispatch gate",
            "blocked" if execution_blockers else "passed",
            "Backend live-mode guard or release policy gate is blocking dispatch."
            if execution_blockers
            else f"{profile.label} is allowed for the first executable wave.",
            execution_blockers,
        ),
        readiness_check(
            "approval.evidence",
            "Approval evidence",
            "blocked" if approval_evidence_blockers else "passed",
            "Production approval requires approver, reason, and recent approval time before live dispatch."
            if approval_evidence_blockers
            else f"Approval evidence is complete and recent within {approval_max_age_label()}.",
            approval_evidence_blockers,
        ),
        readiness_check(
            "change.ticket",
            "Change ticket",
            "blocked"
            if change_ticket_blockers
            else "warning"
            if change_ticket_bypassed
            else "passed",
            "Production live release requires a change ticket or override reason."
            if change_ticket_blockers
            else "Production change ticket gate is bypassed with an operator reason."
            if change_ticket_bypassed
            else "Change ticket requirements are satisfied for the first executable wave.",
            change_ticket_blockers,
        ),
        readiness_check(
            "release.window",
            "Release window",
            "blocked" if window_blockers else "warning" if window_bypassed else "passed",
            "Production live release must run inside an approved release window."
            if window_blockers
            else "Production release window is bypassed with an operator reason."
            if window_bypassed
            else "Release window requirements are satisfied for the first executable wave.",
            window_blockers,
        ),
        readiness_check(
            "change.freeze",
            "Change freeze",
            "blocked" if freeze_blockers else "warning" if freeze_bypassed else "passed",
            "Production live release is inside a change freeze window."
            if freeze_blockers
            else "Production change freeze is bypassed with an operator reason."
            if freeze_bypassed
            else "No active production change freeze blocks this release.",
            freeze_blockers,
        ),
        readiness_check(
            "runbook.sop",
            "Runbook",
            "blocked" if runbook_blockers else "warning" if runbook_bypassed else "passed",
            "Production live release requires an accessible runbook URL or operator override reason."
            if runbook_blockers
            else "Production runbook gate is bypassed with an operator reason."
            if runbook_bypassed
            else "Runbook/SOP evidence is present for the first executable wave.",
            runbook_blockers,
        ),
        readiness_check(
            "owner.contact",
            "Owner contact",
            "blocked" if owner_blockers else "passed",
            "Production live release requires a release owner or on-call contact."
            if owner_blockers
            else "Release owner/on-call contact is present for the first executable wave.",
            owner_blockers,
        ),
        readiness_check(
            "verification.plan",
            "Post-deploy verification",
            "blocked"
            if verification_blockers
            else "warning"
            if verification_bypassed
            else "passed",
            "Production live release requires a live https post_deploy_verification_url."
            if verification_blockers
            else "Post-deploy verification gate is bypassed with an operator reason."
            if verification_bypassed
            else "Post-deploy verification evidence is present for the first executable wave.",
            verification_blockers,
        ),
        readiness_check(
            "rollback.abort_criteria",
            "Rollback criteria",
            "blocked"
            if abort_criteria_blockers
            else "warning"
            if abort_criteria_bypassed
            else "passed",
            "Production live release requires rollback_trigger or abort_criteria."
            if abort_criteria_blockers
            else "Rollback criteria gate is bypassed with an operator reason."
            if abort_criteria_bypassed
            else "Rollback/abort criteria are present for the first executable wave.",
            abort_criteria_blockers,
        ),
        readiness_check(
            "plan.diagnostics",
            "Diagnostics gate",
            "blocked" if diagnostic_blockers else "warning" if diagnostic_bypassed else "passed",
            "Deterministic release diagnostics must be resolved before live dispatch."
            if diagnostic_blockers
            else "Diagnostics gate is bypassed with an operator reason."
            if diagnostic_bypassed
            else "Release diagnostics do not block live dispatch.",
            diagnostic_blockers,
        ),
        readiness_check(
            "rollback.policy",
            "Rollback policy",
            "blocked" if rollback_blockers else "warning" if rollback_bypassed else "passed",
            "Live release cannot disable rollback without an operator reason."
            if rollback_blockers
            else "Rollback policy is disabled with an operator reason."
            if rollback_bypassed
            else "Rollback policy is available for this release.",
            rollback_blockers,
        ),
        readiness_check(
            "alerts.enabled_channels",
            "Alert channels",
            "blocked" if alert_blockers else "passed" if alert_channels else "warning",
            alert_message,
            alert_blockers,
        ),
        readiness_check(
            "retry.policy",
            "Retry policy",
            "passed" if retry_attempts > 0 else "warning",
            f"Failed waves can be retried {retry_attempts} time(s)."
            if retry_attempts > 0
            else "Failed waves cannot be retried automatically from this plan.",
        ),
        readiness_check(
            "audit.redaction",
            "Audit and redaction",
            "passed",
            "Run events are audit-exportable and sensitive event details are redacted.",
        ),
    ]
    blockers = [
        item
        for check in checks
        for item in check.get("blockers", [])
        if check["status"] == "blocked"
    ]
    warnings = [str(check["message"]) for check in checks if check["status"] == "warning"]
    if blockers:
        summary = f"{len(blockers)} blocker(s) must be resolved before release dispatch."
    elif warnings:
        summary = f"Runnable with {len(warnings)} operational warning(s)."
    else:
        summary = f"Ready for {profile.label.lower()}."
    return {
        "ready": not blockers,
        "mode": profile.runtime_mode,
        "summary": summary,
        "checks": checks,
        "impact": release_readiness_impact(plan, preview, profile),
        "next_actions": release_readiness_next_actions(checks),
        "blockers": blockers,
        "warnings": warnings,
    }


def readiness_check(
    check_id: str,
    name: str,
    status: str,
    message: str,
    blockers: list[str] | None = None,
) -> dict[str, Any]:
    return {
        "check_id": check_id,
        "name": name,
        "status": status,
        "message": message,
        "blockers": blockers or [],
    }


def release_readiness_impact(
    plan: dict[str, Any], preview: dict[str, Any], profile: Any
) -> dict[str, Any]:
    preview_steps = [step for step in preview.get("steps", []) if isinstance(step, dict)]
    waves = sorted(
        {int_field(step, "wave", 0) for step in preview_steps if int_field(step, "wave", 0) > 0}
    )
    applications = unique_non_empty(str(step.get("application_id") or "") for step in preview_steps)
    environments = unique_non_empty(str(step.get("environment") or "") for step in preview_steps)
    first_wave = waves[0] if waves else first_preview_wave(preview)
    first_wave_steps = [
        readiness_impact_step(step)
        for step in preview_steps
        if int_field(step, "wave", first_wave) == first_wave
    ]
    production_targets = [
        {
            "application_id": str(step.get("application_id") or ""),
            "name": str(step.get("name") or step.get("application_id") or ""),
            "environment": str(
                step_config(step).get("environment")
                or plan_settings_value(plan).get("environment")
                or ""
            ),
        }
        for step in plan.get("steps", [])
        if isinstance(step, dict) and release_step_targets_production(plan, step)
    ]
    production_labels = unique_non_empty(
        str(item.get("name") or item.get("application_id") or "") for item in production_targets
    )
    return {
        "summary": release_readiness_impact_summary(
            len(preview_steps),
            len(applications),
            len(environments),
            len(waves),
            len(production_targets),
            bool(getattr(profile, "side_effects", False)),
        ),
        "runtime_mode": str(getattr(profile, "runtime_mode", "")),
        "live_side_effects": bool(getattr(profile, "side_effects", False)),
        "total_steps": len(preview_steps),
        "total_waves": len(waves),
        "first_wave": first_wave,
        "applications": applications,
        "environments": environments,
        "production_targets": production_labels,
        "production_target_count": len(production_targets),
        "first_wave_steps": first_wave_steps,
    }


def readiness_impact_step(step: dict[str, Any]) -> dict[str, Any]:
    return {
        "step_id": str(step.get("step_id") or ""),
        "application_id": str(step.get("application_id") or ""),
        "name": str(step.get("name") or step.get("application_id") or ""),
        "environment": str(step.get("environment") or ""),
        "action": str(step.get("action") or ""),
        "strategy": str(step.get("strategy") or ""),
        "wave": int_field(step, "wave", 0) or None,
    }


def release_readiness_impact_summary(
    step_count: int,
    application_count: int,
    environment_count: int,
    wave_count: int,
    production_target_count: int,
    live_side_effects: bool,
) -> str:
    mode_label = "live" if live_side_effects else "dry-run"
    production_label = (
        f", {production_target_count} production target(s)" if production_target_count else ""
    )
    return (
        f"{mode_label} impact covers {step_count} step(s), {application_count} application(s), "
        f"{environment_count} environment(s), and {wave_count} wave(s){production_label}."
    )


def release_readiness_next_actions(checks: list[dict[str, Any]]) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    for check in checks:
        status = str(check.get("status") or "")
        if status not in {"blocked", "warning"}:
            continue
        check_id = str(check.get("check_id") or "")
        name = str(check.get("name") or check_id or "Readiness check")
        blockers = [str(item) for item in check.get("blockers", [])]
        verb = "Resolve" if status == "blocked" else "Review"
        actions.append(
            {
                "action_id": f"{status}.{check_id}" if check_id else status,
                "check_id": check_id,
                "label": f"{verb} {name}",
                "severity": status,
                "message": str(check.get("message") or ""),
                "blockers": blockers,
            }
        )
    return actions


def release_production_verification_blockers(
    plan: dict[str, Any],
    preview: dict[str, Any],
    wave: int,
) -> list[str]:
    if not execution_profile(plan).side_effects:
        return []
    settings = plan_settings_value(plan)
    blockers: list[str] = []
    for step in release_production_steps_for_wave(plan, preview, wave):
        config = step_config(step)
        if release_verification_evidence_present(settings, config):
            continue
        label = str(step.get("name") or step.get("application_id") or "release step")
        blockers.append(
            f"{label} targets production and requires live https post_deploy_verification_url "
            "before live dispatch."
        )
    return blockers


def release_production_verification_bypassed(
    plan: dict[str, Any],
    preview: dict[str, Any],
    wave: int,
) -> bool:
    return False


def release_dispatch_guard_snapshot(
    plan: dict[str, Any],
    db: Any,
    workspace_id: str,
    preview: dict[str, Any],
    wave: int,
) -> dict[str, Any]:
    profile = execution_profile(plan)
    settings = plan_settings_value(plan)
    diagnostics = release_plan_diagnostics(plan) if profile.side_effects else []
    blocking_diagnostics = [
        diagnostic for diagnostic in diagnostics if diagnostic.severity in {"error", "warning"}
    ]
    validated_channels = release_validated_live_alert_channels(db, workspace_id)
    live_channels = release_live_alert_channels(db, workspace_id)
    production_steps = release_production_steps_for_wave(plan, preview, wave)
    window_start, window_end = release_window_bounds(plan)
    freeze_start, freeze_end = release_freeze_window_bounds(plan)
    verification_jobs = release_verification_job_specs(plan, production_steps, wave)
    return {
        "runtime_mode": profile.runtime_mode,
        "side_effects": profile.side_effects,
        "readiness": release_dispatch_readiness_snapshot(plan, preview, profile, wave),
        "change_management": {
            "change_ticket_present": any(
                has_change_ticket(settings, step_config(step)) for step in production_steps
            ),
            "production_targets": [
                str(step.get("application_id") or "") for step in production_steps
            ],
            "production_override_reason": release_production_change_override_reason(plan) or None,
        },
        "approval": {
            "production_targets": [
                str(step.get("application_id") or "") for step in production_steps
            ],
            "granted": any(
                approval_granted(settings, step_config(step)) for step in production_steps
            ),
            "granted_by": str(settings.get("approval_granted_by") or "").strip() or None,
            "reason": str(settings.get("approval_reason") or "").strip() or None,
            "granted_at": release_approval_granted_at_label(settings),
            "max_age": approval_max_age_label(),
        },
        "release_window": {
            "start": release_window_bound_label(window_start) if window_start else None,
            "end": release_window_bound_label(window_end) if window_end else None,
            "override_reason": release_window_override_reason(plan) or None,
            "production_targets": [
                str(step.get("application_id") or "") for step in production_steps
            ],
        },
        "change_freeze": {
            "start": release_window_bound_label(freeze_start) if freeze_start else None,
            "end": release_window_bound_label(freeze_end) if freeze_end else None,
            "active": release_freeze_window_is_active(plan),
            "override_reason": release_freeze_override_reason(plan) or None,
            "production_targets": [
                str(step.get("application_id") or "") for step in production_steps
            ],
        },
        "runbook": {
            "url": str(settings.get("runbook_url") or "").strip() or None,
            "url_present": any(
                release_runbook_url_is_valid(release_runbook_url(settings, step_config(step)))
                for step in production_steps
            ),
            "override_reason": None,
            "production_targets": [
                str(step.get("application_id") or "") for step in production_steps
            ],
        },
        "owner": {
            "release_owner": str(settings.get("release_owner") or "").strip() or None,
            "oncall_contact": str(settings.get("oncall_contact") or "").strip() or None,
            "contact_present": any(
                bool(release_owner_contact(settings, step_config(step)))
                for step in production_steps
            ),
            "production_targets": [
                str(step.get("application_id") or "") for step in production_steps
            ],
        },
        "verification": {
            "evidence_present": any(
                release_verification_evidence_present(settings, step_config(step))
                for step in production_steps
            ),
            "override_reason": None,
            "production_targets": [
                str(step.get("application_id") or "") for step in production_steps
            ],
            "health_check_paths": [
                str(
                    step_config(step).get("health_check_path")
                    or settings.get("health_check_path")
                    or ""
                )
                for step in production_steps
                if str(
                    step_config(step).get("health_check_path")
                    or settings.get("health_check_path")
                    or ""
                ).strip()
            ],
            "verification_urls": [
                release_verification_url(settings, step_config(step))
                for step in production_steps
                if release_verification_url(settings, step_config(step))
            ],
        },
        "verification_jobs": {
            "scheduled": profile.side_effects and bool(verification_jobs),
            "job_count": len(verification_jobs) if profile.side_effects else 0,
            "jobs": verification_jobs if profile.side_effects else [],
        },
        "abort_criteria": {
            "criteria": [
                release_abort_criteria(settings, step_config(step))
                for step in production_steps
                if release_abort_criteria(settings, step_config(step))
            ],
            "override_reason": release_abort_criteria_override_reason(plan) or None,
            "production_targets": [
                str(step.get("application_id") or "") for step in production_steps
            ],
        },
        "diagnostics": {
            "required": settings.get("require_diagnostics_pass") is not False,
            "bypassed": release_diagnostics_bypassed(plan),
            "override_reason": release_diagnostics_override_reason(plan) or None,
            "blocking_count": len(blocking_diagnostics),
            "blocking_codes": [str(diagnostic.code) for diagnostic in blocking_diagnostics],
        },
        "rollback": {
            "policy": str(settings.get("rollback_policy") or "manual"),
            "disabled": str(settings.get("rollback_policy") or "manual") == "disabled",
            "override_reason": release_rollback_override_reason(plan) or None,
        },
        "alerts": {
            "validation_window": alert_channel_validation_window_label(),
            "warning_capable_count": len(live_channels),
            "validated_count": len(validated_channels),
            "validated_channels": [
                {
                    "channel_id": str(channel.get("channel_id") or ""),
                    "kind": str(channel.get("kind") or ""),
                    "min_severity": str(channel.get("min_severity") or "warning"),
                    "last_tested_at": channel.get("last_tested_at"),
                }
                for channel in validated_channels
            ],
        },
    }


def release_dispatch_readiness_snapshot(
    plan: dict[str, Any],
    preview: dict[str, Any],
    profile: Any,
    wave: int,
) -> dict[str, Any]:
    warning_checks = release_dispatch_readiness_warning_checks(plan, preview, wave)
    return {
        "ready": True,
        "checked_wave": wave,
        "summary": f"Dispatch guards passed for wave {wave}.",
        "impact": release_readiness_impact(plan, preview, profile),
        "selected_wave_steps": [
            readiness_impact_step(step)
            for step in preview.get("steps", [])
            if isinstance(step, dict) and int_field(step, "wave", 0) == wave
        ],
        "warnings": [str(check["message"]) for check in warning_checks],
        "next_actions": release_readiness_next_actions(warning_checks),
    }


def release_dispatch_readiness_warning_checks(
    plan: dict[str, Any],
    preview: dict[str, Any],
    wave: int,
) -> list[dict[str, Any]]:
    settings = plan_settings_value(plan)
    retry_attempts = max(0, int_field(settings, "retry_attempts", 1))
    checks: list[dict[str, Any]] = []
    if release_production_change_ticket_bypassed(plan, preview, wave):
        checks.append(
            readiness_check(
                "change.ticket",
                "Change ticket",
                "warning",
                "Production change ticket gate is bypassed with an operator reason.",
            )
        )
    if release_production_window_bypassed(plan, preview, wave):
        checks.append(
            readiness_check(
                "release.window",
                "Release window",
                "warning",
                "Production release window is bypassed with an operator reason.",
            )
        )
    if release_production_freeze_bypassed(plan, preview, wave):
        checks.append(
            readiness_check(
                "change.freeze",
                "Change freeze",
                "warning",
                "Production change freeze is bypassed with an operator reason.",
            )
        )
    if release_production_runbook_bypassed(plan, preview, wave):
        checks.append(
            readiness_check(
                "runbook.sop",
                "Runbook",
                "warning",
                "Production runbook gate is bypassed with an operator reason.",
            )
        )
    if release_production_verification_bypassed(plan, preview, wave):
        checks.append(
            readiness_check(
                "verification.plan",
                "Post-deploy verification",
                "warning",
                "Post-deploy verification gate is bypassed with an operator reason.",
            )
        )
    if release_production_abort_criteria_bypassed(plan, preview, wave):
        checks.append(
            readiness_check(
                "rollback.abort_criteria",
                "Rollback criteria",
                "warning",
                "Rollback criteria gate is bypassed with an operator reason.",
            )
        )
    if release_diagnostics_bypassed(plan):
        checks.append(
            readiness_check(
                "plan.diagnostics",
                "Diagnostics gate",
                "warning",
                "Diagnostics gate is bypassed with an operator reason.",
            )
        )
    if release_rollback_policy_bypassed(plan):
        checks.append(
            readiness_check(
                "rollback.policy",
                "Rollback policy",
                "warning",
                "Rollback policy is disabled with an operator reason.",
            )
        )
    if retry_attempts <= 0:
        checks.append(
            readiness_check(
                "retry.policy",
                "Retry policy",
                "warning",
                "Failed waves cannot be retried automatically from this plan.",
            )
        )
    return checks


def release_dispatch_context_blockers(
    plan: dict[str, Any],
    steps: list[dict[str, Any]],
    db: Any,
    workspace_id: str,
) -> list[str]:
    if not callable(getattr(db, "get_application", None)):
        return []
    blockers: list[str] = []
    for index, step in enumerate(steps, start=1):
        application_id = str(step.get("application_id") or "").strip()
        label = str(step.get("name") or application_id or f"step {index}")
        if not application_id:
            blockers.append(f"{label} is missing application_id.")
            continue
        application = db.get_application(workspace_id, application_id)
        if not application:
            blockers.append(f"Application {application_id} is not registered in this workspace.")
            continue
        config = step_config(step)
        for field, display in (
            ("repo_ref", "repository"),
            ("branch", "branch"),
            ("manifest_path", "manifest path"),
            ("cluster_id", "cluster"),
        ):
            if not dispatch_context_value(config, application, field):
                blockers.append(f"{label} is missing {display} context.")
    return blockers


def dispatch_context_value(
    config: dict[str, Any],
    application: dict[str, Any],
    field: str,
) -> str:
    if field == "branch":
        value = (
            config.get("branch") or application.get("branch") or application.get("default_branch")
        )
    elif field == "manifest_path":
        value = config.get("manifest_path") or application.get("manifest_path")
    else:
        value = config.get(field) or application.get(field)
    return str(value or "").strip()


def enabled_alert_channels(db: Any, workspace_id: str) -> list[dict[str, Any]]:
    list_channels = getattr(db, "list_alert_channels", None)
    if not callable(list_channels):
        return []
    try:
        channels = list_channels(workspace_id, only_enabled=True)
    except TypeError:
        channels = [
            channel
            for channel in list_channels(workspace_id)
            if isinstance(channel, dict) and channel.get("enabled", True)
        ]
    return [channel for channel in channels if isinstance(channel, dict)]


def release_live_alert_channel_blockers(
    plan: dict[str, Any],
    db: Any,
    workspace_id: str,
) -> list[str]:
    if not execution_profile(plan).side_effects:
        return []
    if release_validated_live_alert_channels(db, workspace_id):
        return []
    if release_live_alert_channels(db, workspace_id):
        return [
            "Live release dispatch requires at least one warning-capable alert channel "
            f"with a passing validation test within {alert_channel_validation_window_label()}."
        ]
    return [
        "Live release dispatch requires at least one enabled alert channel that receives warning-or-higher release events."
    ]


def release_live_alert_channels(db: Any, workspace_id: str) -> list[dict[str, Any]]:
    return [
        channel
        for channel in enabled_alert_channels(db, workspace_id)
        if severity_matches(str(channel.get("min_severity") or "warning"), "warning")
    ]


def release_validated_live_alert_channels(db: Any, workspace_id: str) -> list[dict[str, Any]]:
    now = datetime.now(UTC)
    max_age = alert_channel_validation_max_age()
    return [
        channel
        for channel in release_live_alert_channels(db, workspace_id)
        if alert_channel_validation_is_current(channel, now=now, max_age=max_age)
    ]


def alert_channel_validation_is_current(
    channel: dict[str, Any],
    *,
    now: datetime,
    max_age: timedelta,
) -> bool:
    if str(channel.get("last_test_status") or "").lower() != "passed":
        return False
    tested_at = alert_channel_tested_at(channel)
    if tested_at is None:
        return False
    return now - tested_at <= max_age


def alert_channel_tested_at(channel: dict[str, Any]) -> datetime | None:
    value = channel.get("last_tested_at")
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str):
        raw = value.strip()
        if not raw:
            return None
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def alert_channel_validation_max_age() -> timedelta:
    raw = os.getenv(
        ALERT_CHANNEL_VALIDATION_MAX_AGE_HOURS_ENV,
        str(DEFAULT_ALERT_CHANNEL_VALIDATION_MAX_AGE_HOURS),
    ).strip()
    try:
        hours = float(raw)
    except ValueError:
        hours = float(DEFAULT_ALERT_CHANNEL_VALIDATION_MAX_AGE_HOURS)
    return timedelta(hours=max(1.0, hours))


def alert_channel_validation_window_label() -> str:
    hours = alert_channel_validation_max_age().total_seconds() / 3600
    if hours.is_integer():
        return f"{int(hours)} hour(s)"
    return f"{hours:.1f} hour(s)"
