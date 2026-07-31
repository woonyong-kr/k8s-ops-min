"""Safe Timeline facts produced by durable GitOps workflow mutations.

This module maps only the small workflow identity and guarded status values.
It must never receive workflow summaries, manifests, command output, or step
details: those payloads may contain source data that does not belong in a
shared retained Timeline ledger.
"""

from __future__ import annotations

from datetime import UTC, datetime

from packages.contracts.gitops import WorkflowRunStatus, WorkflowStepStatus
from packages.contracts.parity import ClusterScope
from packages.contracts.timeline import TimelineApplicationWorkflowSubject, TimelineEvent


def git_changed_timeline_event(
    *,
    source_event_id: str,
    source_created_at: str,
    workspace_id: str,
    cluster_id: str,
    application_id: str,
    binding_id: str,
    workflow_run_id: str,
) -> TimelineEvent:
    """Build a retained fact for a confirmed canonical ``git.changed`` event.

    The caller must verify the application, binding, and workflow run against
    persistence first. Git payloads, manifests, and repository-change lists do
    not cross this boundary into the retained Timeline ledger.
    """
    source_key = ":".join(("gitops", source_event_id, "git.changed", workflow_run_id))
    return TimelineEvent(
        event_id=source_key,
        source="gitops",
        source_key=source_key,
        native_id=workflow_run_id,
        activity="change",
        occurred_at=workflow_occurred_at(source_created_at),
        scope=ClusterScope(workspace_id=workspace_id, cluster_id=cluster_id),
        subject=TimelineApplicationWorkflowSubject(
            application_id=application_id,
            binding_id=binding_id,
            workflow_run_id=workflow_run_id,
        ),
        event_type="gitops_change",
        severity="info",
        title="Git change confirmed",
        metadata={"state": "changed"},
    )


def workflow_run_timeline_event(
    *,
    source_event_id: str,
    source_created_at: str,
    workspace_id: str,
    cluster_id: str,
    application_id: str,
    binding_id: str,
    workflow_run_id: str,
    status: str,
    current_step: str,
) -> TimelineEvent:
    """Build one retained fact for an already-applied workflow run transition."""
    normalized_status = WorkflowRunStatus(status).value
    source_key = ":".join(
        (
            "application_workflow",
            source_event_id,
            "run",
            workflow_run_id,
            normalized_status,
            current_step,
        )
    )
    activity, severity = _run_presentation(normalized_status)
    return TimelineEvent(
        event_id=source_key,
        source="application_workflow",
        source_key=source_key,
        native_id=workflow_run_id,
        activity=activity,
        occurred_at=workflow_occurred_at(source_created_at),
        scope=ClusterScope(workspace_id=workspace_id, cluster_id=cluster_id),
        subject=TimelineApplicationWorkflowSubject(
            application_id=application_id,
            binding_id=binding_id,
            workflow_run_id=workflow_run_id,
        ),
        event_type="deployment",
        severity=severity,
        title=f"Workflow {workflow_run_id} {normalized_status}",
        metadata={"status": normalized_status, "step": current_step},
    )


def workflow_step_timeline_event(
    *,
    source_event_id: str,
    source_created_at: str,
    workspace_id: str,
    cluster_id: str,
    application_id: str,
    binding_id: str,
    workflow_run_id: str,
    step: str,
    status: str,
) -> TimelineEvent:
    """Build one retained fact for an already-applied workflow step transition."""
    normalized_status = WorkflowStepStatus(status).value
    source_key = ":".join(
        (
            "application_workflow",
            source_event_id,
            "step",
            workflow_run_id,
            step,
            normalized_status,
        )
    )
    activity, severity = _step_presentation(normalized_status)
    return TimelineEvent(
        event_id=source_key,
        source="application_workflow",
        source_key=source_key,
        native_id=f"{workflow_run_id}:{step}",
        activity=activity,
        occurred_at=workflow_occurred_at(source_created_at),
        scope=ClusterScope(workspace_id=workspace_id, cluster_id=cluster_id),
        subject=TimelineApplicationWorkflowSubject(
            application_id=application_id,
            binding_id=binding_id,
            workflow_run_id=workflow_run_id,
        ),
        event_type="deployment",
        severity=severity,
        title=f"Workflow {workflow_run_id} {step} {normalized_status}",
        metadata={"step": step, "status": normalized_status},
    )


def workflow_occurred_at(source_created_at: str) -> datetime:
    """Prefer the durable source-envelope time; test-only legacy contexts get UTC now."""
    normalized = source_created_at.strip()
    if normalized:
        parsed = datetime.fromisoformat(normalized.replace("Z", "+00:00"))
        return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)
    return datetime.now(UTC)


def _run_presentation(status: str) -> tuple[str, str]:
    if status == WorkflowRunStatus.FAILED.value:
        return "warning", "critical"
    return "change", "info"


def _step_presentation(status: str) -> tuple[str, str]:
    if status == WorkflowStepStatus.FAILED.value:
        return "warning", "critical"
    return "change", "info"
