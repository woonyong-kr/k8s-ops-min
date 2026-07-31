from __future__ import annotations

import asyncio
from contextlib import nullcontext
from dataclasses import replace
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import Any

import pytest
from conftest import load_service, run_handler
from fastapi import HTTPException

from domains.gitops import router as gitops_router
from domains.gitops.events import GitWebhookReceivedBody, WorkflowRunCompletedBody
from domains.rca import router as rca_router
from domains.rca.events import (
    HealingActionDraft,
    RecoveryActionCandidate,
    RecoveryActionSelectedBody,
    RecoveryPlan,
    RecoveryPrMergedBody,
    RecoveryPrTrackedBody,
    RecoveryRetryRequestedBody,
    RecoveryVerificationStartedBody,
)
from domains.rca.router import recovery_plan_status_response
from domains.scm.events import SafePrCreatedBody
from packages.contracts.gateway.requests import RecoveryRetryRequest
from services.ai.agent.defaults import ActionRoutes

feedback_worker = load_service("ai/rca-feedback-worker")

NOW = datetime(2026, 7, 24, 1, 0, tzinfo=UTC)
HEAD_SHA = "a" * 40
MERGE_SHA = "b" * 40
TARGET = {
    "workspace_id": "workspace-1",
    "cluster_id": "game-server111-7224",
    "namespace": "sandbox",
    "resource_kind": "Deployment",
    "resource_name": "api-server",
}


def selected_record() -> dict[str, Any]:
    return {
        "plan_id": "plan-1",
        "workspace_id": "workspace-1",
        "correlation_id": "correlation-1",
        "incident_id": "incident-1",
        "evidence_ref": "object://evidence/incident-1.json",
        "status": "selected",
        "selected_action_id": "action-1",
        "payload": {
            "target": TARGET,
            "candidates": [
                {
                    "action_id": "action-1",
                    "route": ActionRoutes().safe_pr,
                    "draft": {
                        "params": {
                            "root_cause": "lobby_capacity_saturation",
                        }
                    },
                }
            ],
        },
    }


def approved_params() -> dict[str, object]:
    return {
        "workspace_id": "workspace-1",
        "repository_id": "repo-1",
        "binding_id": "binding-1",
        "application_id": "app-1",
        "repo_ref": "Jungle-303-04/game-server",
        "base_branch": "main",
        "commit_sha": "c" * 40,
        "expected_replicas": 2,
        "authorized_changes": [
            {
                "field_path": "spec.replicas",
                "current_value": 1,
                "desired_value": 2,
            }
        ],
        "verification_failure_ratio_before": 0.8,
        "verification_failure_ratio_metric_identity": {
            "namespace": "sandbox",
            "resource_kind": "Deployment",
            "resource_name": "api-server",
            "service": "matchmaking",
            "sli": "admission",
            "symptom": "admission_failure",
        },
        "verification_request_rate_baseline": 40.0,
        "verification_request_rate_metric_identity": {
            "namespace": "sandbox",
            "resource_kind": "Deployment",
            "resource_name": "api-server",
            "service": "matchmaking",
            "sli": "admission",
            "symptom": "admission_failure",
        },
        "verification_evidence_cadence_seconds": 30,
        "verification_alert_before": {
            "available": True,
            "alert_event_id": "alert-1",
            "rule_id": "opsia-sli",
            "rule_name": "OpsiaSliFailureRatioHigh",
            "source": "alertmanager",
            "subject_key": "sandbox:Deployment:api-server",
            "series_identity": {
                "namespace": "sandbox",
                "resource_kind": "Deployment",
                "resource_name": "api-server",
                "service": "matchmaking",
                "sli": "admission",
                "symptom": "admission_failure",
            },
            "observed_value": 0.8,
            "threshold": 0.2,
            "fired_at": "2026-07-24T00:59:00+00:00",
            "subject": {
                "cluster": "game-server111-7224",
                "namespace": "sandbox",
                "kind": "Deployment",
                "name": "api-server",
            },
        },
        "root_cause": "lobby_capacity_saturation",
        "verification_contract": "protected_workload_continuity",
        "protected_baseline": [
            {
                "kind": "Deployment",
                "namespace": "sandbox",
                "name": f"arena-{chr(ord('a') + index)}",
                "uid": f"room-{index}",
                "pod_uids": [f"room-{index}-pod"],
                "pod_start_times": ["2026-07-24T00:00:00Z"],
                "restart_count": 0,
            }
            for index in range(5)
        ],
        "protected_session_baseline": [
            {
                "kind": "Deployment",
                "namespace": "sandbox",
                "name": f"arena-{chr(ord('a') + index)}",
                "continuity_id": f"session-{index}",
                "pod_uid": f"room-{index}-pod",
                "value": 1.0,
                "sample_timestamp": (NOW.timestamp() - 30),
            }
            for index in range(5)
        ],
    }


