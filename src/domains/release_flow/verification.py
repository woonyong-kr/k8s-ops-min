"""Post-deploy verification rules and deterministic job projections."""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime
from typing import Any

from domains.release_flow._support import (
    int_field,
    live_https_url_is_valid,
    parse_release_window_time,
    plan_settings_value,
    release_window_bound_label,
    step_config,
)

DEFAULT_RELEASE_VERIFICATION_TIMEOUT_MINUTES = 15
VERIFICATION_JOB_FAILED_STATUSES = {"failed", "error", "timeout", "unhealthy"}
VERIFICATION_JOB_PENDING_STATUSES = {"", "pending", "queued", "running"}


def release_verification_evidence_present(settings: dict[str, Any], config: dict[str, Any]) -> bool:
    verification_url = release_verification_url(settings, config)
    return release_verification_url_is_valid(verification_url)


def release_verification_url(settings: dict[str, Any], config: dict[str, Any]) -> str:
    return str(
        config.get("post_deploy_verification_url")
        or config.get("verification_url")
        or settings.get("post_deploy_verification_url")
        or settings.get("verification_url")
        or ""
    ).strip()


def release_verification_url_is_valid(value: str) -> bool:
    return release_live_evidence_url_is_valid(value)


def release_live_evidence_url_is_valid(value: str) -> bool:
    return live_https_url_is_valid(value)


def release_verification_override_reason(plan: dict[str, Any]) -> str:
    settings = plan_settings_value(plan)
    return str(
        settings.get("verification_override_reason")
        or settings.get("post_deploy_verification_override_reason")
        or ""
    ).strip()


def release_verification_job_specs(
    plan: dict[str, Any],
    production_steps: list[dict[str, Any]],
    wave: int,
) -> list[dict[str, Any]]:
    settings = plan_settings_value(plan)
    queued_at = release_window_bound_label(datetime.now(UTC))
    jobs: list[dict[str, Any]] = []
    for step in production_steps:
        config = step_config(step)
        application_id = str(step.get("application_id") or "").strip()
        name = str(step.get("name") or application_id or "release step")
        health_path = release_health_check_path(settings, config)
        verification_url = release_verification_url(settings, config)
        timeout_minutes = release_verification_timeout_minutes(settings, config)
        if health_path:
            jobs.append(
                {
                    "job_id": release_verification_job_id(
                        plan, wave, application_id, "health", health_path
                    ),
                    "application_id": application_id,
                    "name": name,
                    "kind": "kubernetes_health_check",
                    "status": "pending",
                    "queued_at": queued_at,
                    "timeout_minutes": timeout_minutes,
                    "evidence_key": release_verification_evidence_key(plan, wave, application_id),
                    "target": {
                        "cluster_id": str(
                            config.get("cluster_id") or settings.get("cluster_id") or ""
                        ),
                        "namespace": str(
                            config.get("namespace") or settings.get("namespace") or ""
                        ),
                        "service_name": str(
                            config.get("service_name") or config.get("service") or application_id
                        ),
                        "path": health_path,
                    },
                }
            )
        if verification_url:
            jobs.append(
                {
                    "job_id": release_verification_job_id(
                        plan, wave, application_id, "http", verification_url
                    ),
                    "application_id": application_id,
                    "name": name,
                    "kind": "http_probe",
                    "status": "pending",
                    "queued_at": queued_at,
                    "timeout_minutes": timeout_minutes,
                    "evidence_key": release_verification_evidence_key(plan, wave, application_id),
                    "target": {"url": verification_url},
                }
            )
    return jobs


def release_verification_timeout_minutes(settings: dict[str, Any], config: dict[str, Any]) -> int:
    default_timeout = int_field(
        settings,
        "post_deploy_verification_timeout_minutes",
        int_field(
            settings, "verification_timeout_minutes", DEFAULT_RELEASE_VERIFICATION_TIMEOUT_MINUTES
        ),
    )
    step_timeout = int_field(config, "verification_timeout_minutes", default_timeout)
    return max(1, int_field(config, "post_deploy_verification_timeout_minutes", step_timeout))


def release_health_check_path(settings: dict[str, Any], config: dict[str, Any]) -> str:
    value = str(config.get("health_check_path") or settings.get("health_check_path") or "").strip()
    return value if value.startswith("/") else ""


def release_verification_job_id(
    plan: dict[str, Any],
    wave: int,
    application_id: str,
    kind: str,
    target: str,
) -> str:
    raw = ":".join(
        [
            str(plan.get("plan_id") or plan.get("name") or "release-plan"),
            str(wave),
            application_id,
            kind,
            target,
        ]
    )
    return f"release-verification-{hashlib.sha256(raw.encode()).hexdigest()[:20]}"


def release_verification_evidence_key(plan: dict[str, Any], wave: int, application_id: str) -> str:
    return ":".join(
        [
            str(plan.get("plan_id") or plan.get("name") or "release-plan"),
            f"wave-{wave}",
            application_id,
            "post-deploy-verification",
        ]
    )


