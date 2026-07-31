"""Release-flow execution profile helpers."""

from __future__ import annotations

import hashlib
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Any

from packages.config.environments import is_production_environment
from packages.config.settings import env
from packages.contracts.event_bus.interfaces import JsonObject

RUNTIME_MODE_DEMO = "demo"
RUNTIME_MODE_LIVE = "live"
RUNTIME_MODES = {RUNTIME_MODE_DEMO, RUNTIME_MODE_LIVE}
RELEASE_FLOW_LIVE_ENABLED_ENV = "RELEASE_FLOW_LIVE_ENABLED"
RELEASE_FLOW_LIVE_WORKSPACES_ENV = "RELEASE_FLOW_LIVE_WORKSPACES"
TRUE_VALUES = {"1", "true", "yes", "on", "enabled"}
PLACEHOLDER_CHANGE_TICKETS = {"CHG-PREFLIGHT"}


@dataclass(frozen=True)
class ReleaseExecutionProfile:
    runtime_mode: str
    provider_mode: str
    side_effects: bool
    label: str
    description: str

    def to_body(self) -> JsonObject:
        return {
            "runtime_mode": self.runtime_mode,
            "provider_mode": self.provider_mode,
            "side_effects": self.side_effects,
            "label": self.label,
            "description": self.description,
        }


def execution_profile(plan: Mapping[str, Any]) -> ReleaseExecutionProfile:
    settings = mapping_value(plan.get("settings"))
    runtime_mode = str(
        settings.get("runtime_mode") or settings.get("provider_mode") or RUNTIME_MODE_DEMO
    )
    if runtime_mode not in RUNTIME_MODES:
        runtime_mode = RUNTIME_MODE_DEMO
    if runtime_mode == RUNTIME_MODE_LIVE:
        return ReleaseExecutionProfile(
            runtime_mode=RUNTIME_MODE_LIVE,
            provider_mode=RUNTIME_MODE_LIVE,
            side_effects=True,
            label="Live mode",
            description="Dispatches real GitOps events and records the resulting workflow IDs.",
        )
    return ReleaseExecutionProfile(
        runtime_mode=RUNTIME_MODE_DEMO,
        provider_mode="dry_run",
        side_effects=False,
        label="Demo mode",
        description="Runs the same release-run path but records dry-run events without external side effects.",
    )


def dry_run_event_id(run_id: str | None, application_id: str, wave: int) -> str:
    raw = f"{run_id or 'draft'}|{application_id}|{wave}|dry-run"
    return f"dry-run-{hashlib.sha256(raw.encode()).hexdigest()[:24]}"


def dry_run_correlation_id(run_id: str | None, application_id: str, wave: int) -> str:
    raw = f"{run_id or 'draft'}|{application_id}|{wave}|correlation"
    return f"dry-run-corr-{hashlib.sha256(raw.encode()).hexdigest()[:20]}"


def release_execution_blockers(
    plan: Mapping[str, Any],
    preview: Mapping[str, Any],
    wave: int,
    *,
    workspace_id: str,
) -> list[str]:
    profile = execution_profile(plan)
    if not profile.side_effects:
        return []

    blockers: list[str] = []
    if not live_mode_allowed(workspace_id):
        blockers.append(
            "Live release dispatch is disabled for this workspace. "
            f"Set {RELEASE_FLOW_LIVE_ENABLED_ENV}=1 and include the workspace in "
            f"{RELEASE_FLOW_LIVE_WORKSPACES_ENV} before dispatching real GitOps events."
        )

    settings = mapping_value(plan.get("settings"))
    preview_by_app = preview_steps_by_app(preview)
    plan_steps = [step for step in list_value(plan.get("steps")) if isinstance(step, Mapping)]
    for step in plan_steps:
        application_id = str(step.get("application_id") or "")
        preview_step = preview_by_app.get(application_id, {})
        if int_like(preview_step.get("wave"), -1) != wave:
            continue
        config = mapping_value(step.get("config"))
        environment = str(preview_step.get("environment") or config.get("environment") or "")
        policy = str(settings.get("approval_policy") or "auto_safe")
        gate = str(config.get("approval_gate") or "inherit")
        effective_gate = gate if gate != "inherit" else policy

        if requires_external_ticket(policy, effective_gate) and not has_change_ticket(
            settings, config
        ):
            blockers.append(
                f"Application {application_id} requires an external change ticket before live dispatch."
            )
        if requires_safe_pr(effective_gate) and not has_safe_pr_ready(settings, config):
            blockers.append(
                f"Application {application_id} requires a ready Safe PR before live dispatch."
            )
        if requires_manual_approval(policy, effective_gate, environment) and not approval_granted(
            settings,
            config,
        ):
            blockers.append(
                f"Application {application_id} requires approval before live dispatch "
                f"({effective_gate})."
            )
    return blockers


def live_mode_allowed(workspace_id: str) -> bool:
    enabled = env(RELEASE_FLOW_LIVE_ENABLED_ENV, "").strip().lower() in TRUE_VALUES
    if not enabled:
        return False
    allowlist = {
        item.strip()
        for item in env(RELEASE_FLOW_LIVE_WORKSPACES_ENV, "").split(",")
        if item.strip()
    }
    return not allowlist or "*" in allowlist or workspace_id in allowlist


def requires_manual_approval(policy: str, gate: str, environment: str) -> bool:
    if gate in {"manual", "manual_each_step"}:
        return True
    if policy == "manual_each_step":
        return True
    if policy == "production_only" and is_production_environment(environment):
        return True
    return False


def requires_external_ticket(policy: str, gate: str) -> bool:
    return policy == "external_change_ticket" or gate == "external_change_ticket"


def requires_safe_pr(gate: str) -> bool:
    return gate == "safe_pr"


def approval_granted(settings: Mapping[str, Any], config: Mapping[str, Any]) -> bool:
    return bool(config.get("approval_granted") or settings.get("approval_granted"))


def change_ticket_value(settings: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    for source in (config, settings):
        for field in ("change_ticket", "change_ticket_url", "external_change_ticket"):
            value = str(source.get(field) or "").strip()
            if value:
                return value
    return ""


def placeholder_change_ticket(settings: Mapping[str, Any], config: Mapping[str, Any]) -> str:
    value = change_ticket_value(settings, config)
    return value if value in PLACEHOLDER_CHANGE_TICKETS else ""


def has_change_ticket(settings: Mapping[str, Any], config: Mapping[str, Any]) -> bool:
    value = change_ticket_value(settings, config)
    return bool(value and value not in PLACEHOLDER_CHANGE_TICKETS)


def has_safe_pr_ready(settings: Mapping[str, Any], config: Mapping[str, Any]) -> bool:
    return safe_pr_evidence_ready(config) or safe_pr_evidence_ready(settings)


def safe_pr_evidence_ready(source: Mapping[str, Any]) -> bool:
    if not source.get("safe_pr_ready"):
        return False
    evidence = mapping_value(source.get("safe_pr_evidence"))
    required_fields = ("workflow_run_id", "pr_url", "created_at")
    if not all(str(evidence.get(field) or "").strip() for field in required_fields):
        return False
    configured_url = str(source.get("safe_pr_url") or "").strip()
    evidence_url = str(evidence.get("pr_url") or "").strip()
    return not configured_url or configured_url == evidence_url


def preview_steps_by_app(preview: Mapping[str, Any]) -> dict[str, Mapping[str, Any]]:
    return {
        str(step.get("application_id") or ""): step
        for step in list_value(preview.get("steps"))
        if isinstance(step, Mapping)
    }


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def int_like(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def mapping_value(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}
