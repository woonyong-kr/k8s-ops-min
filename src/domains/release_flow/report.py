"""Release run filtering, handoff, report, and audit serialization."""

from __future__ import annotations

import csv
import io
import json
import os
from datetime import UTC, datetime, timedelta
from typing import Any

from domains.release_flow._support import int_field, parse_release_window_time
from domains.release_flow.redaction import redact_release_value
from domains.release_flow.verification import (
    VERIFICATION_JOB_FAILED_STATUSES,
    VERIFICATION_JOB_PENDING_STATUSES,
    release_run_has_failed_verification,
    release_run_has_timed_out_verification,
    release_verification_job_pending_timeouts,
    release_verification_job_status,
)

TERMINAL_RELEASE_RUN_STATUSES = {"succeeded", "failed", "cancelled", "rollback_requested"}
RELEASE_NOTIFY_COOLDOWN_MINUTES_ENV = "RELEASE_FLOW_NOTIFY_COOLDOWN_MINUTES"
DEFAULT_RELEASE_NOTIFY_COOLDOWN_MINUTES = 10
RELEASE_GUARD_POLICY_OVERRIDE_PATHS = (
    ("change_management", "production_override_reason", "Production change"),
    ("release_window", "override_reason", "Release window"),
    ("change_freeze", "override_reason", "Change freeze"),
    ("runbook", "override_reason", "Runbook"),
    ("verification", "override_reason", "Post-deploy verification"),
    ("abort_criteria", "override_reason", "Rollback criteria"),
    ("diagnostics", "override_reason", "Diagnostics"),
    ("rollback", "override_reason", "Rollback policy"),
)