def created_pr() -> SafePrCreatedBody:
    return SafePrCreatedBody(
        pr_url="https://github.com/Jungle-303-04/game-server/pull/42",
        provider="github",
        mode="github_rest",
        workspace_id="workspace-1",
        repository_id="repo-1",
        binding_id="binding-1",
        application_id="app-1",
        workflow_run_id="safe-pr-run",
        environment="sandbox",
        manifest_path="k8s/api-server.yaml",
        repo_ref="Jungle-303-04/game-server",
        base_branch="main",
        commit_sha="c" * 40,
        patch_sha256="patch-sha",
        pr_number=42,
        pr_node_id="PR_node_42",
        head_ref="opsia/recovery-plan-1",
        head_sha=HEAD_SHA,
    )


def test_lifecycle_response_is_opt_in_for_rolling_frontend_compatibility() -> None:
    plan = RecoveryPlan(
        plan_id="plan-1",
        incident_id="incident-1",
        evidence_ref="object://evidence/incident-1.json",
        summary="restore approved capacity",
        target=TARGET,
        recommended_action_id="action-1",
        execution_route=ActionRoutes().safe_pr,
        selection_required=True,
        candidates=[],
    )
    record = {
        "correlation_id": "correlation-1",
        "status": "verification_pending",
        "selected_action_id": None,
        "selected_by": None,
        "payload": {"lifecycle": {"phase": "verification_pending"}},
    }

    legacy = recovery_plan_status_response(record, plan)
    opted_in = recovery_plan_status_response(record, plan, include_lifecycle=True)

    assert "lifecycle" not in legacy.model_dump(exclude_none=True)
    assert opted_in.lifecycle == {"phase": "verification_pending"}


class SafePrDb:
    def __init__(self, event: SafePrCreatedBody) -> None:
        self.event = event
        self.record = selected_record()
        self.transitions: list[tuple[tuple[str, ...], str, dict[str, Any]]] = []
        self.reopened = False

    async def get_recovery_plan_by_correlation(
        self,
        correlation_id: str,
        workspace_id: str,
    ) -> dict[str, Any]:
        assert (correlation_id, workspace_id) == ("correlation-1", "workspace-1")
        return self.record

    async def get_workflow_approval(
        self,
        approval_id: str,
        workspace_id: str,
    ) -> dict[str, Any]:
        assert approval_id.startswith("approval-")
        assert workspace_id == "workspace-1"
        return {
            "details": {
                "selected_candidate": {
                    "draft": {"params": approved_params()},
                }
            }
        }

    async def current_database_time(self) -> datetime:
        return NOW

    async def get_cluster_registration(
        self,
        workspace_id: str,
        cluster_id: str,
    ) -> dict[str, Any]:
        assert (workspace_id, cluster_id) == (
            "workspace-1",
            "game-server111-7224",
        )
        return {"settings": {"evidence_interval_seconds": 30}}

    async def list_alert_events(
        self,
        workspace_id: str,
        *,
        rule_name: str | None = None,
        source: str | None = None,
        incident_ids: tuple[str, ...] | None = None,
        limit: int,
    ) -> list[dict[str, Any]]:
        assert workspace_id == "workspace-1"
        assert rule_name == "OpsiaSliFailureRatioHigh"
        assert source == "alertmanager"
        assert set(incident_ids or ()) == {"correlation-1", "incident-1"}
        assert limit == 10
        return [
            {
                "event_id": "alert-1",
                "rule_id": "opsia-sli",
                "rule_name": "OpsiaSliFailureRatioHigh",
                "source": "alertmanager",
                "subject_key": "sandbox:Deployment:api-server",
                "series_identity": {
                    "namespace": "sandbox",
                    "resource_kind": "Deployment",
                    "resource_name": "api-server",
                    "service": "matchmaking",
                    "sli": "admission",
                    "symptom": "admission_failure",
                },
                "incident_id": "incident-1",
                "status": "firing",
                "observed_value": 0.8,
                "threshold": 0.2,
                "fired_at": "2026-07-24T00:59:00+00:00",
                "subject": {
                    "cluster": TARGET["cluster_id"],
                    "namespace": TARGET["namespace"],
                    "kind": TARGET["resource_kind"],
                    "name": TARGET["resource_name"],
                },
            }
        ]

    async def get_evidence_payload(
        self,
        workspace_id: str,
        correlation_id: str,
        kind: str,
    ) -> dict[str, Any]:
        assert (workspace_id, correlation_id, kind) == (
            "workspace-1",
            "correlation-1",
            "rca_bundle",
        )
        labels = {
            "namespace": "sandbox",
            "resource_kind": "Deployment",
            "resource_name": "api-server",
            "service": "matchmaking",
            "sli": "admission",
            "symptom": "admission_failure",
        }
        return {
            "object_ref": "object://evidence/correlation-1.json",
            "window_start": NOW.isoformat(),
            "metrics": {
                "results": {
                    "opsia_sli_failure_ratio": {
                        "samples": [{"metric": labels, "value": 0.8}]
                    },
                    "opsia_sli_request_rate": {
                        "samples": [{"metric": labels, "value": 40.0}]
                    },
                    "opsia_continuity_active_sessions": {
                        "samples": [
                            {
                                "metric": {
                                    "namespace": "sandbox",
                                    "resource_kind": "Deployment",
                                    "resource_name": f"arena-{chr(ord('a') + index)}",
                                    "continuity_id": f"session-{index}",
                                    "pod_uid": f"room-{index}-pod",
                                },
                                "value": 1,
                                "timestamp": NOW.timestamp(),
                            }
                            for index in range(5)
                        ]
                    },
                }
            },
            "metadata": {
                "current_workload_snapshots": [
                    {
                        "workload": {
                            "kind": "Deployment",
                            "namespace": "sandbox",
                            "name": f"arena-{chr(ord('a') + index)}",
                            "uid": f"room-{index}",
                        },
                        "deployment_status": {
                            "desired_replicas": 1,
                            "ready_replicas": 1,
                            "updated_replicas": 1,
                            "available_replicas": 1,
                            "unavailable_replicas": 0,
                        },
                        "deployment_labels": {
                            "opsia.dev/recovery-continuity": "protected",
                        },
                        "pod_template_labels": {
                            "opsia.dev/recovery-continuity": "protected",
                        },
                        "pod_statuses": [
                            {
                                "uid": f"room-{index}-pod",
                                "ready": True,
                                "restart_count": 0,
                                "start_time": "2026-07-24T00:00:00Z",
                            }
                        ],
                    }
                    for index in range(5)
                ]
            },
        }

    async def update_recovery_plan_lifecycle_if_status(
        self,
        plan_id: str,
        workspace_id: str,
        *,
        expected_statuses: tuple[str, ...],
        status: str,
        lifecycle: dict[str, Any],
    ) -> dict[str, Any]:
        assert (plan_id, workspace_id) == ("plan-1", "workspace-1")
        self.transitions.append((expected_statuses, status, lifecycle))
        return {"plan_id": plan_id, "status": status}

    async def reopen_recovery_plan_action(
        self,
        plan_id: str,
        workspace_id: str,
        action_id: str,
    ) -> bool:
        self.reopened = True
        return True


