"""Release policy, Safe PR evidence, and rollback projections."""

from __future__ import annotations

import json
import os
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import unquote, urlparse

from domains.diagnostics.router import release_plan_diagnostics
from domains.gitops.repository import (
    derive_deployment_binding_id,
    derive_repository_id,
    derive_workflow_run_id,
)
from domains.release_flow._support import (
    first_environment,
    int_field,
    live_https_url_is_valid,
    parse_release_window_time,
    plan_settings_value,
    release_step_index,
    release_step_index_in_steps,
    release_window_bound_label,
    step_config,
    unique_non_empty,
)
from domains.release_flow.execution import (
    approval_granted,
    execution_profile,
    has_change_ticket,
    placeholder_change_ticket,
)
from domains.release_flow.execution import (
    release_execution_blockers as base_release_execution_blockers,
)
from domains.release_flow.manifest import render_release_step_manifest
from domains.scm.events import SafePrFilePatch, SafePrRequestedBody
from domains.scm.pipeline import safe_pr_patch_sha256
from packages.config.constants import GitHub, Sandbox, Target
from packages.config.environments import is_production_environment
from packages.contracts.identity import DEFAULT_WORKSPACE_ID

APPROVAL_MAX_AGE_HOURS_ENV = "RELEASE_FLOW_APPROVAL_MAX_AGE_HOURS"
DEFAULT_APPROVAL_MAX_AGE_HOURS = 24
SAFE_PR_EVIDENCE_MAX_AGE_HOURS_ENV = "RELEASE_FLOW_SAFE_PR_EVIDENCE_MAX_AGE_HOURS"
DEFAULT_SAFE_PR_EVIDENCE_MAX_AGE_HOURS = 24
APPROVAL_CLOCK_SKEW_MINUTES = 5
SAFE_PR_EVIDENCE_LOOKUP_LIMIT = 20


def release_execution_blockers(
    plan: dict[str, Any],
    preview: dict[str, Any],
    wave: int,
    *,
    workspace_id: str,
    db: Any = None,
) -> list[str]:
    evidenced_plan = plan_with_safe_pr_evidence(
        plan, preview, wave, workspace_id=workspace_id, db=db
    )
    return base_release_execution_blockers(evidenced_plan, preview, wave, workspace_id=workspace_id)


def plan_with_safe_pr_evidence(
    plan: dict[str, Any],
    preview: dict[str, Any],
    wave: int,
    *,
    workspace_id: str,
    db: Any = None,
) -> dict[str, Any]:
    if db is None:
        return plan
    settings = plan_settings_value(plan)
    preview_steps = {
        str(step.get("application_id") or ""): step
        for step in preview.get("steps", [])
        if isinstance(step, dict)
    }
    changed = False
    sanitized_settings = dict(settings)
    safe_pr_ready_fields = {"safe_pr_ready", "safe_pr_url", "safe_pr_evidence"}
    wave_requires_safe_pr = any(
        isinstance(raw_step, dict)
        and int_field(preview_steps.get(str(raw_step.get("application_id") or ""), {}), "wave", -1)
        == wave
        and safe_pr_gate_required(settings, raw_step)
        for raw_step in plan.get("steps", [])
    )
    if wave_requires_safe_pr:
        for field in safe_pr_ready_fields:
            if field in sanitized_settings:
                sanitized_settings.pop(field, None)
                changed = True
    steps: list[dict[str, Any]] = []
    for raw_step in plan.get("steps", []):
        if not isinstance(raw_step, dict):
            continue
        step = raw_step
        application_id = str(step.get("application_id") or "")
        preview_step = preview_steps.get(application_id, {})
        if int_field(preview_step, "wave", -1) == wave and safe_pr_gate_required(settings, step):
            config = {
                key: value
                for key, value in step_config(step).items()
                if key not in safe_pr_ready_fields
            }
            evidence = safe_pr_created_evidence_for_step(db, workspace_id, plan, step)
            pr_url = str(evidence.get("pr_url") or "") if evidence else ""
            if pr_url:
                config = {
                    **config,
                    "safe_pr_ready": True,
                    "safe_pr_url": pr_url,
                    "safe_pr_evidence": evidence,
                }
            if config != step_config(step):
                step = {**step, "config": config}
                changed = True
        steps.append(step)
    if changed:
        return {**plan, "settings": sanitized_settings, "steps": steps}
    return plan


def safe_pr_gate_required(settings: dict[str, Any], step: dict[str, Any]) -> bool:
    config = step_config(step)
    gate = str(config.get("approval_gate") or "inherit")
    policy = str(settings.get("approval_policy") or "auto_safe")
    return (gate if gate != "inherit" else policy) == "safe_pr"


def safe_pr_created_evidence_for_step(
    db: Any,
    workspace_id: str,
    plan: dict[str, Any],
    step: dict[str, Any],
) -> dict[str, Any] | None:
    application_id = str(step.get("application_id") or "") or None
    expected = safe_pr_expected_evidence(plan, step, workspace_id, db)
    for workflow_run_id in safe_pr_workflow_run_ids(plan, step, workspace_id, db):
        for evidence in safe_pr_evidence_candidates(
            db, workspace_id, workflow_run_id, application_id=application_id
        ):
            if safe_pr_evidence_matches(evidence, expected):
                return dict(evidence)
    return None