def release_notify_cooldown_blocker(run: dict[str, Any]) -> str | None:
    cooldown = release_notify_cooldown()
    events = run.get("events") if isinstance(run.get("events"), list) else []
    now = datetime.now(UTC)
    for event in reversed(events):
        if not isinstance(event, dict):
            continue
        event_type = str(event.get("event_type") or "")
        if not event_type.startswith("release.notify"):
            continue
        created_at = parse_release_window_time(event.get("created_at"))
        if created_at is None:
            continue
        age = now - created_at.astimezone(UTC)
        if age < cooldown:
            remaining_seconds = max(0, int((cooldown - age).total_seconds()))
            remaining_minutes = max(1, (remaining_seconds + 59) // 60)
            return (
                "Release notification was already sent recently; "
                f"wait about {remaining_minutes} minute(s) before notifying again."
            )
    return None


def release_notify_cooldown() -> timedelta:
    raw = os.getenv(RELEASE_NOTIFY_COOLDOWN_MINUTES_ENV, "").strip()
    try:
        minutes = float(raw) if raw else DEFAULT_RELEASE_NOTIFY_COOLDOWN_MINUTES
    except ValueError:
        minutes = DEFAULT_RELEASE_NOTIFY_COOLDOWN_MINUTES
    return timedelta(minutes=max(1.0, minutes))


def release_run_rollback_policy(run: dict[str, Any]) -> str:
    rollback = run.get("rollback")
    if isinstance(rollback, dict):
        policy = str(rollback.get("policy") or "").strip()
        if policy:
            return policy
    settings = run.get("settings")
    if isinstance(settings, dict):
        policy = str(settings.get("rollback_policy") or "").strip()
        if policy:
            return policy
    return "manual"


def first_release_run_step(run: dict[str, Any]) -> dict[str, Any]:
    steps = run.get("steps") if isinstance(run.get("steps"), list) else []
    for step in steps:
        if isinstance(step, dict):
            return step
    return {}


def filter_release_runs(
    runs: list[dict[str, Any]],
    *,
    status: str | None = None,
    attention_only: bool = False,
    stale_only: bool = False,
    active_only: bool = False,
    live_only: bool = False,
    unhealthy_only: bool = False,
    verification_failed_only: bool = False,
    verification_pending_timeout_only: bool = False,
    policy_override_only: bool = False,
    policy_override_source: str | None = None,
    active_change_freeze_only: bool = False,
    change_freeze_override_only: bool = False,
) -> list[dict[str, Any]]:
    expected_status = str(status or "").strip().lower()
    expected_policy_override_source = release_policy_override_source_key(policy_override_source)
    filtered: list[dict[str, Any]] = []
    for run in runs:
        run_status = str(run.get("derived_status") or run.get("status") or "").lower()
        attention = run.get("attention") if isinstance(run.get("attention"), dict) else {}
        if expected_status and run_status != expected_status:
            continue
        if attention_only and attention.get("required") is not True:
            continue
        if stale_only and attention.get("stale") is not True:
            continue
        if active_only and run_status in TERMINAL_RELEASE_RUN_STATUSES:
            continue
        if live_only and not release_run_has_live_side_effects(run):
            continue
        if unhealthy_only and not release_run_has_unhealthy_health(run):
            continue
        if verification_failed_only and not release_run_has_failed_verification(run):
            continue
        if verification_pending_timeout_only and not release_run_has_timed_out_verification(run):
            continue
        if policy_override_only and not release_run_has_policy_override(run):
            continue
        if expected_policy_override_source and not release_run_has_policy_override_source(
            run,
            expected_policy_override_source,
        ):
            continue
        if active_change_freeze_only and not release_run_has_active_change_freeze(run):
            continue
        if change_freeze_override_only and not release_run_has_change_freeze_override(run):
            continue
        filtered.append(run)
    return filtered


def release_run_has_live_side_effects(run: dict[str, Any]) -> bool:
    settings = run.get("settings") if isinstance(run.get("settings"), dict) else {}
    if settings.get("runtime_mode") == "live" or settings.get("provider_mode") == "live":
        return True
    steps = run.get("steps") if isinstance(run.get("steps"), list) else []
    return any(
        isinstance(step, dict)
        and isinstance(step.get("details"), dict)
        and step["details"].get("side_effects") is True
        for step in steps
    )


def release_run_has_unhealthy_health(run: dict[str, Any]) -> bool:
    health = run.get("health") if isinstance(run.get("health"), dict) else {}
    if str(health.get("status") or "").strip().lower() == "unhealthy":
        return True
    steps = run.get("steps") if isinstance(run.get("steps"), list) else []
    return any(
        isinstance(step, dict)
        and isinstance(step.get("health"), dict)
        and str(step["health"].get("status") or "").strip().lower() == "unhealthy"
        for step in steps
    )


def release_run_change_freeze_snapshot(run: dict[str, Any]) -> dict[str, Any]:
    guard = release_run_latest_guard(run)
    freeze = guard.get("change_freeze") if isinstance(guard.get("change_freeze"), dict) else {}
    return freeze


def release_run_has_change_freeze_override(run: dict[str, Any]) -> bool:
    freeze = release_run_change_freeze_snapshot(run)
    return bool(str(freeze.get("override_reason") or "").strip())


def release_run_has_active_change_freeze(run: dict[str, Any]) -> bool:
    freeze = release_run_change_freeze_snapshot(run)
    return freeze.get("active") is True


def release_run_has_policy_override(run: dict[str, Any]) -> bool:
    return bool(release_run_policy_overrides(run))


def release_policy_override_source_key(value: Any) -> str:
    return str(value or "").strip().lower().replace("_", " ").replace("-", " ")


def release_run_has_policy_override_source(run: dict[str, Any], source: str) -> bool:
    expected_source = release_policy_override_source_key(source)
    return any(
        release_policy_override_source_key(override.get("source")) == expected_source
        for override in release_run_policy_overrides(run)
    )


def release_run_policy_overrides(run: dict[str, Any]) -> list[dict[str, Any]]:
    guard = release_run_latest_guard(run)
    overrides: list[dict[str, Any]] = []
    for section_key, reason_key, label in RELEASE_GUARD_POLICY_OVERRIDE_PATHS:
        section = guard.get(section_key)
        if not isinstance(section, dict):
            continue
        reason = str(section.get(reason_key) or "").strip()
        if not reason:
            continue
        targets = (
            section.get("production_targets")
            if isinstance(section.get("production_targets"), list)
            else []
        )
        overrides.append(
            {
                "source": label,
                "reason": reason,
                "production_targets": [str(item) for item in targets if str(item).strip()],
            }
        )
    return overrides


def release_run_summary_from_runs(runs: list[dict[str, Any]]) -> dict[str, Any]:
    status_breakdown: dict[str, int] = {}
    plan_breakdown: dict[str, int] = {}
    recent_runs: list[dict[str, Any]] = []
    active_runs = 0
    succeeded_runs = 0
    cancelled_runs = 0
    failed_runs = 0
    paused_runs = 0
    rollback_requested_runs = 0
    waiting_for_approval_runs = 0
    live_runs = 0
    unhealthy_runs = 0
    verification_failed_runs = 0
    verification_pending_timeout_runs = 0
    policy_override_runs = 0
    policy_override_breakdown: dict[str, int] = {}
    active_change_freeze_runs = 0
    change_freeze_override_runs = 0
    stale_runs = 0
    attention_required_runs = 0
    last_run_status = ""
    for run in runs:
        status = str(run.get("derived_status") or run.get("status") or "unknown")
        plan_id = str(run.get("plan_id") or "")
        attention_reasons = release_attention_reasons(run)
        if not last_run_status:
            last_run_status = status
        status_breakdown[status] = status_breakdown.get(status, 0) + 1
        if plan_id:
            plan_breakdown[plan_id] = plan_breakdown.get(plan_id, 0) + 1
        if status not in TERMINAL_RELEASE_RUN_STATUSES:
            active_runs += 1
        if status == "succeeded":
            succeeded_runs += 1
        if status == "cancelled":
            cancelled_runs += 1
        if status == "failed":
            failed_runs += 1
        if status == "paused":
            paused_runs += 1
        if status == "rollback_requested":
            rollback_requested_runs += 1
        if status == "waiting_for_approval":
            waiting_for_approval_runs += 1
        status_needs_attention = status in {"failed", "waiting_for_approval", "rollback_requested"}
        if status_needs_attention:
            attention_required_runs += 1
        health = run.get("health") if isinstance(run.get("health"), dict) else {}
        attention = run.get("attention") if isinstance(run.get("attention"), dict) else {}
        if attention.get("stale") is True:
            stale_runs += 1
        health_needs_attention = health.get("status") == "unhealthy" and not status_needs_attention
        if health.get("status") == "unhealthy":
            unhealthy_runs += 1
            if health_needs_attention:
                attention_required_runs += 1
        if release_run_has_failed_verification(run):
            verification_failed_runs += 1
        verification_pending_timed_out = release_run_has_timed_out_verification(run)
        if verification_pending_timed_out:
            verification_pending_timeout_runs += 1
        policy_overrides = release_run_policy_overrides(run)
        if policy_overrides:
            policy_override_runs += 1
            for override in policy_overrides:
                source = str(override.get("source") or "Policy override")
                policy_override_breakdown[source] = policy_override_breakdown.get(source, 0) + 1
        if release_run_has_active_change_freeze(run):
            active_change_freeze_runs += 1
        if release_run_has_change_freeze_override(run):
            change_freeze_override_runs += 1
        if (
            (attention.get("required") is True or verification_pending_timed_out)
            and not status_needs_attention
            and not health_needs_attention
        ):
            attention_required_runs += 1
        if release_run_has_live_side_effects(run):
            live_runs += 1
        if len(recent_runs) < 10:
            recent_runs.append(
                {
                    "run_id": str(run.get("run_id") or ""),
                    "plan_id": plan_id,
                    "status": status,
                    "attention_reasons": attention_reasons,
                }
            )
    return {
        "total_runs": len(runs),
        "status_breakdown": status_breakdown,
        "plan_breakdown": plan_breakdown,
        "active_runs": active_runs,
        "succeeded_runs": succeeded_runs,
        "cancelled_runs": cancelled_runs,
        "attention_required_runs": attention_required_runs,
        "failed_runs": failed_runs,
        "paused_runs": paused_runs,
        "rollback_requested_runs": rollback_requested_runs,
        "waiting_for_approval_runs": waiting_for_approval_runs,
        "live_runs": live_runs,
        "unhealthy_runs": unhealthy_runs,
        "verification_failed_runs": verification_failed_runs,
        "verification_pending_timeout_runs": verification_pending_timeout_runs,
        "policy_override_runs": policy_override_runs,
        "policy_override_breakdown": policy_override_breakdown,
        "active_change_freeze_runs": active_change_freeze_runs,
        "change_freeze_override_runs": change_freeze_override_runs,
        "stale_runs": stale_runs,
        "last_run_status": last_run_status or None,
        "recent_runs": recent_runs,
    }


def release_run_handoff(run: dict[str, Any]) -> dict[str, Any]:
    status = str(run.get("derived_status") or run.get("status") or "unknown")
    attention = run.get("attention") if isinstance(run.get("attention"), dict) else {}
    health = run.get("health") if isinstance(run.get("health"), dict) else {}
    rollback_policy = release_run_rollback_policy(run)
    attention_reasons = release_attention_reasons(run)
    stale = attention.get("stale") is True
    live_side_effects = release_run_has_live_side_effects(run)
    retryable = release_run_is_retryable(run)
    terminal = status in TERMINAL_RELEASE_RUN_STATUSES
    alertable = stale or attention.get("required") is True or bool(attention_reasons)
    notify_blocker = release_notify_cooldown_blocker(run)
    verification = release_run_handoff_verification(run)
    abort_criteria = release_run_handoff_abort_criteria(run)
    change_freeze = release_run_handoff_change_freeze(run)
    policy_overrides = release_run_policy_overrides(run)
    return {
        "run_id": str(run.get("run_id") or ""),
        "plan_id": str(run.get("plan_id") or ""),
        "plan_name": str(run.get("plan_name") or "Release run"),
        "status": status,
        "headline": release_run_handoff_headline(run, status, alertable=alertable, stale=stale),
        "severity": release_run_handoff_severity(status, alertable=alertable, stale=stale),
        "current_wave": int(run.get("current_wave") or 0),
        "total_waves": int(run.get("total_waves") or 0),
        "live_side_effects": live_side_effects,
        "attention_reasons": attention_reasons,
        "verification": verification,
        "abort_criteria": abort_criteria,
        "change_freeze": change_freeze,
        "policy_overrides": policy_overrides,
        "next_actions": release_run_handoff_actions(
            status,
            terminal=terminal,
            retryable=retryable,
            alertable=alertable,
            rollback_enabled=rollback_policy != "disabled",
            notify_blocker=notify_blocker,
            rollback_blocker="Rollback policy is disabled for this release run."
            if rollback_policy == "disabled"
            else None,
        ),
        "checks": [
            release_handoff_check(
                "mode",
                "warning" if live_side_effects else "info",
                "Live side effects are enabled for this run."
                if live_side_effects
                else "Demo/dry-run mode is active.",
            ),
            release_handoff_check(
                "health",
                "blocked" if health.get("status") == "unhealthy" else "passed",
                f"Health is {str(health.get('status') or 'unknown')}.",
            ),
            release_handoff_check(
                "attention",
                "blocked" if notify_blocker else "warning" if alertable else "passed",
                notify_blocker
                or (
                    "; ".join(attention_reasons[:3])
                    if attention_reasons
                    else "No operator attention reason is recorded."
                ),
            ),
            release_handoff_check(
                "rollback",
                "blocked" if rollback_policy == "disabled" else "passed",
                f"Rollback policy is {rollback_policy}.",
            ),
            release_handoff_check(
                "verification",
                str(verification.get("status") or "info"),
                str(verification.get("message") or "No verification snapshot recorded."),
            ),
            release_handoff_check(
                "abort_criteria",
                str(abort_criteria.get("status") or "info"),
                str(abort_criteria.get("message") or "No rollback criteria snapshot recorded."),
            ),
            release_handoff_check(
                "change_freeze",
                str(change_freeze.get("status") or "info"),
                str(change_freeze.get("message") or "No change freeze snapshot recorded."),
            ),
            release_handoff_check(
                "policy_overrides",
                "warning" if policy_overrides else "passed",
                f"{len(policy_overrides)} policy override reason(s) recorded."
                if policy_overrides
                else "No policy override reasons are recorded.",
            ),
        ],
        "last_event": release_run_last_event(run),
    }


def release_run_report(run: dict[str, Any], audit_events: list[dict[str, Any]]) -> dict[str, Any]:
    handoff = release_run_handoff(run)
    generated_at = datetime.now(UTC).isoformat()
    public_audit_events = audit_events[:20]
    return {
        "run_id": str(run.get("run_id") or ""),
        "plan_id": str(run.get("plan_id") or ""),
        "plan_name": str(run.get("plan_name") or "Release run"),
        "status": str(run.get("derived_status") or run.get("status") or "unknown"),
        "current_wave": int_field(run, "current_wave", 0),
        "total_waves": int_field(run, "total_waves", 0),
        "generated_at": generated_at,
        "handoff": handoff,
        "audit_events": public_audit_events,
        "markdown": release_run_report_markdown(run, handoff, public_audit_events, generated_at),
    }


def release_run_report_markdown(
    run: dict[str, Any],
    handoff: dict[str, Any],
    audit_events: list[dict[str, Any]],
    generated_at: str,
) -> str:
    status = str(run.get("derived_status") or run.get("status") or "unknown")
    health = run.get("health") if isinstance(run.get("health"), dict) else {}
    lines = [
        f"## Release run report: {run.get('plan_name') or run.get('run_id') or 'release run'}",
        "",
        f"- Run: {run.get('run_id') or ''}",
        f"- Status: {status}",
        f"- Wave: {int_field(run, 'current_wave', 0)} of {int_field(run, 'total_waves', 0)}",
        f"- Mode: {'live side effects' if release_run_has_live_side_effects(run) else 'demo/dry-run'}",
        f"- Health: {health.get('status') or 'pending'}",
        f"- Generated at: {generated_at}",
        "",
        f"Headline: {handoff.get('headline') or ''}",
        f"Severity: {handoff.get('severity') or 'info'}",
    ]
    attention_reasons = release_attention_reasons(run)
    if attention_reasons:
        lines.extend(["", "Attention:", *[f"- {reason}" for reason in attention_reasons]])
    target_lines = release_run_report_target_lines(run)
    if target_lines:
        lines.extend(["", "Targets:", *target_lines])
    approval_lines = release_run_report_approval_lines(run)
    if approval_lines:
        lines.extend(["", "Approvals:", *approval_lines])
    next_actions = (
        handoff.get("next_actions") if isinstance(handoff.get("next_actions"), list) else []
    )
    if next_actions:
        lines.extend(["", "Next actions:"])
        for action in next_actions[:6]:
            if not isinstance(action, dict):
                continue
            marker = "[ ]" if action.get("enabled") is not False else "[blocked]"
            reason = f": {action.get('reason')}" if action.get("reason") else ""
            lines.append(
                f"- {marker} {action.get('label') or action.get('action') or 'action'}{reason}"
            )
    checks = handoff.get("checks") if isinstance(handoff.get("checks"), list) else []
    if checks:
        lines.extend(["", "Checks:"])
        for check in checks[:8]:
            if not isinstance(check, dict):
                continue
            lines.append(
                f"- {check.get('name') or 'check'}: {check.get('status') or 'info'} "
                f"({check.get('message') or 'No message.'})"
            )
    policy_overrides = (
        handoff.get("policy_overrides") if isinstance(handoff.get("policy_overrides"), list) else []
    )
    if policy_overrides:
        lines.extend(["", "Policy overrides:"])
        for override in policy_overrides[:8]:
            if not isinstance(override, dict):
                continue
            reason = str(override.get("reason") or "").strip()
            targets = (
                override.get("production_targets")
                if isinstance(override.get("production_targets"), list)
                else []
            )
            target_label = (
                f" / targets: {', '.join(str(item) for item in targets[:5])}" if targets else ""
            )
            lines.append(
                f"- {override.get('source') or 'Policy override'}: {reason or 'No reason recorded.'}{target_label}"
            )
    verification = (
        handoff.get("verification") if isinstance(handoff.get("verification"), dict) else {}
    )
    if verification:
        lines.extend(
            [
                "",
                "Verification:",
                f"- {verification.get('status') or 'info'}: {verification.get('message') or 'No verification message.'}",
            ]
        )
        evidence = (
            verification.get("evidence") if isinstance(verification.get("evidence"), list) else []
        )
        lines.extend(f"- evidence: {item}" for item in evidence[:3])
        jobs = verification.get("jobs") if isinstance(verification.get("jobs"), list) else []
        for job in jobs[:3]:
            if isinstance(job, dict):
                lines.append(
                    f"- job: {job.get('job_id') or job.get('kind') or 'verification'} "
                    f"{job.get('status') or 'unknown'}"
                )
        if verification.get("override_reason"):
            lines.append(f"- override: {verification.get('override_reason')}")
    abort_criteria = (
        handoff.get("abort_criteria") if isinstance(handoff.get("abort_criteria"), dict) else {}
    )
    if abort_criteria:
        lines.extend(
            [
                "",
                "Rollback criteria:",
                f"- {abort_criteria.get('status') or 'info'}: {abort_criteria.get('message') or 'No rollback criteria message.'}",
            ]
        )
        criteria = (
            abort_criteria.get("criteria")
            if isinstance(abort_criteria.get("criteria"), list)
            else []
        )
        lines.extend(f"- {item}" for item in criteria[:3])
        if abort_criteria.get("override_reason"):
            lines.append(f"- override: {abort_criteria.get('override_reason')}")
    change_freeze = (
        handoff.get("change_freeze") if isinstance(handoff.get("change_freeze"), dict) else {}
    )
    if change_freeze:
        lines.extend(
            [
                "",
                "Change freeze:",
                f"- {change_freeze.get('status') or 'info'}: {change_freeze.get('message') or 'No change freeze message.'}",
            ]
        )
        if change_freeze.get("start") or change_freeze.get("end"):
            lines.append(
                f"- window: {change_freeze.get('start') or '?'} to {change_freeze.get('end') or '?'}"
            )
        targets = (
            change_freeze.get("production_targets")
            if isinstance(change_freeze.get("production_targets"), list)
            else []
        )
        if targets:
            lines.append(f"- targets: {', '.join(str(item) for item in targets[:5])}")
        if change_freeze.get("override_reason"):
            lines.append(f"- override: {change_freeze.get('override_reason')}")
    audit_summary_lines = release_run_report_audit_summary_lines(audit_events)
    if audit_summary_lines:
        lines.extend(["", "Audit summary:", *audit_summary_lines])
    steps = run.get("steps") if isinstance(run.get("steps"), list) else []
    if steps:
        lines.extend(["", "Steps:"])
        for step in steps[:12]:
            if not isinstance(step, dict):
                continue
            step_health = step.get("health") if isinstance(step.get("health"), dict) else {}
            details = step.get("details") if isinstance(step.get("details"), dict) else {}
            context = " / ".join(
                str(value)
                for value in [
                    details.get("environment"),
                    details.get("strategy"),
                    details.get("gate"),
                ]
                if value
            )
            health_label = (
                f", health {step_health.get('status')}" if step_health.get("status") else ""
            )
            suffix = f" ({context})" if context else ""
            lines.append(
                f"- Wave {step.get('wave') or '?'} {step.get('name') or step.get('application_id') or 'step'}: "
                f"{step.get('status') or 'unknown'}{health_label}{suffix}"
            )
    if audit_events:
        lines.extend(["", "Recent audit:"])
        for event in audit_events[:12]:
            created_suffix = f" ({event.get('created_at')})" if event.get("created_at") else ""
            lines.append(
                f"- {event.get('event_type') or 'event'}: {event.get('message') or 'recorded'}{created_suffix}"
            )
    return "\n".join(lines)


def release_run_report_audit_summary_lines(audit_events: list[dict[str, Any]]) -> list[str]:
    if not audit_events:
        return []
    counts: dict[str, int] = {}
    for event in audit_events:
        event_type = str(event.get("event_type") or "event")
        counts[event_type] = counts.get(event_type, 0) + 1
    latest = audit_events[0]
    latest_label = str(latest.get("event_type") or "event")
    latest_message = str(latest.get("message") or "recorded")
    lines = [
        f"- Events in report: {len(audit_events)}",
        f"- Latest: {latest_label} - {latest_message}",
    ]
    for event_type, count in sorted(counts.items(), key=lambda item: (-item[1], item[0]))[:6]:
        lines.append(f"- {event_type}: {count}")
    return lines


def release_run_report_approval_lines(run: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    steps = run.get("steps") if isinstance(run.get("steps"), list) else []
    for step in steps[:12]:
        if not isinstance(step, dict):
            continue
        details = step.get("details") if isinstance(step.get("details"), dict) else {}
        approval = details.get("approval") if isinstance(details.get("approval"), dict) else {}
        approval_id = str(step.get("approval_id") or approval.get("approval_id") or "").strip()
        if not approval_id:
            continue
        decision = (
            str(approval.get("decision") or approval.get("status") or "pending").strip()
            or "pending"
        )
        reason = str(approval.get("reason") or "").strip()
        gate = str(details.get("gate") or approval.get("gate") or "").strip()
        label = str(step.get("name") or step.get("application_id") or "step")
        suffix = " / ".join(
            item
            for item in [f"gate {gate}" if gate else "", f"reason {reason}" if reason else ""]
            if item
        )
        lines.append(f"- {label}: {approval_id} / {decision}{f' / {suffix}' if suffix else ''}")
    return lines


def release_run_report_target_lines(run: dict[str, Any]) -> list[str]:
    lines: list[str] = []
    steps = run.get("steps") if isinstance(run.get("steps"), list) else []
    for step in steps[:12]:
        if not isinstance(step, dict):
            continue
        details = step.get("details") if isinstance(step.get("details"), dict) else {}
        config = details.get("config") if isinstance(details.get("config"), dict) else {}
        dispatch = details.get("dispatch") if isinstance(details.get("dispatch"), dict) else {}
        workflow = step.get("workflow") if isinstance(step.get("workflow"), dict) else {}
        values = [
            str(step.get("application_id") or step.get("name") or "application"),
            f"cluster {details.get('cluster_id') or config.get('cluster_id') or dispatch.get('cluster_id')}"
            if details.get("cluster_id") or config.get("cluster_id") or dispatch.get("cluster_id")
            else "",
            f"namespace {details.get('namespace') or config.get('namespace') or dispatch.get('namespace')}"
            if details.get("namespace") or config.get("namespace") or dispatch.get("namespace")
            else "",
            f"workflow {step.get('workflow_run_id') or workflow.get('workflow_run_id') or dispatch.get('workflow_run_id')}"
            if step.get("workflow_run_id")
            or workflow.get("workflow_run_id")
            or dispatch.get("workflow_run_id")
            else "",
            f"repo {config.get('repo_ref') or dispatch.get('repo_ref')}"
            if config.get("repo_ref") or dispatch.get("repo_ref")
            else "",
            f"commit {config.get('commit_sha') or dispatch.get('commit_sha')}"
            if config.get("commit_sha") or dispatch.get("commit_sha")
            else "",
            f"manifest {config.get('manifest_path') or dispatch.get('manifest_path')}"
            if config.get("manifest_path") or dispatch.get("manifest_path")
            else "",
        ]
        lines.append("- " + " / ".join(value for value in values if value))
    return lines


def safe_release_report_filename(value: str) -> str:
    safe = "".join(ch if ch.isalnum() or ch in {"-", "_"} else "-" for ch in value.strip())
    return safe[:120] or "report"


def release_run_handoff_verification(run: dict[str, Any]) -> dict[str, Any]:
    guard = release_run_latest_guard(run)
    verification = guard.get("verification") if isinstance(guard.get("verification"), dict) else {}
    verification_jobs = (
        guard.get("verification_jobs") if isinstance(guard.get("verification_jobs"), dict) else {}
    )
    readiness = guard.get("readiness") if isinstance(guard.get("readiness"), dict) else {}
    impact = readiness.get("impact") if isinstance(readiness.get("impact"), dict) else {}
    health_paths = [
        str(item) for item in verification.get("health_check_paths", []) if str(item).strip()
    ]
    verification_urls = [
        str(item) for item in verification.get("verification_urls", []) if str(item).strip()
    ]
    production_targets = [
        str(item) for item in verification.get("production_targets", []) if str(item).strip()
    ]
    jobs = [dict(item) for item in verification_jobs.get("jobs", []) if isinstance(item, dict)]
    failed_jobs = [
        item
        for item in jobs
        if release_verification_job_status(item) in VERIFICATION_JOB_FAILED_STATUSES
    ]
    timed_out_jobs = release_verification_job_pending_timeouts(run)
    pending_jobs = [
        item
        for item in jobs
        if release_verification_job_status(item) in VERIFICATION_JOB_PENDING_STATUSES
    ]
    if failed_jobs:
        return {
            "status": "blocked",
            "message": f"{len(failed_jobs)} post-deploy verification job failed.",
            "evidence": [],
            "jobs": jobs,
            "job_count": len(jobs),
            "timed_out_jobs": timed_out_jobs,
            "override_reason": None,
            "production_targets": production_targets
            or (
                impact.get("production_targets")
                if isinstance(impact.get("production_targets"), list)
                else []
            ),
        }
    if timed_out_jobs:
        return {
            "status": "blocked",
            "message": f"{len(timed_out_jobs)} post-deploy verification job timed out.",
            "evidence": [],
            "jobs": jobs,
            "job_count": len(jobs),
            "timed_out_jobs": timed_out_jobs,
            "override_reason": None,
            "production_targets": production_targets
            or (
                impact.get("production_targets")
                if isinstance(impact.get("production_targets"), list)
                else []
            ),
        }
    if pending_jobs:
        return {
            "status": "warning",
            "message": f"{len(pending_jobs)} post-deploy verification job is still pending.",
            "evidence": [],
            "jobs": jobs,
            "job_count": len(jobs),
            "timed_out_jobs": [],
            "override_reason": None,
            "production_targets": production_targets
            or (
                impact.get("production_targets")
                if isinstance(impact.get("production_targets"), list)
                else []
            ),
        }
    if not verification:
        return {
            "status": "info",
            "message": "No verification snapshot recorded.",
            "evidence": [],
            "jobs": jobs,
            "job_count": len(jobs),
            "timed_out_jobs": [],
            "override_reason": None,
            "production_targets": impact.get("production_targets")
            if isinstance(impact.get("production_targets"), list)
            else [],
        }
    evidence = health_paths + verification_urls
    override_reason = str(verification.get("override_reason") or "").strip() or None
    if evidence:
        return {
            "status": "passed",
            "message": f"Post-deploy verification evidence is present ({', '.join(evidence[:2])}).",
            "evidence": evidence,
            "jobs": jobs,
            "job_count": len(jobs),
            "timed_out_jobs": [],
            "override_reason": override_reason,
            "production_targets": production_targets,
        }
    if override_reason:
        return {
            "status": "warning",
            "message": "Post-deploy verification was bypassed with an operator reason.",
            "evidence": [],
            "jobs": jobs,
            "job_count": len(jobs),
            "timed_out_jobs": [],
            "override_reason": override_reason,
            "production_targets": production_targets,
        }
    return {
        "status": "blocked",
        "message": "Post-deploy verification evidence is missing.",
        "evidence": [],
        "jobs": jobs,
        "job_count": len(jobs),
        "timed_out_jobs": [],
        "override_reason": None,
        "production_targets": production_targets,
    }


def release_run_handoff_abort_criteria(run: dict[str, Any]) -> dict[str, Any]:
    guard = release_run_latest_guard(run)
    criteria_snapshot = (
        guard.get("abort_criteria") if isinstance(guard.get("abort_criteria"), dict) else {}
    )
    readiness = guard.get("readiness") if isinstance(guard.get("readiness"), dict) else {}
    impact = readiness.get("impact") if isinstance(readiness.get("impact"), dict) else {}
    criteria = [str(item) for item in criteria_snapshot.get("criteria", []) if str(item).strip()]
    production_targets = [
        str(item) for item in criteria_snapshot.get("production_targets", []) if str(item).strip()
    ]
    if not criteria_snapshot:
        return {
            "status": "info",
            "message": "No rollback criteria snapshot recorded.",
            "criteria": [],
            "override_reason": None,
            "production_targets": impact.get("production_targets")
            if isinstance(impact.get("production_targets"), list)
            else [],
        }
    override_reason = str(criteria_snapshot.get("override_reason") or "").strip() or None
    if criteria:
        return {
            "status": "passed",
            "message": f"Rollback criteria are present ({', '.join(criteria[:2])}).",
            "criteria": criteria,
            "override_reason": override_reason,
            "production_targets": production_targets,
        }
    if override_reason:
        return {
            "status": "warning",
            "message": "Rollback criteria were bypassed with an operator reason.",
            "criteria": [],
            "override_reason": override_reason,
            "production_targets": production_targets,
        }
    return {
        "status": "blocked",
        "message": "Rollback criteria are missing.",
        "criteria": [],
        "override_reason": None,
        "production_targets": production_targets,
    }


def release_run_handoff_change_freeze(run: dict[str, Any]) -> dict[str, Any]:
    guard = release_run_latest_guard(run)
    freeze = guard.get("change_freeze") if isinstance(guard.get("change_freeze"), dict) else {}
    readiness = guard.get("readiness") if isinstance(guard.get("readiness"), dict) else {}
    impact = readiness.get("impact") if isinstance(readiness.get("impact"), dict) else {}
    production_targets = [
        str(item) for item in freeze.get("production_targets", []) if str(item).strip()
    ]
    fallback_targets = (
        impact.get("production_targets")
        if isinstance(impact.get("production_targets"), list)
        else []
    )
    start = str(freeze.get("start") or "").strip() or None
    end = str(freeze.get("end") or "").strip() or None
    override_reason = str(freeze.get("override_reason") or "").strip() or None
    active = freeze.get("active") is True
    if not freeze:
        return {
            "status": "info",
            "message": "No change freeze snapshot recorded.",
            "active": False,
            "start": None,
            "end": None,
            "override_reason": None,
            "production_targets": fallback_targets,
        }
    if active and override_reason:
        status = "warning"
        message = "Active change freeze was bypassed with an operator reason."
    elif active:
        status = "blocked"
        message = "Release is inside an active change freeze window."
    elif start or end:
        status = "passed"
        message = "Change freeze window is not active for this run."
    else:
        status = "info"
        message = "No change freeze window was configured."
    return {
        "status": status,
        "message": message,
        "active": active,
        "start": start,
        "end": end,
        "override_reason": override_reason,
        "production_targets": production_targets or fallback_targets,
    }


def release_run_latest_guard(run: dict[str, Any]) -> dict[str, Any]:
    steps = run.get("steps") if isinstance(run.get("steps"), list) else []
    for step in reversed(steps):
        if not isinstance(step, dict):
            continue
        details = step.get("details") if isinstance(step.get("details"), dict) else {}
        guard = details.get("release_guard")
        if isinstance(guard, dict):
            return guard
    return {}


def release_run_handoff_actions(
    status: str,
    *,
    terminal: bool,
    retryable: bool,
    alertable: bool,
    rollback_enabled: bool,
    notify_blocker: str | None = None,
    rollback_blocker: str | None = None,
) -> list[dict[str, Any]]:
    actions: list[dict[str, Any]] = []
    if terminal:
        return [{"action": "review_audit", "label": "Review audit trail", "enabled": True}]
    if status == "paused":
        actions.append(
            {"action": "resume", "label": "Resume when the blocker is cleared", "enabled": True}
        )
    elif status == "waiting_for_approval":
        actions.append(
            {"action": "approval", "label": "Review the pending approval", "enabled": True}
        )
    elif retryable:
        actions.append(
            {"action": "retry", "label": "Retry the failed or unhealthy wave", "enabled": True}
        )
    else:
        actions.append(
            {"action": "monitor", "label": "Monitor current wave health", "enabled": True}
        )
    if alertable:
        actions.append(
            {
                "action": "notify",
                "label": "Notify the release owner",
                "enabled": notify_blocker is None,
                **({"reason": notify_blocker} if notify_blocker else {}),
            }
        )
    actions.append(
        {
            "action": "rollback",
            "label": "Request rollback if user impact is confirmed",
            "enabled": rollback_enabled,
            **({"reason": rollback_blocker} if rollback_blocker and not rollback_enabled else {}),
        }
    )
    actions.append({"action": "cancel", "label": "Cancel if the run should stop", "enabled": True})
    return actions


def release_handoff_check(name: str, status: str, message: str) -> dict[str, str]:
    return {"name": name, "status": status, "message": message}


def release_run_handoff_headline(
    run: dict[str, Any],
    status: str,
    *,
    alertable: bool,
    stale: bool,
) -> str:
    plan_name = str(run.get("plan_name") or "Release run")
    if stale:
        return f"{plan_name} is stale and needs operator follow-up."
    if alertable:
        return f"{plan_name} needs operator attention."
    return f"{plan_name} is {status}."


def release_run_handoff_severity(status: str, *, alertable: bool, stale: bool) -> str:
    if status in {"failed", "rollback_requested"} or stale:
        return "danger"
    if alertable or status in {"paused", "waiting_for_approval"}:
        return "warning"
    return "info"


def release_run_is_retryable(run: dict[str, Any]) -> bool:
    status = str(run.get("derived_status") or run.get("status") or "")
    if status == "failed":
        return True
    steps = run.get("steps") if isinstance(run.get("steps"), list) else []
    for step in steps:
        if not isinstance(step, dict):
            continue
        health = step.get("health") if isinstance(step.get("health"), dict) else {}
        if step.get("status") == "failed" or health.get("status") == "unhealthy":
            return True
    return False


def release_run_last_event(run: dict[str, Any]) -> dict[str, Any] | None:
    events = run.get("events") if isinstance(run.get("events"), list) else []
    for event in reversed(events):
        if isinstance(event, dict):
            return {
                "event_type": str(event.get("event_type") or ""),
                "message": str(event.get("message") or ""),
                "created_at": event.get("created_at"),
            }
    return None


def release_attention_reasons(run: dict[str, Any]) -> list[str]:
    attention = run.get("attention") if isinstance(run.get("attention"), dict) else {}
    reasons = attention.get("reasons") if isinstance(attention, dict) else []
    if isinstance(reasons, list):
        return [str(reason) for reason in reasons if str(reason).strip()]
    return []


def public_release_audit_event(event: dict[str, Any]) -> dict[str, Any]:
    public = {key: value for key, value in event.items() if key != "_steps"}
    public["details"] = redact_release_value(public.get("details") or {})
    return public


def release_audit_csv(events: Any) -> str:
    output = io.StringIO()
    fields = [
        "audit_id",
        "created_at",
        "plan_id",
        "plan_name",
        "run_id",
        "run_status",
        "event_type",
        "actor",
        "message",
        "application_ids",
        "details_json",
    ]
    writer = csv.DictWriter(output, fieldnames=fields)
    writer.writeheader()
    for event in events:
        writer.writerow(
            {
                "created_at": event.get("created_at") or "",
                "audit_id": event.get("audit_id") or "",
                "plan_id": event.get("plan_id") or "",
                "plan_name": event.get("plan_name") or "",
                "run_id": event.get("run_id") or "",
                "run_status": event.get("run_status") or "",
                "event_type": event.get("event_type") or "",
                "actor": event.get("actor") or "",
                "message": event.get("message") or "",
                "application_ids": ",".join(event.get("application_ids") or []),
                "details_json": json.dumps(event.get("details") or {}, sort_keys=True),
            }
        )
    return output.getvalue()