def test_real_draft_pr_route_enters_pr_open_with_immutable_identity() -> None:
    evt = created_pr()
    db = SafePrDb(evt)

    emitted = run_handler(
        feedback_worker.on_recovery_safe_pr_created,
        evt,
        db=db,
        correlation_id="correlation-1",
    )

    assert len(emitted) == 1
    assert isinstance(emitted[0], RecoveryPrTrackedBody)
    assert len(db.transitions) == 1
    expected, status, lifecycle = db.transitions[0]
    assert expected == ("selected",)
    assert status == "pr_open"
    assert lifecycle["pr"] == {
        "url": evt.pr_url,
        "number": 42,
        "node_id": "PR_node_42",
        "head_ref": "opsia/recovery-plan-1",
        "head_sha": HEAD_SHA,
        "repo_ref": evt.repo_ref,
        "repository_id": evt.repository_id,
        "binding_id": evt.binding_id,
        "application_id": evt.application_id,
        "base_branch": evt.base_branch,
        "manifest_path": evt.manifest_path,
        "environment": evt.environment,
        "cluster_id": TARGET["cluster_id"],
        "patch_sha256": evt.patch_sha256,
        "tracked_at": NOW.isoformat(),
    }
    assert lifecycle["verification"]["expected"] == {
        "failure_ratio_max": 0.2,
        "request_rate_baseline": 40.0,
        "request_rate_tolerance_ratio": 0.2,
        "replicas": 2,
        "failure_ratio_metric_identity": {
            "namespace": "sandbox",
            "resource_kind": "Deployment",
            "resource_name": "api-server",
            "service": "matchmaking",
            "sli": "admission",
            "symptom": "admission_failure",
        },
        "request_rate_metric_identity": {
            "namespace": "sandbox",
            "resource_kind": "Deployment",
            "resource_name": "api-server",
            "service": "matchmaking",
            "sli": "admission",
            "symptom": "admission_failure",
        },
        "evidence_cadence_seconds": 30,
        "protected_workloads": 5,
    }
    assert lifecycle["verification"]["before"]["alert_event_id"] == "alert-1"
    assert lifecycle["verification"]["before"]["request_rate"] == 40.0
    assert len(lifecycle["verification"]["protected_baseline"]) == 5
    assert len(lifecycle["verification"]["protected_session_baseline"]) == 5


class MissingCadenceSafePrDb(SafePrDb):
    async def get_workflow_approval(
        self,
        approval_id: str,
        workspace_id: str,
    ) -> dict[str, Any]:
        approval = await super().get_workflow_approval(approval_id, workspace_id)
        params = approval["details"]["selected_candidate"]["draft"]["params"]
        params.pop("verification_evidence_cadence_seconds")
        return approval

    async def get_cluster_registration(
        self,
        workspace_id: str,
        cluster_id: str,
    ) -> dict[str, Any]:
        assert (workspace_id, cluster_id) == (
            "workspace-1",
            "game-server111-7224",
        )
        return {"settings": {}}


def test_pr_is_tracked_but_merge_blocked_when_prerequisite_is_missing() -> None:
    evt = created_pr()
    db = MissingCadenceSafePrDb(evt)

    emitted = run_handler(
        feedback_worker.on_recovery_safe_pr_created,
        evt,
        db=db,
        correlation_id="correlation-1",
    )

    assert len(emitted) == 1
    assert isinstance(emitted[0], RecoveryPrTrackedBody)
    assert len(db.transitions) == 1
    expected, status, lifecycle = db.transitions[0]
    assert expected == ("selected",)
    assert status == "pr_open"
    assert lifecycle["pr"]["url"] == evt.pr_url
    assert lifecycle["verification"]["status"] == "merge_blocked"
    assert lifecycle["verification"]["blockers"] == [
        "cluster:evidence_cadence"
    ]
    assert db.reopened is False