def release_safe_pr_evidence_blockers(
    plan: dict[str, Any],
    preview: dict[str, Any],
    wave: int,
    *,
    workspace_id: str,
    db: Any,
) -> list[str]:
    if db is None or not execution_profile(plan).side_effects:
        return []
    settings = plan_settings_value(plan)
    preview_steps = {
        str(step.get("application_id") or ""): step
        for step in preview.get("steps", [])
        if isinstance(step, dict)
    }
    blockers: list[str] = []
    for step in plan.get("steps", []):
        if not isinstance(step, dict) or not safe_pr_gate_required(settings, step):
            continue
        application_id = str(step.get("application_id") or "")
        preview_step = preview_steps.get(application_id, {})
        if int_field(preview_step, "wave", -1) != wave:
            continue
        expected = safe_pr_expected_evidence(plan, step, workspace_id, db)
        workflow_run_ids = safe_pr_workflow_run_ids(plan, step, workspace_id, db)
        if not workflow_run_ids:
            blockers.append(
                f"Application {application_id} requires Safe PR evidence, but no workflow_run_id was derived."
            )
            continue
        candidates: list[dict[str, Any]] = []
        for workflow_run_id in workflow_run_ids:
            candidates.extend(
                safe_pr_evidence_candidates(
                    db, workspace_id, workflow_run_id, application_id=application_id
                )
            )
        if any(safe_pr_evidence_matches(candidate, expected) for candidate in candidates):
            continue
        workflow_label = ", ".join(workflow_run_ids)
        if not candidates:
            blockers.append(
                f"Application {application_id} requires Safe PR evidence for workflow_run_id {workflow_label}, "
                "but no safe_pr.created event was found."
            )
            continue
        reasons = safe_pr_evidence_mismatch_reasons(candidates[0], expected)
        reason_text = (
            "; ".join(reasons[:4])
            if reasons
            else "candidate evidence did not match server expectations"
        )
        blockers.append(
            f"Application {application_id} found {len(candidates)} Safe PR candidate(s) for workflow_run_id "
            f"{workflow_label}, but none matched: {reason_text}."
        )
    return blockers


def safe_pr_evidence_candidates(
    db: Any,
    workspace_id: str,
    workflow_run_id: str,
    *,
    application_id: str | None,
) -> list[dict[str, Any]]:
    lister = getattr(db, "list_release_safe_pr_evidence", None)
    if callable(lister):
        candidates = lister(
            workspace_id,
            workflow_run_id,
            application_id=application_id,
            limit=SAFE_PR_EVIDENCE_LOOKUP_LIMIT,
        )
        if isinstance(candidates, Iterable) and not isinstance(candidates, (bytes, str, dict)):
            return [dict(candidate) for candidate in candidates if isinstance(candidate, dict)]
    finder = getattr(db, "find_release_safe_pr_evidence", None)
    if callable(finder):
        evidence = finder(workspace_id, workflow_run_id, application_id=application_id)
        if isinstance(evidence, dict):
            return [dict(evidence)]
    return []


def safe_pr_workflow_run_id(
    plan: dict[str, Any],
    step: dict[str, Any],
    workspace_id: str,
    db: Any,
) -> str:
    ids = safe_pr_workflow_run_ids(plan, step, workspace_id, db)
    return ids[0] if ids else ""


def safe_pr_workflow_run_ids(
    plan: dict[str, Any],
    step: dict[str, Any],
    workspace_id: str,
    db: Any,
) -> list[str]:
    config = step_config(step)
    settings = plan_settings_value(plan)
    explicit = str(
        config.get("safe_pr_workflow_run_id")
        or config.get("workflow_run_id")
        or settings.get("safe_pr_workflow_run_id")
        or ""
    ).strip()
    if explicit:
        return [explicit]
    application_id = str(step.get("application_id") or "")
    application = release_application_context(db, workspace_id, application_id)
    manifest_paths = [
        str(config.get("manifest_path") or application.get("manifest_path") or "").strip(),
        generated_safe_pr_manifest_path(plan, step, application),
    ]
    workflow_ids: list[str] = []
    for manifest_path in manifest_paths:
        if not manifest_path:
            continue
        workflow_id = derive_workflow_run_id(
            safe_pr_workflow_basis(
                plan,
                step,
                application,
                workspace_id,
                manifest_path=manifest_path,
            )
        )
        if workflow_id not in workflow_ids:
            workflow_ids.append(workflow_id)
    return workflow_ids


def safe_pr_expected_evidence(
    plan: dict[str, Any],
    step: dict[str, Any],
    workspace_id: str,
    db: Any,
) -> dict[str, Any]:
    config = step_config(step)
    settings = plan_settings_value(plan)
    application_id = str(step.get("application_id") or "")
    application = release_application_context(db, workspace_id, application_id)
    manifest_paths = [
        str(config.get("manifest_path") or application.get("manifest_path") or "").strip(),
        generated_safe_pr_manifest_path(plan, step, application),
    ]
    rollback_required = release_step_targets_production(plan, step)
    return {
        "provider": str(
            config.get("scm_provider") or settings.get("scm_provider") or GitHub.PROVIDER
        ).lower(),
        "pr_url": str(config.get("safe_pr_url") or settings.get("safe_pr_url") or "").strip(),
        "repo_ref": str(config.get("repo_ref") or application.get("repo_ref") or "").strip(),
        "base_branch": str(config.get("branch") or application.get("branch") or "main").strip(),
        "manifest_paths": [path for path in manifest_paths if path],
        "environment": str(
            config.get("environment") or first_environment(settings) or "sandbox"
        ).strip(),
        "commit_sha": str(config.get("commit_sha") or settings.get("commit_sha") or "").strip(),
        "patch_sha256": generated_safe_pr_patch_sha256(
            plan, step, application, workspace_id=workspace_id
        ),
        "rollback_required": rollback_required,
        "rollback_available": (not rollback_required)
        or generated_safe_pr_rollback_patch_available(
            plan, step, application, workspace_id=workspace_id
        ),
    }