def release_verification_timeout_alert_summary(jobs: list[dict[str, Any]]) -> str:
    parts: list[str] = []
    for job in jobs[:3]:
        kind = str(job.get("kind") or "verification")
        job_id = str(job.get("job_id") or "unknown-job")
        age = int_field(job, "age_minutes", 0)
        timeout = int_field(job, "timeout_minutes", DEFAULT_RELEASE_VERIFICATION_TIMEOUT_MINUTES)
        parts.append(f"{kind} {job_id} timed out after {age}m (limit {timeout}m)")
    if len(jobs) > 3:
        parts.append(f"+{len(jobs) - 3} more timed out verification jobs")
    return "; ".join(parts)


def release_current_wave_health_blockers(steps: list[dict[str, Any]], wave: int) -> list[str]:
    blockers: list[str] = []
    for step in steps:
        name = str(step.get("name") or step.get("application_id") or "release step")
        if str(step.get("status") or "") != "succeeded":
            blockers.append(f"{name} in wave {wave} has not succeeded yet.")
            continue
        health = step.get("health") if isinstance(step.get("health"), dict) else {}
        if str(health.get("status") or "") == "unhealthy":
            blockers.append(f"{name} health is unhealthy; resolve it before advancing wave {wave}.")
        blockers.extend(release_verification_job_advance_blockers(step, name, wave))
    return blockers


def release_verification_job_advance_blockers(
    step: dict[str, Any],
    name: str,
    wave: int,
) -> list[str]:
    details = step.get("details") if isinstance(step.get("details"), dict) else {}
    guard = details.get("release_guard") if isinstance(details.get("release_guard"), dict) else {}
    verification_jobs = (
        guard.get("verification_jobs") if isinstance(guard.get("verification_jobs"), dict) else {}
    )
    jobs = [job for job in list(verification_jobs.get("jobs") or []) if isinstance(job, dict)]
    blockers: list[str] = []
    for job in jobs:
        status = str(job.get("status") or "").lower()
        if status in VERIFICATION_JOB_FAILED_STATUSES:
            kind = str(job.get("kind") or "verification")
            blockers.append(
                f"{name} post-deploy verification {kind} failed; resolve it before advancing wave {wave}."
            )
        elif status in {"", "pending", "queued", "running"}:
            kind = str(job.get("kind") or "verification")
            blockers.append(
                f"{name} post-deploy verification {kind} is {status or 'pending'}; wait before advancing wave {wave}."
            )
    return blockers


def release_run_has_failed_verification(run: dict[str, Any]) -> bool:
    for job, _step, _name in release_run_verification_jobs(run):
        if release_verification_job_status(job) in VERIFICATION_JOB_FAILED_STATUSES:
            return True
    return False


def release_run_has_timed_out_verification(run: dict[str, Any]) -> bool:
    return bool(release_verification_job_pending_timeouts(run))


def release_run_verification_jobs(
    run: dict[str, Any],
) -> list[tuple[dict[str, Any], dict[str, Any], str]]:
    steps = run.get("steps") if isinstance(run.get("steps"), list) else []
    records: list[tuple[dict[str, Any], dict[str, Any], str]] = []
    for step in steps:
        if not isinstance(step, dict):
            continue
        name = str(step.get("name") or step.get("application_id") or "release step")
        details = step.get("details") if isinstance(step.get("details"), dict) else {}
        guard = (
            details.get("release_guard") if isinstance(details.get("release_guard"), dict) else {}
        )
        verification_jobs = (
            guard.get("verification_jobs")
            if isinstance(guard.get("verification_jobs"), dict)
            else {}
        )
        jobs = (
            verification_jobs.get("jobs") if isinstance(verification_jobs.get("jobs"), list) else []
        )
        for job in jobs:
            if isinstance(job, dict):
                records.append((job, step, name))
    return records


def release_verification_job_status(job: dict[str, Any]) -> str:
    return str(job.get("status") or "").strip().lower()


def release_verification_job_pending_timeouts(
    run: dict[str, Any],
    *,
    now: datetime | None = None,
) -> list[dict[str, Any]]:
    current_time = now or datetime.now(UTC)
    settings = run.get("settings") if isinstance(run.get("settings"), dict) else {}
    default_timeout = int_field(
        settings,
        "post_deploy_verification_timeout_minutes",
        int_field(
            settings, "verification_timeout_minutes", DEFAULT_RELEASE_VERIFICATION_TIMEOUT_MINUTES
        ),
    )
    timed_out: list[dict[str, Any]] = []
    for job, step, step_name in release_run_verification_jobs(run):
        if release_verification_job_status(job) not in VERIFICATION_JOB_PENDING_STATUSES:
            continue
        timeout_minutes = max(1, int_field(job, "timeout_minutes", default_timeout))
        queued_at = parse_release_window_time(
            job.get("queued_at")
            or job.get("created_at")
            or job.get("started_at")
            or step.get("updated_at")
            or run.get("updated_at")
            or run.get("created_at")
        )
        if queued_at is None:
            continue
        age_seconds = max(0, int((current_time - queued_at.astimezone(UTC)).total_seconds()))
        if age_seconds < timeout_minutes * 60:
            continue
        record = dict(job)
        record["step_name"] = step_name
        record["age_minutes"] = age_seconds // 60
        record["timeout_minutes"] = timeout_minutes
        record["queued_at"] = release_window_bound_label(queued_at)
        timed_out.append(record)
    return timed_out