def tracked_record(
    *,
    status: str = "pr_open",
    expected_replicas: int = 2,
) -> dict[str, Any]:
    return {
        "plan_id": "plan-1",
        "workspace_id": "workspace-1",
        "correlation_id": "correlation-1",
        "incident_id": "incident-1",
        "evidence_ref": "object://evidence/incident-1.json",
        "status": status,
        "payload": {
            "target": TARGET,
            "lifecycle": {
                "phase": status,
                "pr": {
                    "url": created_pr().pr_url,
                    "number": 42,
                    "node_id": "PR_node_42",
                    "head_ref": "opsia/recovery-plan-1",
                    "head_sha": HEAD_SHA,
                    "repo_ref": created_pr().repo_ref,
                    "repository_id": "repo-1",
                    "binding_id": "binding-1",
                    "application_id": "app-1",
                    "base_branch": "main",
                    "manifest_path": "k8s/api-server.yaml",
                    "environment": "sandbox",
                    "cluster_id": TARGET["cluster_id"],
                },
                "verification": {
                    "minimum_seconds": 300,
                    "maximum_seconds": 600,
                    "expected": {"replicas": expected_replicas},
                    "before": {"alert_event_id": "alert-1"},
                    "target": TARGET,
                },
                "authorization": {
                    "target": TARGET,
                    "changes": [
                        {
                            "field_path": "spec.replicas",
                            "current_value": 1,
                            "desired_value": expected_replicas,
                        }
                    ],
                },
            },
        },
    }


def merged_webhook(
    *,
    action: str = "closed",
    head_sha: str = HEAD_SHA,
    merged: bool | None = None,
) -> dict[str, Any]:
    return {
        "action": action,
        "number": 42,
        "repository": {"full_name": "Jungle-303-04/game-server"},
        "pull_request": {
            "number": 42,
            "node_id": "PR_node_42",
            "html_url": created_pr().pr_url,
            "merged": action == "closed" if merged is None else merged,
            "merge_commit_sha": MERGE_SHA,
            "head": {
                "ref": "opsia/recovery-plan-1",
                "sha": head_sha,
            },
            "base": {"ref": "main"},
        },
    }


class Accepted:
    def __init__(self, body: object, correlation_id: str) -> None:
        self.event = SimpleNamespace(
            event_id=f"event-{getattr(body, '__subject__', 'unknown')}",
            correlation_id=correlation_id,
            to_dict=lambda: {
                "event_id": f"event-{getattr(body, '__subject__', 'unknown')}",
                "correlation_id": correlation_id,
            },
        )


class Events:
    def __init__(self) -> None:
        self.calls: list[tuple[object, str, str | None]] = []

    async def accept_body(
        self,
        body: object,
        *,
        correlation_id: str,
        causation_id: str | None = None,
    ) -> Accepted:
        self.calls.append((body, correlation_id, causation_id))
        return Accepted(body, correlation_id)


class MergeDb:
    def __init__(self, *, expected_replicas: int = 2) -> None:
        self.record = tracked_record(expected_replicas=expected_replicas)
        self.transition: tuple[tuple[str, ...], str, dict[str, Any]] | None = None
        self.cleared_selection = False

    def find_open_recovery_plan_for_pull_request_base_identity(
        self,
        **kwargs: object,
    ) -> dict[str, Any] | None:
        expected = self.record["payload"]["lifecycle"]["pr"]
        return (
            self.record
            if kwargs
            == {
                "pr_url": expected["url"],
                "repo_ref": expected["repo_ref"],
                "base_branch": expected["base_branch"],
                "pr_number": expected["number"],
                "pr_node_id": expected["node_id"],
                "head_ref": expected["head_ref"],
            }
            else None
        )

    def find_open_recovery_plan_for_pull_request(
        self,
        **kwargs: object,
    ) -> dict[str, Any] | None:
        expected = self.record["payload"]["lifecycle"]["pr"]
        return (
            self.record
            if kwargs
            == {
                "pr_url": expected["url"],
                "repo_ref": expected["repo_ref"],
                "base_branch": expected["base_branch"],
                "pr_number": expected["number"],
                "pr_node_id": expected["node_id"],
                "head_ref": expected["head_ref"],
                "head_sha": expected["head_sha"],
            }
            else None
        )

    def list_active_github_poll_targets(self, *, limit: int) -> list[dict[str, str]]:
        assert limit == 1000
        return [
            {
                "workspace_id": "workspace-1",
                "repository_id": "repo-1",
                "repo_ref": "Jungle-303-04/game-server",
                "branch": "main",
                "watch_target_id": "watch-1",
                "binding_id": "binding-1",
                "application_id": "app-1",
                "environment": "sandbox",
                "cluster_id": TARGET["cluster_id"],
                "manifest_path": "k8s/api-server.yaml",
                "source_type": "raw-yaml",
            }
        ]

    def current_database_time(self) -> datetime:
        return NOW

    def update_recovery_plan_lifecycle_if_status(
        self,
        plan_id: str,
        workspace_id: str,
        *,
        expected_statuses: tuple[str, ...],
        status: str,
        lifecycle: dict[str, Any],
        clear_selection: bool = False,
    ) -> dict[str, Any]:
        self.cleared_selection = clear_selection
        self.transition = (expected_statuses, status, lifecycle)
        return {"plan_id": plan_id}

    def unit_of_work(self):
        return nullcontext()