def safe_pr_evidence_matches(evidence: dict[str, Any], expected: dict[str, Any]) -> bool:
    if not str(evidence.get("pr_url") or "").strip():
        return False
    if not safe_pr_evidence_is_current(evidence):
        return False
    if not safe_pr_required_evidence_field_matches(evidence, expected, "provider"):
        return False
    if not safe_pr_evidence_field_matches(evidence, expected, "pr_url"):
        return False
    if not safe_pr_required_evidence_field_matches(evidence, expected, "repo_ref"):
        return False
    if not safe_pr_evidence_pr_url_matches_provider(evidence, expected):
        return False
    if not safe_pr_required_evidence_field_matches(evidence, expected, "base_branch"):
        return False
    if not safe_pr_required_evidence_field_matches(evidence, expected, "commit_sha"):
        return False
    if not safe_pr_required_evidence_field_matches(evidence, expected, "patch_sha256"):
        return False
    if bool(expected.get("rollback_required")) and not bool(expected.get("rollback_available")):
        return False
    evidence_manifest_path = str(evidence.get("manifest_path") or "").strip()
    expected_manifest_paths = {
        str(path).strip() for path in expected.get("manifest_paths", []) if str(path).strip()
    }
    if not expected_manifest_paths or evidence_manifest_path not in expected_manifest_paths:
        return False
    evidence_environment = str(evidence.get("environment") or "").strip()
    expected_environment = str(expected.get("environment") or "").strip()
    return bool(
        evidence_environment
        and expected_environment
        and evidence_environment == expected_environment
    )


def safe_pr_evidence_mismatch_reasons(
    evidence: dict[str, Any], expected: dict[str, Any]
) -> list[str]:
    reasons: list[str] = []
    if not str(evidence.get("pr_url") or "").strip():
        reasons.append("pr_url is missing")
    if not safe_pr_evidence_is_current(evidence):
        reasons.append("created_at is missing, stale, or in the future")
    for field in ("provider", "repo_ref", "base_branch", "commit_sha", "patch_sha256"):
        if not safe_pr_required_evidence_field_matches(evidence, expected, field):
            reasons.append(safe_pr_evidence_field_reason(evidence, expected, field))
    if not safe_pr_evidence_field_matches(evidence, expected, "pr_url"):
        reasons.append(safe_pr_evidence_field_reason(evidence, expected, "pr_url"))
    if not safe_pr_evidence_pr_url_matches_provider(evidence, expected):
        reasons.append("pr_url path does not match the expected GitHub repo_ref")
    evidence_manifest_path = str(evidence.get("manifest_path") or "").strip()
    expected_manifest_paths = {
        str(path).strip() for path in expected.get("manifest_paths", []) if str(path).strip()
    }
    if not expected_manifest_paths:
        reasons.append("expected manifest_path could not be derived")
    elif evidence_manifest_path not in expected_manifest_paths:
        reasons.append(
            "manifest_path expected one of "
            f"{safe_pr_short_values(sorted(expected_manifest_paths))} but got {safe_pr_short_value(evidence_manifest_path)}"
        )
    evidence_environment = str(evidence.get("environment") or "").strip()
    expected_environment = str(expected.get("environment") or "").strip()
    if (
        not evidence_environment
        or not expected_environment
        or evidence_environment != expected_environment
    ):
        reasons.append(
            f"environment expected {safe_pr_short_value(expected_environment)} but got "
            f"{safe_pr_short_value(evidence_environment)}"
        )
    return unique_non_empty(reasons)


def safe_pr_evidence_field_reason(
    evidence: dict[str, Any], expected: dict[str, Any], field: str
) -> str:
    return (
        f"{field} expected {safe_pr_short_value(str(expected.get(field) or '').strip())} "
        f"but got {safe_pr_short_value(str(evidence.get(field) or '').strip())}"
    )


def safe_pr_short_values(values: list[str]) -> str:
    return ", ".join(safe_pr_short_value(value) for value in values[:3])


def safe_pr_short_value(value: str) -> str:
    if not value:
        return "<missing>"
    if len(value) <= 32:
        return value
    return f"{value[:16]}...{value[-8:]}"


def safe_pr_evidence_is_current(evidence: dict[str, Any]) -> bool:
    created_at = parse_release_window_time(evidence.get("created_at"))
    if created_at is None:
        return False
    now = datetime.now(UTC)
    if created_at.astimezone(UTC) > now + timedelta(minutes=APPROVAL_CLOCK_SKEW_MINUTES):
        return False
    return now - created_at.astimezone(UTC) <= safe_pr_evidence_max_age()


def safe_pr_evidence_max_age() -> timedelta:
    try:
        hours = float(
            os.getenv(
                SAFE_PR_EVIDENCE_MAX_AGE_HOURS_ENV, str(DEFAULT_SAFE_PR_EVIDENCE_MAX_AGE_HOURS)
            )
        )
    except ValueError:
        hours = float(DEFAULT_SAFE_PR_EVIDENCE_MAX_AGE_HOURS)
    return timedelta(hours=max(1.0, hours))


def safe_pr_evidence_field_matches(
    evidence: dict[str, Any],
    expected: dict[str, Any],
    field: str,
) -> bool:
    expected_value = str(expected.get(field) or "").strip()
    evidence_value = str(evidence.get(field) or "").strip()
    return not expected_value or evidence_value == expected_value


