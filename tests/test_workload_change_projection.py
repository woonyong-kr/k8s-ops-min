from __future__ import annotations

from domains.gitops.events import WorkflowRunCompletedBody
from domains.rca_changes.projection import workload_change_row
from packages.runtime.app import EventContext


def test_completed_workflow_projects_each_authoritative_command_diff() -> None:
    evt = WorkflowRunCompletedBody(
        workflow_run_id="workflow-a",
        application_id="application-a",
        workspace_id="workspace-a",
        binding_id="binding-a",
        environment="production",
        details={"command_ids": ["command-api", "command-service"]},
    )
    ctx = EventContext(
        event_id="event-a",
        subject=evt.__subject__,
        correlation_id="correlation-a",
        causation_id=None,
        created_at="2026-07-24T04:00:00+00:00",
        workspace_id="workspace-a",
        db=None,
    )
    authority = {
        "workspace_id": "workspace-a",
        "workflow_run_id": "workflow-a",
        "application_id": "application-a",
        "binding_id": "binding-a",
        "cluster_id": "cluster-a",
        "commit_sha": "commit-a",
        "command_id": "command-api",
        "command_result": {
            "status": "completed",
            "applied": True,
            "resources": [{"resource": "deployment/api", "applied": True}],
            "rollout": {"ready": True},
        },
        "repository_id": "repository-a",
        "namespace": "sandbox",
        "manifest_path": "deploy/k8s",
        "repo_ref": "owner/repo",
        "diff_details": {
            "workspace_id": "workspace-a",
            "repository_id": "repository-a",
            "binding_id": "binding-a",
            "workflow_run_id": "workflow-a",
            "cluster_id": "cluster-a",
            "commit_sha": "commit-a",
            "manifest_path": "deploy/k8s",
            "resource": "deployment/api",
            "namespace": "sandbox",
            "has_changes": True,
            "status": "changed",
            "actual_image": "registry/api:v1",
            "desired_image": "registry/api:v2",
        },
    }

    row = workload_change_row(evt, ctx, authority)

    assert row is not None
    assert row["event_id"] == "event-a:command-api"
    assert row["resource_name"] == "api"
    assert row["image_before"] == "registry/api:v1"
    assert row["image_after"] == "registry/api:v2"