def test_signed_exact_merge_moves_only_tracked_pr_to_deploy_pending(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = MergeDb(expected_replicas=3)
    events = Events()
    monkeypatch.setenv("GITOPS_WEBHOOK_IMAGE", "demo/game-server:v2")

    response = asyncio.run(
        gitops_router.handle_tracked_recovery_pull_request(
            payload=merged_webhook(),
            db=db,
            events=events,
        )
    )

    assert response is not None
    assert db.transition is not None
    expected, status, lifecycle = db.transition
    assert expected == ("pr_open",)
    assert status == "deploy_pending"
    assert lifecycle["merge"]["merge_commit_sha"] == MERGE_SHA
    assert lifecycle["merge"]["binding_id"] == "binding-1"
    assert lifecycle["merge"]["retry_attempt"] == 0
    assert lifecycle["merge"]["deployment_request"]["commit_sha"] == MERGE_SHA
    assert lifecycle["merge"]["deployment_request"]["workflow_run_id"].startswith(
        "workflow-"
    )
    assert lifecycle["merge"]["workflow_run_id"] == (
        gitops_router.recovery_merge_workflow_run_id("plan-1", MERGE_SHA)
    )
    assert lifecycle["merge"]["deployment_request"]["force"] is True
    assert lifecycle["merge"]["deployment_request"]["replicas"] == 3
    assert len(events.calls) == 2
    assert isinstance(events.calls[0][0], RecoveryPrMergedBody)
    assert events.calls[1][0].commit_sha == MERGE_SHA
    assert events.calls[1][0].force is True
    assert events.calls[1][2] is not None


def test_merge_does_not_start_deploy_while_verification_is_blocked(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = MergeDb()
    db.record["payload"]["lifecycle"]["verification"].update(
        {
            "status": "merge_blocked",
            "blockers": ["metadata:current_workload_snapshots"],
        }
    )
    events = Events()
    monkeypatch.setenv("GITOPS_WEBHOOK_IMAGE", "demo/game-server:v2")

    response = asyncio.run(
        gitops_router.handle_tracked_recovery_pull_request(
            payload=merged_webhook(),
            db=db,
            events=events,
        )
    )

    assert response is not None
    assert response.status_code == 409
    assert db.transition is None
    assert events.calls == []


@pytest.mark.parametrize(
    ("payload", "reason_code"),
    [
        (merged_webhook(action="synchronize"), "safe_pr_head_changed"),
        (merged_webhook(merged=False), "safe_pr_closed_without_merge"),
        (merged_webhook(head_sha="d" * 40), "safe_pr_head_mismatch"),
    ],
)
def test_force_push_or_head_mismatch_fails_closed(
    monkeypatch: pytest.MonkeyPatch,
    payload: dict[str, Any],
    reason_code: str,
) -> None:
    db = MergeDb()
    events = Events()
    monkeypatch.setenv("GITOPS_WEBHOOK_IMAGE", "demo/game-server:v2")

    asyncio.run(
        gitops_router.handle_tracked_recovery_pull_request(
            payload=payload,
            db=db,
            events=events,
        )
    )

    assert db.transition is not None
    assert db.transition[1] == "selection_requested"
    assert db.cleared_selection is True
    assert db.transition[2]["failure"]["reason_code"] == reason_code
    assert len(events.calls) == 1


def test_cross_repository_webhook_cannot_claim_another_tenant_recovery(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = MergeDb()
    events = Events()
    payload = merged_webhook()
    repository = payload["repository"]
    assert isinstance(repository, dict)
    repository["full_name"] = "other-tenant/game-server"
    monkeypatch.setenv("GITOPS_WEBHOOK_IMAGE", "demo/game-server:v2")

    response = asyncio.run(
        gitops_router.handle_tracked_recovery_pull_request(
            payload=payload,
            db=db,
            events=events,
        )
    )

    assert response is None
    assert db.transition is None
    assert events.calls == []


class DeployDb:
    def __init__(self, *, workflow_status: str = "succeeded") -> None:
        self.record = tracked_record(status="deploy_pending")
        lifecycle = self.record["payload"]["lifecycle"]
        lifecycle["merge"] = {
            "workflow_run_id": "workflow-1",
            "binding_id": "binding-1",
            "application_id": "app-1",
            "cluster_id": TARGET["cluster_id"],
            "merge_commit_sha": MERGE_SHA,
        }
        self.workflow_status = workflow_status
        self.transitions: list[tuple[tuple[str, ...], str, dict[str, Any]]] = []

    async def get_recovery_plan_for_workflow(
        self,
        workspace_id: str,
        workflow_run_id: str,
        binding_id: str,
        application_id: str,
    ) -> dict[str, Any]:
        return self.record

    async def get_workflow_run(self, workflow_run_id: str) -> dict[str, Any]:
        return {
            "workflow_run_id": workflow_run_id,
            "workspace_id": "workspace-1",
            "binding_id": "binding-1",
            "application_id": "app-1",
            "cluster_id": TARGET["cluster_id"],
            "commit_sha": MERGE_SHA,
            "status": self.workflow_status,
        }

    async def current_database_time(self) -> datetime:
        return NOW

    async def update_recovery_plan_lifecycle_if_status(
        self,
        plan_id: str,
        workspace_id: str,
        *,
        expected_statuses: tuple[str, ...],
        status: str,
        lifecycle: dict[str, Any],
    ) -> dict[str, Any]:
        self.transitions.append((expected_statuses, status, lifecycle))
        return {"plan_id": plan_id}


def merged_event() -> RecoveryPrMergedBody:
    return RecoveryPrMergedBody(
        plan_id="plan-1",
        incident_id="incident-1",
        pr_url=created_pr().pr_url,
        merge_commit_sha=MERGE_SHA,
        repository_id="repo-1",
        repo_ref=created_pr().repo_ref,
        binding_id="binding-1",
        application_id="app-1",
        workflow_run_id="workflow-1",
        cluster_id=TARGET["cluster_id"],
        workspace_id="workspace-1",
    )


def test_push_before_pr_race_reconciles_already_succeeded_workflow() -> None:
    db = DeployDb()

    emitted = run_handler(
        feedback_worker.on_recovery_pr_merged,
        merged_event(),
        db=db,
        correlation_id="correlation-1",
    )

    assert len(emitted) == 1
    assert isinstance(emitted[0], RecoveryVerificationStartedBody)
    assert db.transitions[0][0] == ("deploy_pending",)
    assert db.transitions[0][1] == "verification_pending"
    verification = db.transitions[0][2]["verification"]
    assert verification["started_at"] == NOW.isoformat()
    assert verification["deadline_at"] == "2026-07-24T01:10:00+00:00"


def test_pr_before_push_starts_from_exact_completion_event_once() -> None:
    db = DeployDb()
    evt = WorkflowRunCompletedBody(
        workflow_run_id="workflow-1",
        application_id="app-1",
        workspace_id="workspace-1",
        binding_id="binding-1",
    )

    emitted = run_handler(
        feedback_worker.on_recovery_deploy_completed,
        evt,
        db=db,
        correlation_id="correlation-1",
    )

    assert len(emitted) == 1
    assert isinstance(emitted[0], RecoveryVerificationStartedBody)


def test_spoofed_workflow_completion_cannot_start_verification() -> None:
    db = DeployDb()
    db.record["payload"]["lifecycle"]["merge"]["merge_commit_sha"] = "e" * 40

    emitted = run_handler(
        feedback_worker.on_recovery_pr_merged,
        merged_event(),
        db=db,
        correlation_id="correlation-1",
    )

    assert emitted == []
    assert db.transitions == []


def retry_record(*, reason_code: str) -> dict[str, Any]:
    workflow_run_id = "workflow-original"
    deployment = GitWebhookReceivedBody(
        commit_sha=MERGE_SHA,
        image="demo/game-server:v2",
        replicas=2,
        correlation_id="correlation-1",
        workspace_id="workspace-1",
        repository_id="repo-1",
        repo_ref="Jungle-303-04/game-server",
        branch="main",
        watch_target_id="watch-1",
        binding_id="binding-1",
        application_id="app-1",
        workflow_run_id=workflow_run_id,
        environment="sandbox",
        cluster_id=TARGET["cluster_id"],
        manifest_path="k8s/api-server.yaml",
        source_type="raw-yaml",
    )
    candidate = RecoveryActionCandidate(
        action_id="action-1",
        title="로비 replicas 복구 PR",
        description="restore lobby capacity",
        draft=HealingActionDraft(
            action_type="replica_scale",
            namespace="sandbox",
            resource_kind="Deployment",
            resource_name="api-server",
            reason="lobby capacity saturated",
            risk_level="low",
            dry_run=True,
            source_evidence=["evidence-1"],
            params={"replicas": 2},
        ),
        route=ActionRoutes().safe_pr,
        rank=1,
        score=0.9,
        risk_level="low",
        blast_radius="single deployment",
        approval_required=True,
        prerequisites=[],
        validation_checks=[],
        rollback_plan="restore previous replicas",
        evidence_refs=["evidence-1"],
    )
    plan = RecoveryPlan(
        plan_id="plan-1",
        incident_id="incident-1",
        evidence_ref="object://evidence/incident-1.json",
        summary="restore lobby capacity",
        target=TARGET,
        recommended_action_id="action-1",
        execution_route="safe_pr",
        selection_required=True,
        candidates=[candidate],
    )
    payload = plan.to_body()
    payload["lifecycle"] = {
        "phase": "failed",
        "attempt": {"id": "attempt-1", "number": 1},
        "pr": {
            "url": created_pr().pr_url,
            "repository_id": "repo-1",
            "repo_ref": "Jungle-303-04/game-server",
            "base_branch": "main",
            "binding_id": "binding-1",
            "application_id": "app-1",
            "cluster_id": TARGET["cluster_id"],
            "manifest_path": "k8s/api-server.yaml",
            "attempt_id": "attempt-1",
        },
        "merge": {
            "workflow_run_id": workflow_run_id,
            "binding_id": "binding-1",
            "application_id": "app-1",
            "cluster_id": TARGET["cluster_id"],
            "merge_commit_sha": MERGE_SHA,
            "retry_attempt": 0,
            "deployment_request": deployment.to_body(),
        },
        "verification": {
            "minimum_seconds": 300,
            "maximum_seconds": 600,
            "expected": {"replicas": 2},
            "before": {"alert_event_id": "alert-1"},
            "target": TARGET,
            "protected_session_baseline": [{"continuity_id": "room-a", "value": 1}],
            "status": "failed",
            "started_at": "2026-07-24T00:50:00+00:00",
            "deadline_at": "2026-07-24T01:00:00+00:00",
            "healthy_since": "2026-07-24T00:59:00+00:00",
            "distinct_evidence_count": 2,
            "last_evidence_key": "window-old",
            "after": {"failure_ratio": 0.4},
        },
        "failure": {
            "reason_code": reason_code,
            "reason": "stage failed",
            "workflow_run_id": workflow_run_id,
        },
    }
    return {
        "plan_id": "plan-1",
        "workspace_id": "workspace-1",
        "correlation_id": "correlation-1",
        "incident_id": "incident-1",
        "evidence_ref": "object://evidence/incident-1.json",
        "status": "failed",
        "selected_action_id": "action-1",
        "selected_by": "operator-1",
        "payload": payload,
    }


class RetryRouteDb:
    def __init__(self, *, reason_code: str, workflow_status: str) -> None:
        self.record = retry_record(reason_code=reason_code)
        self.workflow_status = workflow_status
        self.transitions: list[tuple[tuple[str, ...], str, dict[str, Any]]] = []
        self.approvals: list[dict[str, Any]] = []

    def get_recovery_plan_by_correlation(
        self,
        correlation_id: str,
        workspace_id: str,
    ) -> dict[str, Any]:
        assert (correlation_id, workspace_id) == ("correlation-1", "workspace-1")
        return self.record

    def get_workflow_run(self, workflow_run_id: str) -> dict[str, Any]:
        return {
            "workflow_run_id": workflow_run_id,
            "workspace_id": "workspace-1",
            "binding_id": "binding-1",
            "application_id": "app-1",
            "cluster_id": TARGET["cluster_id"],
            "commit_sha": MERGE_SHA,
            "status": self.workflow_status,
        }

    def current_database_time(self) -> datetime:
        return NOW

    def request_workflow_approval(self, payload: dict[str, Any]) -> dict[str, Any]:
        self.approvals.append(payload)
        return payload

    def update_recovery_plan_lifecycle_if_status(
        self,
        plan_id: str,
        workspace_id: str,
        *,
        expected_statuses: tuple[str, ...],
        status: str,
        lifecycle: dict[str, Any],
    ) -> dict[str, Any]:
        assert (plan_id, workspace_id) == ("plan-1", "workspace-1")
        self.transitions.append((expected_statuses, status, lifecycle))
        return {"plan_id": plan_id}


class RetryEvents:
    def __init__(self) -> None:
        self.calls: list[tuple[object, dict[str, object]]] = []

    async def accept_body(self, body: object, **kwargs: object) -> Accepted:
        self.calls.append((body, kwargs))
        return Accepted(body, str(kwargs["correlation_id"]))


def retry_current_user() -> SimpleNamespace:
    return SimpleNamespace(
        user_id="operator-1",
        workspace_id="workspace-1",
        roles=("release_operator",),
    )


def run_retry(
    db: RetryRouteDb,
    events: RetryEvents,
    monkeypatch: pytest.MonkeyPatch,
    *,
    preflight: object | None = None,
):
    monkeypatch.setattr(rca_router, "require_cluster_access", lambda *args, **kwargs: None)
    return asyncio.run(
        rca_router.retry_recovery_by_correlation(
            "correlation-1",
            RecoveryRetryRequest(
                expected_plan_id="plan-1",
                reason="operator requested retry",
            ),
            current=retry_current_user(),
            db=db,
            events=events,
            preflight=preflight,
        )
    )


class SafePrRetryPreflight:
    def __init__(self) -> None:
        self.calls: list[tuple[RecoveryActionSelectedBody, str]] = []

    async def prepare(
        self,
        evt: RecoveryActionSelectedBody,
        correlation_id: str,
    ) -> RecoveryActionCandidate:
        self.calls.append((evt, correlation_id))
        return replace(
            evt.selected,
            draft=replace(
                evt.selected.draft,
                params={
                    **evt.selected.draft.params,
                    "workspace_id": "workspace-1",
                    "repository_id": "repo-1",
                    "binding_id": "binding-1",
                    "application_id": "app-1",
                    "workflow_run_id": "workflow-latest",
                    "environment": "sandbox",
                    "manifest_path": "k8s/api-server.yaml",
                    "repo_ref": "Jungle-303-04/game-server",
                    "base_branch": "main",
                    "commit_sha": "d" * 40,
                },
            ),
        )


def test_safe_pr_failure_retry_refreshes_authority_and_redispatches_same_action(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = RetryRouteDb(reason_code="provider_error", workflow_status="succeeded")
    lifecycle = db.record["payload"]["lifecycle"]
    lifecycle["failure"] = {
        "stage": "safe_pr",
        "reason_code": "provider_error",
        "reason": "base advanced",
    }
    events = RetryEvents()
    preflight = SafePrRetryPreflight()

    response = run_retry(db, events, monkeypatch, preflight=preflight)

    assert response.accepted is True
    assert preflight.calls[0][1] == "correlation-1"
    assert db.transitions[0][0] == ("failed",)
    assert db.transitions[0][1] == "selected"
    lifecycle = db.transitions[0][2]
    assert lifecycle["retry"]["stage"] == "safe_pr"
    assert lifecycle["retry"]["previous_failure"]["reason_code"] == "provider_error"
    assert len(db.approvals) == 1
    assert db.approvals[0]["workflow_run_id"] == "workflow-latest"
    assert db.approvals[0]["approval_id"] != rca_router.recovery_approval_id(
        "plan-1", "action-1"
    )
    assert isinstance(events.calls[0][0], RecoveryRetryRequestedBody)
    assert isinstance(events.calls[1][0], RecoveryActionSelectedBody)
    selected = events.calls[1][0]
    assert selected.selected.draft.params["commit_sha"] == "d" * 40
    assert selected.selected.draft.params["approval_ref"] == db.approvals[0]["approval_id"]


def test_deploy_failure_retry_replays_exact_merge_request_with_new_workflow(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = RetryRouteDb(
        reason_code="recovery_deploy_failed",
        workflow_status="failed",
    )
    events = RetryEvents()

    response = run_retry(db, events, monkeypatch)

    assert response.accepted is True
    assert db.transitions[0][0] == ("failed",)
    assert db.transitions[0][1] == "deploy_pending"
    lifecycle = db.transitions[0][2]
    assert lifecycle["phase"] == "deploy_pending"
    assert "failure" not in lifecycle
    assert lifecycle["merge"]["previous_workflow_run_id"] == "workflow-original"
    new_workflow = lifecycle["merge"]["workflow_run_id"]
    assert new_workflow != "workflow-original"
    assert lifecycle["merge"]["deployment_request"]["workflow_run_id"] == new_workflow
    assert lifecycle["merge"]["deployment_request"]["commit_sha"] == MERGE_SHA
    assert lifecycle["merge"]["deployment_request"]["force"] is True
    assert isinstance(events.calls[0][0], RecoveryRetryRequestedBody)
    replay = events.calls[1][0]
    assert isinstance(replay, GitWebhookReceivedBody)
    assert replay.workflow_run_id == new_workflow
    assert replay.binding_id == "binding-1"
    assert replay.cluster_id == TARGET["cluster_id"]


def test_expired_verification_retry_keeps_deployment_and_restarts_only_window(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = RetryRouteDb(
        reason_code="verification_window_expired",
        workflow_status="succeeded",
    )
    events = RetryEvents()

    run_retry(db, events, monkeypatch)

    assert db.transitions[0][1] == "verification_pending"
    lifecycle = db.transitions[0][2]
    assert lifecycle["merge"]["workflow_run_id"] == "workflow-original"
    assert lifecycle["verification"]["started_at"] == NOW.isoformat()
    assert lifecycle["verification"]["deadline_at"] == (
        "2026-07-24T01:10:00+00:00"
    )
    assert lifecycle["verification"]["distinct_evidence_count"] == 0
    assert lifecycle["verification"]["last_evidence_key"] is None
    assert lifecycle["verification"]["after"] == {}
    assert len(events.calls) == 1
    assert isinstance(events.calls[0][0], RecoveryRetryRequestedBody)


def test_expired_verification_retry_recovers_legacy_missing_failure_metadata(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = RetryRouteDb(
        reason_code="verification_window_expired",
        workflow_status="succeeded",
    )
    lifecycle = db.record["payload"]["lifecycle"]
    lifecycle.pop("failure")
    lifecycle["verification"]["last_reason_code"] = "verification_window_expired"
    events = RetryEvents()

    run_retry(db, events, monkeypatch)

    assert db.transitions[0][1] == "verification_pending"
    assert db.transitions[0][2]["verification"]["last_reason_code"] == (
        "waiting_for_post_deploy_evidence"
    )
    assert isinstance(events.calls[0][0], RecoveryRetryRequestedBody)


def test_deploy_retry_rejects_tampered_stored_request(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    db = RetryRouteDb(
        reason_code="recovery_deploy_failed",
        workflow_status="failed",
    )
    request = db.record["payload"]["lifecycle"]["merge"]["deployment_request"]
    request["cluster_id"] = "other-cluster"
    events = RetryEvents()

    with pytest.raises(HTTPException) as raised:
        run_retry(db, events, monkeypatch)

    assert raised.value.status_code == 409
    assert raised.value.detail == "recovery retry identity is invalid"
    assert db.transitions == []
    assert events.calls == []
