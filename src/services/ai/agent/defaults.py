from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class EvidenceDefaults:
    object_ref_prefix: str = "object://evidence"
    kind: str = "rca_bundle"


@dataclass(frozen=True)
class IncidentMessages:
    detected_reason: str = "deterministic evidence sample indicates failure"
    not_detected_reason: str = "no deterministic incident signal found"


@dataclass(frozen=True)
class RcaMessages:
    no_incident_action_required: str = "incident flag was not set"
    missing_incident_context: str = "incident context is missing"
    missing_analysis_context: str = "RCA analysis context is missing"


@dataclass(frozen=True)
class RcaDefaults:
    recommended_action: str = "plan_recovery"


@dataclass(frozen=True)
class ActionRoutes:
    safe_pr: str = "draft_pr"
    approval_required: str = "approval_required"
    forbidden: str = "forbidden"
    auto: str = "auto"


@dataclass(frozen=True)
class RecoveryDefaults:
    action_type: str = "rollout_restart"
    risk_level: str = "low"
    dry_run: bool = True
    unknown_namespace: str = "unknown"