def safe_pr_evidence_pr_url_matches_provider(
    evidence: dict[str, Any],
    expected: dict[str, Any],
) -> bool:
    provider = str(expected.get("provider") or "").strip().lower()
    if provider != GitHub.PROVIDER:
        return True
    repo_ref = str(expected.get("repo_ref") or "").strip()
    if "/" not in repo_ref:
        return False
    parsed = urlparse(str(evidence.get("pr_url") or "").strip())
    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False
    path_parts = [unquote(part).lower() for part in parsed.path.split("/") if part]
    owner, repo = [part.lower() for part in repo_ref.split("/", 1)]
    return (
        len(path_parts) >= 4
        and path_parts[0] == owner
        and path_parts[1] == repo
        and path_parts[2] == "pull"
        and path_parts[3].isdigit()
    )


def safe_pr_required_evidence_field_matches(
    evidence: dict[str, Any],
    expected: dict[str, Any],
    field: str,
) -> bool:
    expected_value = str(expected.get(field) or "").strip()
    evidence_value = str(evidence.get(field) or "").strip()
    return bool(expected_value and evidence_value and evidence_value == expected_value)


def generated_safe_pr_manifest_path(
    plan: dict[str, Any],
    step: dict[str, Any],
    application: dict[str, Any],
) -> str:
    step_index = release_step_index(plan, step)
    if step_index < 0:
        return ""
    rendered = render_release_step_manifest(plan, step_index, application)
    files = rendered.get("files")
    if not isinstance(files, list) or not files or not isinstance(files[0], dict):
        return ""
    return str(files[0].get("path") or "").strip()


def generated_safe_pr_patch_sha256(
    plan: dict[str, Any],
    step: dict[str, Any],
    application: dict[str, Any],
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> str:
    step_index = release_step_index(plan, step)
    if step_index < 0:
        return ""
    rendered = render_release_step_manifest(plan, step_index, application)
    patches = [
        SafePrFilePatch(
            path=str(file.get("path") or ""),
            content=str(file.get("content") or ""),
            description=str(file.get("description") or "Generated release manifest"),
        )
        for file in rendered.get("files", [])
        if isinstance(file, dict) and file.get("content")
    ]
    manifest_path = str(
        patches[0].path if patches else generated_safe_pr_manifest_path(plan, step, application)
    )
    workflow_run_id = derive_workflow_run_id(
        safe_pr_workflow_basis(
            plan,
            step,
            application,
            workspace_id,
            manifest_path=manifest_path,
        )
    )
    patches.extend(
        generated_manifest_rollback_patches(
            plan,
            step,
            application,
            manifest_path=manifest_path,
            workflow_run_id=workflow_run_id,
        )
    )
    return safe_pr_patch_sha256(patches) if patches else ""


def generated_safe_pr_rollback_patch_available(
    plan: dict[str, Any],
    step: dict[str, Any],
    application: dict[str, Any],
    *,
    workspace_id: str = DEFAULT_WORKSPACE_ID,
) -> bool:
    manifest_path = generated_safe_pr_manifest_path(plan, step, application)
    if not manifest_path:
        return False
    workflow_run_id = derive_workflow_run_id(
        safe_pr_workflow_basis(
            plan,
            step,
            application,
            workspace_id,
            manifest_path=manifest_path,
        )
    )
    return bool(
        generated_manifest_rollback_patches(
            plan,
            step,
            application,
            manifest_path=manifest_path,
            workflow_run_id=workflow_run_id,
        )
    )


def safe_pr_workflow_basis(
    plan: dict[str, Any],
    step: dict[str, Any],
    application: dict[str, Any],
    workspace_id: str,
    *,
    manifest_path: str,
) -> dict[str, Any]:
    config = step_config(step)
    settings = plan_settings_value(plan)
    application_id = str(step.get("application_id") or "")
    return {
        "workspace_id": workspace_id,
        "repo_ref": str(config.get("repo_ref") or application.get("repo_ref") or ""),
        "branch": str(config.get("branch") or application.get("branch") or "main"),
        "manifest_path": manifest_path,
        "cluster_id": str(
            config.get("cluster_id") or application.get("cluster_id") or Target.DEFAULT_CLUSTER_ID
        ),
        "namespace": str(
            config.get("namespace") or application.get("namespace") or Sandbox.NAMESPACE
        ),
        "app_name": str(application.get("name") or step.get("name") or application_id),
        "application_id": application_id,
        "environment": str(config.get("environment") or first_environment(settings) or "sandbox"),
        "commit_sha": str(config.get("commit_sha") or settings.get("commit_sha") or ""),
    }


def release_application_context(db: Any, workspace_id: str, application_id: str) -> dict[str, Any]:
    getter = getattr(db, "get_application", None)
    if not callable(getter) or not application_id:
        return {}
    application = getter(workspace_id, application_id)
    return dict(application) if isinstance(application, dict) else {}


def required_release_input_blockers(plan: dict[str, Any]) -> list[str]:
    settings = plan_settings_value(plan)
    blockers: list[str] = []
    for index, step in enumerate(plan.get("steps", []), start=1):
        if not isinstance(step, dict):
            continue
        config = step_config(step)
        label = str(step.get("name") or step.get("application_id") or f"step {index}")
        for field in ("commit_sha", "image"):
            if not str(config.get(field) or settings.get(field) or "").strip():
                blockers.append(f"{label} is missing {field}.")
    return blockers


def release_diagnostics_blockers(plan: dict[str, Any]) -> list[str]:
    if not execution_profile(plan).side_effects:
        return []
    settings = plan_settings_value(plan)
    if settings.get("require_diagnostics_pass") is False:
        if not release_diagnostics_override_reason(plan):
            return [
                "Live release dispatch cannot bypass diagnostics without a diagnostics override reason."
            ]
        return []
    diagnostics = release_plan_diagnostics(plan)
    blockers: list[str] = []
    for diagnostic in diagnostics:
        if diagnostic.severity not in {"error", "warning"}:
            continue
        location = f" at {diagnostic.path}" if diagnostic.path else ""
        blockers.append(
            f"Release diagnostics must pass before live dispatch: "
            f"{diagnostic.code}{location} - {diagnostic.message}"
        )
    return blockers


def release_diagnostics_override_reason(plan: dict[str, Any]) -> str:
    settings = plan_settings_value(plan)
    return str(
        settings.get("diagnostics_override_reason")
        or settings.get("diagnostics_bypass_reason")
        or ""
    ).strip()


def release_diagnostics_bypassed(plan: dict[str, Any]) -> bool:
    if not execution_profile(plan).side_effects:
        return False
    settings = plan_settings_value(plan)
    return settings.get("require_diagnostics_pass") is False and bool(
        release_diagnostics_override_reason(plan)
    )


def release_rollback_policy_blockers(plan: dict[str, Any]) -> list[str]:
    if not execution_profile(plan).side_effects:
        return []
    settings = plan_settings_value(plan)
    if str(settings.get("rollback_policy") or "manual") != "disabled":
        return []
    if release_rollback_override_reason(plan):
        return []
    return ["Live release dispatch cannot disable rollback without a rollback override reason."]


def release_rollback_override_reason(plan: dict[str, Any]) -> str:
    settings = plan_settings_value(plan)
    return str(
        settings.get("rollback_override_reason") or settings.get("rollback_disabled_reason") or ""
    ).strip()


def release_rollback_policy_bypassed(plan: dict[str, Any]) -> bool:
    if not execution_profile(plan).side_effects:
        return False
    settings = plan_settings_value(plan)
    return str(settings.get("rollback_policy") or "manual") == "disabled" and bool(
        release_rollback_override_reason(plan)
    )


def release_production_approval_evidence_blockers(
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
        if not approval_granted(settings, config):
            continue
        missing = []
        if not release_approval_granted_by(settings, config):
            missing.append("approval_granted_by")
        if not release_approval_reason(settings, config):
            missing.append("approval_reason")
        approval_time = release_approval_granted_at(settings, config)
        if approval_time is None:
            missing.append("approval_granted_at")
        if missing:
            label = str(step.get("name") or step.get("application_id") or "release step")
            blockers.append(
                f"{label} targets production and requires approval evidence "
                f"({', '.join(missing)}) before live dispatch."
            )
            continue
        if approval_time and release_approval_is_in_future(approval_time):
            label = str(step.get("name") or step.get("application_id") or "release step")
            blockers.append(f"{label} targets production but approval evidence is in the future.")
        elif approval_time and release_approval_is_expired(approval_time):
            label = str(step.get("name") or step.get("application_id") or "release step")
            blockers.append(
                f"{label} targets production but approval evidence is older than "
                f"{approval_max_age_label()}."
            )
    return blockers


def release_approval_granted_by(settings: dict[str, Any], config: dict[str, Any]) -> str:
    return str(
        config.get("approval_granted_by") or settings.get("approval_granted_by") or ""
    ).strip()


def release_approval_reason(settings: dict[str, Any], config: dict[str, Any]) -> str:
    return str(config.get("approval_reason") or settings.get("approval_reason") or "").strip()


def release_approval_granted_at(
    settings: dict[str, Any],
    config: dict[str, Any],
) -> datetime | None:
    return parse_release_window_time(
        config.get("approval_granted_at") or settings.get("approval_granted_at")
    )


def release_approval_granted_at_label(settings: dict[str, Any]) -> str | None:
    granted_at = parse_release_window_time(settings.get("approval_granted_at"))
    return release_window_bound_label(granted_at) if granted_at else None


def release_approval_is_expired(granted_at: datetime) -> bool:
    age = datetime.now(UTC) - granted_at.astimezone(UTC)
    return age > approval_max_age()


def release_approval_is_in_future(granted_at: datetime) -> bool:
    skew = timedelta(minutes=APPROVAL_CLOCK_SKEW_MINUTES)
    return granted_at.astimezone(UTC) > datetime.now(UTC) + skew


def approval_max_age() -> timedelta:
    raw = os.getenv(APPROVAL_MAX_AGE_HOURS_ENV, str(DEFAULT_APPROVAL_MAX_AGE_HOURS)).strip()
    try:
        hours = float(raw)
    except ValueError:
        hours = float(DEFAULT_APPROVAL_MAX_AGE_HOURS)
    return timedelta(hours=max(1.0, hours))


def approval_max_age_label() -> str:
    hours = approval_max_age().total_seconds() / 3600
    if hours.is_integer():
        return f"{int(hours)} hour(s)"
    return f"{hours:.1f} hour(s)"


def release_production_change_ticket_blockers(
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
        label = str(step.get("name") or step.get("application_id") or "release step")
        placeholder = placeholder_change_ticket(settings, config)
        if placeholder:
            blockers.append(
                f"{label} targets production and must not use placeholder change ticket {placeholder}."
            )
            continue
        if has_change_ticket(settings, config) or release_production_change_override_reason(plan):
            continue
        blockers.append(
            f"{label} targets production and requires a change ticket before live dispatch."
        )
    return blockers


def release_production_change_ticket_bypassed(
    plan: dict[str, Any],
    preview: dict[str, Any],
    wave: int,
) -> bool:
    if not execution_profile(plan).side_effects:
        return False
    if not release_production_change_override_reason(plan):
        return False
    settings = plan_settings_value(plan)
    return any(
        not has_change_ticket(settings, step_config(step))
        for step in release_production_steps_for_wave(plan, preview, wave)
    )


def release_production_change_override_reason(plan: dict[str, Any]) -> str:
    settings = plan_settings_value(plan)
    return str(
        settings.get("production_change_override_reason")
        or settings.get("change_ticket_override_reason")
        or ""
    ).strip()


def release_production_window_blockers(
    plan: dict[str, Any],
    preview: dict[str, Any],
    wave: int,
) -> list[str]:
    if not execution_profile(plan).side_effects:
        return []
    if not release_production_steps_for_wave(plan, preview, wave):
        return []
    if release_window_override_reason(plan):
        return []
    start, end = release_window_bounds(plan)
    if start is None or end is None:
        return [
            "Production live release requires release_window_start and release_window_end or a release window override reason."
        ]
    if end <= start:
        return ["Production live release window end must be after the start time."]
    now = datetime.now(UTC)
    if not (start <= now <= end):
        return [
            "Production live release is outside the approved release window "
            f"({release_window_bound_label(start)} to {release_window_bound_label(end)} UTC)."
        ]
    return []


def release_production_window_bypassed(
    plan: dict[str, Any],
    preview: dict[str, Any],
    wave: int,
) -> bool:
    if not execution_profile(plan).side_effects:
        return False
    return bool(release_production_steps_for_wave(plan, preview, wave)) and bool(
        release_window_override_reason(plan)
    )


def release_production_freeze_blockers(
    plan: dict[str, Any],
    preview: dict[str, Any],
    wave: int,
) -> list[str]:
    if not execution_profile(plan).side_effects:
        return []
    if not release_production_steps_for_wave(plan, preview, wave):
        return []
    if release_freeze_override_reason(plan):
        return []
    start, end = release_freeze_window_bounds(plan)
    has_any_bound = start is not None or end is not None
    if not has_any_bound:
        return []
    if start is None or end is None:
        return [
            "Production change freeze requires both change_freeze_start and change_freeze_end or a change freeze override reason."
        ]
    if end <= start:
        return ["Production change freeze end must be after the start time."]
    now = datetime.now(UTC)
    if start <= now <= end:
        return [
            "Production live release is inside a change freeze window "
            f"({release_window_bound_label(start)} to {release_window_bound_label(end)} UTC) "
            "and requires a change_freeze_override_reason."
        ]
    return []


def release_production_freeze_bypassed(
    plan: dict[str, Any],
    preview: dict[str, Any],
    wave: int,
) -> bool:
    if not execution_profile(plan).side_effects:
        return False
    if not release_production_steps_for_wave(plan, preview, wave):
        return False
    if not release_freeze_override_reason(plan):
        return False
    start, end = release_freeze_window_bounds(plan)
    return start is not None or end is not None


def release_freeze_override_reason(plan: dict[str, Any]) -> str:
    settings = plan_settings_value(plan)
    return str(
        settings.get("change_freeze_override_reason")
        or settings.get("freeze_window_override_reason")
        or ""
    ).strip()


def release_window_override_reason(plan: dict[str, Any]) -> str:
    settings = plan_settings_value(plan)
    return str(
        settings.get("release_window_override_reason")
        or settings.get("change_window_override_reason")
        or ""
    ).strip()


def release_production_runbook_blockers(
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
        url = release_runbook_url(settings, config)
        if release_runbook_url_is_valid(url):
            continue
        label = str(step.get("name") or step.get("application_id") or "release step")
        if url:
            blockers.append(
                f"{label} targets production and requires a live https runbook_url before live dispatch."
            )
        else:
            blockers.append(
                f"{label} targets production and requires live https runbook_url before live dispatch."
            )
    return blockers


def release_production_runbook_bypassed(
    plan: dict[str, Any],
    preview: dict[str, Any],
    wave: int,
) -> bool:
    return False


def release_runbook_url(settings: dict[str, Any], config: dict[str, Any]) -> str:
    return str(config.get("runbook_url") or settings.get("runbook_url") or "").strip()


def release_runbook_url_is_valid(value: str) -> bool:
    return live_https_url_is_valid(value)


def release_runbook_override_reason(plan: dict[str, Any]) -> str:
    settings = plan_settings_value(plan)
    return str(
        settings.get("runbook_override_reason") or settings.get("sop_override_reason") or ""
    ).strip()


def release_production_owner_blockers(
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
        if release_owner_contact(settings, config):
            continue
        label = str(step.get("name") or step.get("application_id") or "release step")
        blockers.append(
            f"{label} targets production and requires release_owner or oncall_contact before live dispatch."
        )
    return blockers


def release_owner_contact(settings: dict[str, Any], config: dict[str, Any]) -> str:
    return str(
        config.get("release_owner")
        or config.get("oncall_contact")
        or settings.get("release_owner")
        or settings.get("oncall_contact")
        or ""
    ).strip()


def release_production_abort_criteria_blockers(
    plan: dict[str, Any],
    preview: dict[str, Any],
    wave: int,
) -> list[str]:
    if not execution_profile(plan).side_effects:
        return []
    settings = plan_settings_value(plan)
    override_reason = release_abort_criteria_override_reason(plan)
    blockers: list[str] = []
    for step in release_production_steps_for_wave(plan, preview, wave):
        config = step_config(step)
        if release_abort_criteria(settings, config) or override_reason:
            continue
        label = str(step.get("name") or step.get("application_id") or "release step")
        blockers.append(
            f"{label} targets production and requires rollback_trigger, abort_criteria, "
            "or abort criteria override reason before live dispatch."
        )
    return blockers


def release_production_abort_criteria_bypassed(
    plan: dict[str, Any],
    preview: dict[str, Any],
    wave: int,
) -> bool:
    if not execution_profile(plan).side_effects:
        return False
    if not release_abort_criteria_override_reason(plan):
        return False
    settings = plan_settings_value(plan)
    return any(
        not release_abort_criteria(settings, step_config(step))
        for step in release_production_steps_for_wave(plan, preview, wave)
    )


def release_abort_criteria(settings: dict[str, Any], config: dict[str, Any]) -> str:
    return str(
        config.get("rollback_trigger")
        or config.get("abort_criteria")
        or settings.get("rollback_trigger")
        or settings.get("abort_criteria")
        or ""
    ).strip()


def release_abort_criteria_override_reason(plan: dict[str, Any]) -> str:
    settings = plan_settings_value(plan)
    return str(
        settings.get("abort_criteria_override_reason")
        or settings.get("rollback_trigger_override_reason")
        or ""
    ).strip()


def release_window_bounds(plan: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    settings = plan_settings_value(plan)
    start = parse_release_window_time(settings.get("release_window_start"))
    end = parse_release_window_time(settings.get("release_window_end"))
    return start, end


def release_freeze_window_bounds(plan: dict[str, Any]) -> tuple[datetime | None, datetime | None]:
    settings = plan_settings_value(plan)
    start = parse_release_window_time(settings.get("change_freeze_start"))
    end = parse_release_window_time(settings.get("change_freeze_end"))
    return start, end


def release_freeze_window_is_active(plan: dict[str, Any]) -> bool:
    start, end = release_freeze_window_bounds(plan)
    if start is None or end is None or end <= start:
        return False
    return start <= datetime.now(UTC) <= end


def release_production_steps_for_wave(
    plan: dict[str, Any],
    preview: dict[str, Any],
    wave: int,
) -> list[dict[str, Any]]:
    return [
        step
        for step in steps_for_wave(plan, preview, wave)
        if release_step_targets_production(plan, step)
    ]


def release_step_targets_production(plan: dict[str, Any], step: dict[str, Any]) -> bool:
    settings = plan_settings_value(plan)
    config = step_config(step)
    environment = str(config.get("environment") or settings.get("environment") or "")
    namespace = str(config.get("namespace") or settings.get("namespace") or "")
    return is_production_environment(environment) or is_production_environment(namespace)


def active_release_run_blockers(db: Any, workspace_id: str, plan: dict[str, Any]) -> list[str]:
    plan_id = str(plan.get("plan_id") or "").strip()
    has_active = getattr(db, "has_active_release_runs", None)
    if not plan_id or not callable(has_active):
        return []
    if has_active(workspace_id, plan_id):
        return [f"Release plan {plan_id} already has an active run."]
    return []


def steps_for_wave(
    plan: dict[str, Any],
    preview: dict[str, Any],
    wave: int,
) -> list[dict[str, Any]]:
    preview_steps = [
        item
        for item in preview.get("steps", [])
        if isinstance(item, dict) and item.get("wave") == wave
    ]
    application_ids = {str(step.get("application_id")) for step in preview_steps}
    return [
        step
        for step in plan.get("steps", [])
        if isinstance(step, dict) and str(step.get("application_id")) in application_ids
    ]


def generated_manifest_safe_pr_body(
    plan: dict[str, Any],
    step: dict[str, Any],
    application: dict[str, Any],
    rendered: dict[str, Any],
    title: str | None,
    body: str | None,
    workspace_id: str,
) -> SafePrRequestedBody:
    config = step_config(step)
    settings = plan_settings_value(plan)
    files = list(rendered.get("files", []))
    manifest_path = str(files[0].get("path") or config.get("manifest_path") or "deploy.yaml")
    application_id = str(step.get("application_id") or application.get("application_id") or "")
    provider = str(
        config.get("scm_provider") or settings.get("scm_provider") or GitHub.PROVIDER
    ).lower()
    request_basis = {
        "workspace_id": workspace_id,
        "repo_ref": str(config.get("repo_ref") or application.get("repo_ref") or ""),
        "branch": str(config.get("branch") or application.get("branch") or "main"),
        "manifest_path": manifest_path,
        "cluster_id": str(
            config.get("cluster_id") or application.get("cluster_id") or Target.DEFAULT_CLUSTER_ID
        ),
        "namespace": str(
            config.get("namespace") or application.get("namespace") or Sandbox.NAMESPACE
        ),
        "app_name": str(application.get("name") or step.get("name") or application_id),
        "application_id": application_id,
        "environment": str(config.get("environment") or first_environment(settings) or "sandbox"),
        "commit_sha": str(config.get("commit_sha") or settings.get("commit_sha") or ""),
    }
    request_basis["repository_id"] = derive_repository_id(request_basis)
    request_basis["binding_id"] = derive_deployment_binding_id(request_basis)
    request_basis["workflow_run_id"] = derive_workflow_run_id(request_basis)
    patches = [
        SafePrFilePatch(
            path=str(file.get("path") or manifest_path),
            content=str(file.get("content") or ""),
            description=str(file.get("description") or "Generated release manifest"),
        )
        for file in files
        if file.get("content")
    ]
    commit_sha = str(request_basis["commit_sha"])
    workflow_run_id = str(request_basis["workflow_run_id"])
    patches.extend(
        generated_manifest_rollback_patches(
            plan,
            step,
            application,
            manifest_path=manifest_path,
            workflow_run_id=workflow_run_id,
        )
    )
    step_name = str(step.get("name") or application.get("name") or application_id or "release step")
    rollback_paths = [patch.path for patch in patches if patch.path.startswith(".gitops/rollback/")]
    resource_lines = [
        f"- {resource.get('kind')}/{resource.get('name')} ({resource.get('namespace') or '-'})"
        for resource in rendered.get("resources", [])[:12]
        if isinstance(resource, dict)
    ]
    default_body = "\n".join(
        [
            str(rendered.get("summary") or "Generated release manifest update."),
            "",
            "## Release context",
            f"- plan: `{plan.get('name') or plan.get('plan_id') or ''}`",
            f"- step: `{step_name}`",
            f"- application_id: `{application_id}`",
            f"- repo_ref: `{request_basis['repo_ref']}`",
            f"- branch: `{request_basis['branch']}`",
            f"- environment: `{request_basis['environment']}`",
            f"- cluster_id: `{request_basis['cluster_id']}`",
            f"- manifest_path: `{manifest_path}`",
            f"- rollback_patch: `{rollback_paths[0]}`"
            if rollback_paths
            else "- rollback_patch: unavailable",
            "",
            "## Generated resources",
            *(resource_lines or ["- none"]),
        ]
    )
    return SafePrRequestedBody(
        title=title or f"Release manifest update: {step_name}",
        body=body or default_body,
        provider=provider,
        patches=patches,
        workspace_id=workspace_id,
        repository_id=str(request_basis["repository_id"]),
        binding_id=str(request_basis["binding_id"]),
        application_id=application_id,
        workflow_run_id=workflow_run_id,
        environment=str(request_basis["environment"]),
        manifest_path=manifest_path,
        repo_ref=str(request_basis["repo_ref"]),
        base_branch=str(request_basis["branch"]),
        commit_sha=commit_sha,
        patch_sha256=safe_pr_patch_sha256(patches),
        approval_ref=str(config.get("approval_ref") or settings.get("approval_ref") or "") or None,
        policy_decision_ref=str(
            config.get("policy_decision_ref") or settings.get("policy_decision_ref") or ""
        )
        or None,
    )


def generated_manifest_safe_pr_blockers(
    plan: dict[str, Any],
    step: dict[str, Any],
    safe_pr: SafePrRequestedBody,
) -> list[str]:
    if not release_step_targets_production(plan, step):
        return []
    if any(patch.path.startswith(".gitops/rollback/") for patch in safe_pr.patches):
        return []
    return [
        (
            "production generated Safe PR requires rollback_image, previous_image, "
            "current_image, deployed_image, or registered application image before PR creation"
        )
    ]


def generated_manifest_rollback_patches(
    plan: dict[str, Any],
    step: dict[str, Any],
    application: dict[str, Any],
    *,
    manifest_path: str,
    workflow_run_id: str,
) -> list[SafePrFilePatch]:
    rollback_image = rollback_manifest_image(plan, step, application)
    if not rollback_image:
        return []
    rollback_plan = json.loads(json.dumps(plan))
    rollback_steps = rollback_plan.get("steps")
    if not isinstance(rollback_steps, list):
        return []
    step_index = release_step_index_in_steps(rollback_steps, step)
    if step_index < 0:
        return []
    rollback_step = rollback_steps[step_index]
    if not isinstance(rollback_step, dict):
        return []
    rollback_config = rollback_step.setdefault("config", {})
    if not isinstance(rollback_config, dict):
        return []
    rollback_config["image"] = rollback_image
    rollback_config["generated_manifest_path"] = manifest_path
    rollback_rendered = render_release_step_manifest(rollback_plan, step_index, application)
    if any(
        getattr(diag, "severity", "") == "error"
        for diag in rollback_rendered.get("diagnostics", [])
    ):
        return []
    rollback_files = [
        file
        for file in list(rollback_rendered.get("files", []))
        if isinstance(file, dict) and file.get("content")
    ]
    if not rollback_files:
        return []
    return [
        SafePrFilePatch(
            path=generated_manifest_rollback_path(workflow_run_id, manifest_path),
            content=str(rollback_files[0].get("content") or ""),
            description="Generated rollback manifest from current application state",
        )
    ]


def rollback_manifest_image(
    plan: dict[str, Any],
    step: dict[str, Any],
    application: dict[str, Any],
) -> str:
    config = step_config(step)
    settings = plan_settings_value(plan)
    for source, fields in (
        (config, ("rollback_image", "previous_image", "current_image", "deployed_image")),
        (settings, ("rollback_image", "previous_image", "current_image", "deployed_image")),
        (
            application,
            ("rollback_image", "previous_image", "current_image", "deployed_image", "image"),
        ),
    ):
        for field in fields:
            value = str(source.get(field) or "").strip()
            if value:
                return value
    return ""


def generated_manifest_rollback_path(workflow_run_id: str, manifest_path: str) -> str:
    filename = manifest_path.rsplit("/", 1)[-1] or "manifest.yaml"
    if "." not in filename:
        filename = f"{filename}.yaml"
    return f".gitops/rollback/{workflow_run_id}/{filename}"
