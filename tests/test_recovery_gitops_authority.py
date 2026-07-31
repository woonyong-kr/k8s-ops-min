from __future__ import annotations

import asyncio

import pytest
from conftest import load_service, make_context

from domains.gitops.source_patch import canonical_manifest_digest
from domains.rca.events import (
    EvidenceBundle,
    EvidenceItem,
    HealingActionDraft,
    IncidentRecord,
    RcaCompletedBody,
    RcaReportDetail,
    RecoveryActionCandidate,
    RecoveryActionSelectedBody,
    RecoveryPlan,
)
from packages.contracts.gitops_authority import GitOpsAuthorityQuery
from services.ai.agent.playbooks.recovery import RecoveryContext
from services.ai.agent.recovery.authority import DatabaseGitOpsAuthorityReadPort
from services.ai.agent.recovery.dispatch import authority_query
from services.ai.agent.workload_target import (
    WORKLOAD_SNAPSHOT_SOURCE,
    resolve_workload_target,
)


def snapshot(
    deployment: str,
    *,
    namespace: str = "sandbox",
    replicasets: tuple[str, ...] = (),
    pods: tuple[str, ...] = (),
) -> dict[str, object]:
    return {
        "workload": {
            "kind": "Deployment",
            "namespace": namespace,
            "name": deployment,
        },
        "replicaset_revisions": [{"name": value} for value in replicasets],
        "pod_statuses": [{"name": value} for value in pods],
    }


def test_workload_target_requires_one_explicit_controller_owner() -> None:
    metadata = {
        "change_context": {
            "current_workload_snapshots": [
                snapshot(
                    "game-room-0",
                    replicasets=("game-room-0-774544b4fb",),
                    pods=("game-room-0-774544b4fb-pwrwh",),
                )
            ]
        }
    }

    replicaset = resolve_workload_target(
        "sandbox",
        "ReplicaSet",
        "game-room-0-774544b4fb",
        metadata,
    )
    pod = resolve_workload_target(
        "sandbox",
        "Pod",
        "game-room-0-774544b4fb-pwrwh",
        metadata,
    )

    assert replicaset.identity() == {
        "namespace": "sandbox",
        "resource_kind": "Deployment",
        "resource_name": "game-room-0",
    }
    assert pod.identity() == replicaset.identity()
    assert replicaset.original_identity()["resource_kind"] == "ReplicaSet"
    assert replicaset.resolution_source == WORKLOAD_SNAPSHOT_SOURCE


def test_workload_target_fails_closed_for_ambiguous_or_prefix_only_snapshots() -> None:
    ambiguous = {
        "current_workload_snapshots": [
            snapshot("one", replicasets=("shared-rs",)),
            snapshot("two", replicasets=("shared-rs",)),
        ]
    }
    prefix_only = {
        "current_workload_snapshots": [
            snapshot("game-room-0"),
        ]
    }
    duplicate = {
        "current_workload_snapshots": [
            snapshot("one", replicasets=("duplicated-rs",)),
            snapshot("one", replicasets=("duplicated-rs",)),
        ]
    }

    assert not resolve_workload_target(
        "sandbox", "ReplicaSet", "shared-rs", ambiguous
    ).resolved
    assert not resolve_workload_target(
        "sandbox", "ReplicaSet", "game-room-0-774544b4fb", prefix_only
    ).resolved
    assert not resolve_workload_target(
        "sandbox", "ReplicaSet", "duplicated-rs", duplicate
    ).resolved


class EvidenceDb:
    def __init__(self) -> None:
        self.calls: list[tuple[object, ...]] = []

    async def list_recent_workload_changes_for_evidence(self, *args: object, **kwargs: object):
        self.calls.append((*args, kwargs))
        return [
            {
                "workspace_id": "workspace-1",
                "cluster_id": "cluster-1",
                "namespace": "sandbox",
                "resource_kind": "deployment",
                "resource_name": "game-room-0",
                "repository_id": "repo-1",
                "binding_id": "binding-1",
                "repo_ref": "org/repo",
                "manifest_path": "deploy/game.yaml",
                "commit_sha": "a" * 40,
                "workflow_run_id": "run-1",
            }
        ]


def test_evidence_worker_uses_resolved_deployment_and_keeps_original_lineage() -> None:
    worker = load_service("ai/evidence-worker")
    db = EvidenceDb()
    evt = worker.ClusterEvidenceReceivedBody(
        workspace_id="workspace-1",
        cluster_id="cluster-1",
        kubernetes={
            "resource": {
                "namespace": "sandbox",
                "kind": "ReplicaSet",
                "name": "game-room-0-774544b4fb",
            }
        },
        metrics={},
        logs=[],
        traces={},
        window_start="2026-07-24T00:00:00Z",
        metadata={
            "current_workload_snapshots": [
                snapshot(
                    "game-room-0",
                    replicasets=("game-room-0-774544b4fb",),
                )
            ],
            "change_context": {
                "gitops": {"binding_id": "agent-controlled"},
                "gitops_target": {
                    "namespace": "sandbox",
                    "resource_kind": "StatefulSet",
                    "resource_name": "tampered",
                },
                "original_target": {
                    "namespace": "sandbox",
                    "resource_kind": "ReplicaSet",
                    "resource_name": "tampered",
                },
                "gitops_target_resolution": WORKLOAD_SNAPSHOT_SOURCE,
            },
        },
    )

    hydrated = asyncio.run(
        worker.attach_gitops_change_context(evt, make_context(db=db))
    )

    assert db.calls[0][2:5] == ("sandbox", "Deployment", "game-room-0")
    context = hydrated.metadata["change_context"]
    assert context["gitops_target"] == {
        "namespace": "sandbox",
        "resource_kind": "Deployment",
        "resource_name": "game-room-0",
    }
    assert context["original_target"] == {
        "namespace": "sandbox",
        "resource_kind": "ReplicaSet",
        "resource_name": "game-room-0-774544b4fb",
    }
    assert context["gitops"]["binding_id"] == "binding-1"
    assert context["gitops_target_resolution"] == WORKLOAD_SNAPSHOT_SOURCE
    assert context["original_target"]["resource_name"] == "game-room-0-774544b4fb"


def report_with_controller_snapshot() -> RcaCompletedBody:
    incident = IncidentRecord(
        incident_id="incident-1",
        cluster_id="cluster-1",
        resource_kind="ReplicaSet",
        resource_name="game-room-0-774544b4fb",
        namespace="sandbox",
        symptom="Readiness probe response failure",
        severity="warning",
        first_seen_at="2026-07-24T00:00:00Z",
        summary="unready",
        workspace_id="workspace-1",
    )
    detail = RcaReportDetail(
        root_cause="probe_path_wrong",
        confidence=1.0,
        selected_candidate_id="probe_path_wrong",
        supporting_evidence=[],
        missing_evidence=[],
        reason="probe mismatch",
    )
    return RcaCompletedBody(
        root_cause=detail.root_cause,
        action="fix probe",
        evidence_ref="object://evidence/correlation-1.json",
        workspace_id="workspace-1",
        incident=incident,
        rca_detail=detail,
        evidence_bundle=EvidenceBundle(
            incident_id=incident.incident_id,
            items=[
                EvidenceItem(
                    source="metadata",
                    name="change_context",
                    value={
                        "current_workload_snapshots": [
                            snapshot(
                                "game-room-0",
                                replicasets=("game-room-0-774544b4fb",),
                            )
                        ]
                    },
                    summary="workload ownership",
                )
            ],
            missing_evidence=[],
            complete=True,
        ),
    )


def report_with_promoted_controller_snapshot() -> RcaCompletedBody:
    report = report_with_controller_snapshot()
    assert report.evidence_bundle is not None
    snapshots = report.evidence_bundle.items[0].value["current_workload_snapshots"]
    return RcaCompletedBody(
        root_cause=report.root_cause,
        action=report.action,
        evidence_ref=report.evidence_ref,
        workspace_id=report.workspace_id,
        incident=report.incident,
        rca_detail=report.rca_detail,
        evidence_bundle=EvidenceBundle(
            incident_id=report.evidence_bundle.incident_id,
            items=[
                EvidenceItem(
                    source="metadata",
                    name="current_workload_snapshots",
                    value={"items": snapshots},
                    summary="promoted workload ownership",
                )
            ],
            missing_evidence=[],
            complete=True,
        ),
    )


def selected_event(target: dict[str, object]) -> RecoveryActionSelectedBody:
    draft = HealingActionDraft(
        action_type="probe_fix",
        namespace="sandbox",
        resource_kind="ReplicaSet",
        resource_name="game-room-0-774544b4fb",
        reason="probe mismatch",
        risk_level="medium",
        dry_run=True,
        source_evidence=[],
        params={},
    )
    candidate = RecoveryActionCandidate(
        action_id="action-1",
        title="fix",
        description="fix",
        draft=draft,
        route="draft_pr",
        rank=1,
        score=1.0,
        risk_level="medium",
        blast_radius="one workload",
        approval_required=True,
        prerequisites=[],
        validation_checks=[],
        rollback_plan="revert",
        evidence_refs=[],
    )
    return RecoveryActionSelectedBody(
        plan=RecoveryPlan(
            plan_id="plan-1",
            incident_id="incident-1",
            evidence_ref="object://evidence/correlation-1.json",
            summary="summary",
            target=target,
            recommended_action_id=candidate.action_id,
            execution_route=candidate.route,
            selection_required=True,
            candidates=[candidate],
        ),
        selected=candidate,
        selected_by="user-1",
        auto_selected=False,
        reason="approved",
        workspace_id="workspace-1",
    )


def test_recovery_plan_and_dispatch_share_the_resolved_authority_target() -> None:
    report = report_with_controller_snapshot()
    context = RecoveryContext(
        report=report,
        incident=report.incident,  # type: ignore[arg-type]
        detail=report.rca_detail,  # type: ignore[arg-type]
        evidence_ref=report.evidence_ref,
    )

    target = context.target
    query = authority_query(selected_event(target), "correlation-1")

    assert target["original_target"]["resource_kind"] == "ReplicaSet"
    assert query.resource_kind == "Deployment"
    assert query.resource_name == "game-room-0"


def test_recovery_plan_resolves_controller_from_promoted_snapshot_evidence() -> None:
    report = report_with_promoted_controller_snapshot()
    context = RecoveryContext(
        report=report,
        incident=report.incident,  # type: ignore[arg-type]
        detail=report.rca_detail,  # type: ignore[arg-type]
        evidence_ref=report.evidence_ref,
    )

    target = context.target
    query = authority_query(selected_event(target), "correlation-1")

    assert target["original_target"]["resource_kind"] == "ReplicaSet"
    assert query.resource_kind == "Deployment"
    assert query.resource_name == "game-room-0"


def manifest(kind: str = "Deployment", name: str = "checkout") -> dict[str, object]:
    return {
        "apiVersion": "apps/v1",
        "kind": kind,
        "metadata": {"name": name, "namespace": "sandbox"},
        "spec": {
            "replicas": 1,
            "template": {
                "spec": {
                    "containers": [
                        {
                            "name": name,
                            "image": "example/app:v2",
                        }
                    ]
                }
            },
        },
    }


def exact_identity(kind: str = "Deployment", name: str = "checkout") -> dict[str, object]:
    return {
        "workspace_id": "workspace-1",
        "repository_id": "repo-1",
        "binding_id": "binding-1",
        "application_id": "app-1",
        "workflow_run_id": "run-1",
        "environment": "sandbox",
        "cluster_id": "cluster-1",
        "commit_sha": "a" * 40,
        "manifest_path": "deploy/app.yaml",
        "repo_ref": "org/repo",
        "branch": "dev",
        "resource": f"{kind}/{name}",
    }


def fallback_evidence(
    kind: str = "Deployment",
    name: str = "checkout",
) -> dict[str, object]:
    identity = exact_identity(kind, name)
    return {
        "workspace_id": "workspace-1",
        "cluster_id": "cluster-1",
        "kubernetes": {
            "resource": {
                "namespace": "sandbox",
                "kind": kind,
                "name": name,
            }
        },
        "metadata": {
            "change_context": {
                "gitops": {
                    "workspace_id": identity["workspace_id"],
                    "cluster_id": identity["cluster_id"],
                    "namespace": "sandbox",
                    "resource_kind": kind.casefold(),
                    "resource_name": name,
                    "repository_id": identity["repository_id"],
                    "binding_id": identity["binding_id"],
                    "workflow_run_id": identity["workflow_run_id"],
                    "commit_sha": identity["commit_sha"],
                    "manifest_path": identity["manifest_path"],
                    "repo_ref": identity["repo_ref"],
                }
            }
        },
    }


class AuthorityDb:
    def __init__(
        self,
        *,
        exact: dict[str, object] | None = None,
        evidence: dict[str, object] | None = None,
        kind: str = "Deployment",
        name: str = "checkout",
    ) -> None:
        self.exact = exact
        self.evidence = evidence or fallback_evidence(kind, name)
        self.kind = kind
        self.name = name
        self.calls: list[str] = []
        self.identity = exact_identity(kind, name)
        self.desired = manifest(kind, name)
        self.digest = canonical_manifest_digest(self.desired)

    async def get_evidence_payload(
        self,
        workspace_id: str,
        correlation_id: str,
        kind: str,
    ) -> dict[str, object] | None:
        self.calls.append(kind)
        return self.exact if kind == "gitops_change_context" else self.evidence

    async def get_workflow_run(self, workflow_run_id: str) -> dict[str, object]:
        return {
            "workflow_run_id": workflow_run_id,
            "workspace_id": "workspace-1",
            "application_id": "app-1",
            "binding_id": "binding-1",
            "environment": "sandbox",
            "cluster_id": "cluster-1",
            "commit_sha": "a" * 40,
        }

    async def get_workflow_step_details(
        self,
        workflow_run_id: str,
        name: str,
    ) -> dict[str, object]:
        return {
            **self.identity,
            "namespace": "sandbox",
            "resource": f"{self.kind}/{self.name}",
            "desired_manifest": self.desired,
            "basis": {
                "artifact_digest": self.digest,
                "old_desired_source": "last_approved_snapshot",
            },
            "changes": [],
        }

    async def get_completed_workload_resource_diff(
        self,
        workspace_id: str,
        workflow_run_id: str,
        binding_id: str,
        cluster_id: str,
        namespace: str,
        resource_kind: str,
        resource_name: str,
    ) -> dict[str, object]:
        diff = {
            **self.identity,
            "namespace": namespace,
            "resource": f"{resource_kind}/{resource_name}",
            "desired_manifest": self.desired,
            "basis": {
                "artifact_digest": self.digest,
                "old_desired_source": "last_approved_snapshot",
            },
            "changes": [],
        }
        return {
            "workspace_id": workspace_id,
            "workflow_run_id": workflow_run_id,
            "binding_id": binding_id,
            "cluster_id": cluster_id,
            "namespace": namespace,
            "resource_kind": resource_kind.casefold(),
            "resource_name": resource_name,
            "repository_id": "repo-1",
            "manifest_path": "deploy/app.yaml",
            "commit_sha": "a" * 40,
            "diff_details": diff,
        }

    async def get_application(
        self,
        workspace_id: str,
        application_id: str,
    ) -> dict[str, object]:
        return {
            "workspace_id": workspace_id,
            "application_id": application_id,
            "repository_id": "repo-1",
            "manifest_path": "deploy/app.yaml",
            "repo_ref": "org/repo",
            "default_branch": "dev",
            "status": "active",
        }

    async def get_deployment_binding(
        self,
        workspace_id: str,
        binding_id: str,
    ) -> dict[str, object]:
        return {
            "workspace_id": workspace_id,
            "binding_id": binding_id,
            "repository_id": "repo-1",
            "cluster_id": "cluster-1",
            "namespace": "sandbox",
            "manifest_path": "deploy/app.yaml",
            "environment": "sandbox",
            "status": "active",
        }

    async def get_repository_by_ref(
        self,
        workspace_id: str,
        repo_ref: str,
    ) -> dict[str, object]:
        return {
            "workspace_id": workspace_id,
            "repository_id": "repo-1",
            "repo_ref": repo_ref,
            "default_branch": "dev",
            "status": "active",
        }

    async def get_manifest_artifact_provenance(self, *args: object) -> dict[str, object]:
        return {
            **self.identity,
            "artifact_digest": self.digest,
            "desired_manifest": self.desired,
            "source_type": "raw-yaml",
            "source_origin": "git_cache",
            "source_is_file": True,
            "source_document_count": 1,
            "artifact_count": 1,
            "source_manifest_sha256": "sha256:" + "b" * 64,
        }

    async def list_active_github_poll_targets(
        self,
        workspace_id: str,
        *,
        limit: int,
    ) -> list[dict[str, object]]:
        return [
            {
                "workspace_id": workspace_id,
                "application_id": "app-1",
                "repository_id": "repo-1",
                "repo_ref": "org/repo",
                "branch": "dev",
                "binding_id": "binding-1",
                "environment": "sandbox",
                "cluster_id": "cluster-1",
                "manifest_path": "deploy/app.yaml",
            }
        ]

    async def get_last_approved_resource_snapshot(
        self,
        workspace_id: str,
        binding_id: str,
        cluster_id: str,
        namespace: str,
        resource: str,
    ) -> dict[str, object]:
        kind, name = resource.split("/", 1)
        return {
            "workspace_id": workspace_id,
            "binding_id": binding_id,
            "cluster_id": cluster_id,
            "namespace": namespace,
            "resource_kind": kind.casefold(),
            "resource_name": name,
            "workflow_run_id": "run-1",
            "commit_sha": "a" * 40,
            "artifact_digest": self.digest,
            "snapshot": {
                "resource": resource,
                "namespace": namespace,
                "fields": {"spec.replicas": 1},
            },
        }

    async def list_recent_completed_workload_resource_diffs(
        self,
        workspace_id: str,
        binding_id: str,
        cluster_id: str,
        namespace: str,
        resource_kind: str,
        resource_name: str,
        *,
        limit: int,
    ) -> list[dict[str, object]]:
        return []


def query(kind: str = "Deployment", name: str = "checkout") -> GitOpsAuthorityQuery:
    return GitOpsAuthorityQuery(
        correlation_id="correlation-1",
        workspace_id="workspace-1",
        incident_id="incident-1",
        cluster_id="cluster-1",
        namespace="sandbox",
        resource_kind=kind,
        resource_name=name,
    )


def load_authority(db: AuthorityDb, authority_query_value: GitOpsAuthorityQuery):
    return asyncio.run(DatabaseGitOpsAuthorityReadPort(db).load_authority(authority_query_value))


def test_authority_uses_rca_bundle_identity_when_exact_correlation_is_absent() -> None:
    db = AuthorityDb()

    authority = load_authority(db, query())

    assert authority is not None
    assert authority.resource == "Deployment/checkout"
    assert db.calls == ["gitops_change_context", "rca_bundle"]


def test_authority_uses_current_approved_snapshot_when_incident_has_no_recent_change() -> None:
    db = AuthorityDb(evidence={"metadata": {}})

    async def no_recent_diff(*args: object) -> None:
        return None

    db.get_completed_workload_resource_diff = no_recent_diff  # type: ignore[method-assign]

    authority = load_authority(db, query())

    assert authority is not None
    assert authority.binding_id == "binding-1"
    assert authority.manifest_path == "deploy/app.yaml"
    assert authority.resource == "Deployment/checkout"
    assert authority.desired_manifest == db.desired
    assert authority.changes == ()


def test_approved_snapshot_authority_survives_workflow_history_retention() -> None:
    db = AuthorityDb(evidence={"metadata": {}})

    async def missing_retained_workflow(*args: object) -> None:
        return None

    async def no_recent_diff(*args: object) -> None:
        return None

    db.get_workflow_run = missing_retained_workflow  # type: ignore[method-assign]
    db.get_completed_workload_resource_diff = no_recent_diff  # type: ignore[method-assign]

    authority = load_authority(db, query())

    assert authority is not None
    assert authority.workflow_run_id == "run-1"
    assert authority.binding_id == "binding-1"
    assert authority.desired_manifest == db.desired


def test_authority_uses_binding_manifest_path_when_application_default_differs() -> None:
    db = AuthorityDb(evidence={"metadata": {}})

    async def no_recent_diff(*args: object) -> None:
        return None

    async def application_with_repository_default_path(
        workspace_id: str,
        application_id: str,
    ) -> dict[str, object]:
        return {
            "workspace_id": workspace_id,
            "application_id": application_id,
            "repository_id": "repo-1",
            "manifest_path": "deploy/base",
            "repo_ref": "org/repo",
            "default_branch": "dev",
            "status": "active",
        }

    db.get_completed_workload_resource_diff = no_recent_diff  # type: ignore[method-assign]
    db.get_application = application_with_repository_default_path  # type: ignore[method-assign]

    authority = load_authority(db, query())

    assert authority is not None
    assert authority.binding_id == "binding-1"
    assert authority.manifest_path == "deploy/app.yaml"


def test_authority_enriches_approved_snapshot_with_latest_replica_change() -> None:
    db = AuthorityDb(evidence={"metadata": {}})

    async def no_current_workflow_diff(*args: object) -> None:
        return None

    async def recent_diffs(
        workspace_id: str,
        binding_id: str,
        cluster_id: str,
        namespace: str,
        resource_kind: str,
        resource_name: str,
        *,
        limit: int,
    ) -> list[dict[str, object]]:
        return [
            {
                "workspace_id": workspace_id,
                "binding_id": binding_id,
                "cluster_id": cluster_id,
                "namespace": namespace,
                "resource_kind": resource_kind,
                "resource_name": resource_name,
                "repository_id": "repo-1",
                "manifest_path": "deploy/app.yaml",
                "diff_details": {
                    "changes": [
                        {
                            "field_path": "spec.replicas",
                            "old_desired": 2,
                            "new_desired": 1,
                        }
                    ]
                },
            }
        ]

    db.get_completed_workload_resource_diff = no_current_workflow_diff  # type: ignore[method-assign]
    db.list_recent_completed_workload_resource_diffs = recent_diffs  # type: ignore[method-assign]

    authority = load_authority(db, query())

    assert authority is not None
    assert authority.changes == (
        {
            "field_path": "spec.replicas",
            "old_desired": 2,
            "new_desired": 1,
        },
    )


def test_authority_ignores_ambiguous_replica_history() -> None:
    db = AuthorityDb(evidence={"metadata": {}})

    async def no_current_workflow_diff(*args: object) -> None:
        return None

    async def ambiguous_diffs(*args: object, **kwargs: object) -> list[dict[str, object]]:
        return [
            {
                "workspace_id": "workspace-1",
                "binding_id": "binding-1",
                "cluster_id": "cluster-1",
                "namespace": "sandbox",
                "resource_kind": "Deployment",
                "resource_name": "checkout",
                "repository_id": "repo-1",
                "manifest_path": "deploy/app.yaml",
                "diff_details": {
                    "changes": [
                        {
                            "field_path": "spec.replicas",
                            "old_desired": 2,
                            "new_desired": 1,
                        },
                        {
                            "field_path": "spec.replicas",
                            "old_desired": 3,
                            "new_desired": 1,
                        },
                    ]
                },
            }
        ]

    db.get_completed_workload_resource_diff = no_current_workflow_diff  # type: ignore[method-assign]
    db.list_recent_completed_workload_resource_diffs = ambiguous_diffs  # type: ignore[method-assign]

    authority = load_authority(db, query())

    assert authority is not None
    assert authority.changes == ()


def test_authority_rejects_ambiguous_current_approved_snapshot_bindings() -> None:
    db = AuthorityDb(evidence={"metadata": {}})
    list_targets = db.list_active_github_poll_targets

    async def ambiguous_targets(
        workspace_id: str,
        *,
        limit: int,
    ) -> list[dict[str, object]]:
        first = (await list_targets(workspace_id, limit=limit))[0]
        return [first, {**first, "binding_id": "binding-2"}]

    db.list_active_github_poll_targets = ambiguous_targets  # type: ignore[method-assign]

    assert load_authority(db, query()) is None


def test_authority_uses_exact_resource_diff_not_singleton_workflow_step() -> None:
    db = AuthorityDb()

    async def wrong_singleton(*args: object) -> dict[str, object]:
        return {
            **db.identity,
            "namespace": "sandbox",
            "resource": "Service/unrelated",
            "desired_manifest": manifest("Service", "unrelated"),
            "basis": {"artifact_digest": "wrong"},
            "changes": [],
        }

    db.get_workflow_step_details = wrong_singleton  # type: ignore[method-assign]

    authority = load_authority(db, query())

    assert authority is not None
    assert authority.resource == "Deployment/checkout"


def test_invalid_exact_authority_cannot_be_bypassed_by_valid_rca_fallback() -> None:
    invalid_exact = exact_identity()
    invalid_exact["binding_id"] = ""
    db = AuthorityDb(exact=invalid_exact)

    assert load_authority(db, query()) is None


def test_rca_fallback_fails_closed_when_embedded_identity_is_tampered() -> None:
    evidence = fallback_evidence()
    evidence["metadata"]["change_context"]["gitops"]["binding_id"] = "tampered"  # type: ignore[index]
    db = AuthorityDb(evidence=evidence)

    assert load_authority(db, query()) is None


def test_rca_fallback_accepts_verified_replicaset_to_deployment_lineage() -> None:
    evidence = fallback_evidence()
    evidence["kubernetes"] = {
        "resource": {
            "namespace": "sandbox",
            "kind": "ReplicaSet",
            "name": "checkout-774544b4fb",
        }
    }
    context = evidence["metadata"]["change_context"]  # type: ignore[index]
    context.update(  # type: ignore[union-attr]
        {
            "gitops_target": {
                "namespace": "sandbox",
                "resource_kind": "Deployment",
                "resource_name": "checkout",
            },
            "original_target": {
                "namespace": "sandbox",
                "resource_kind": "ReplicaSet",
                "resource_name": "checkout-774544b4fb",
            },
            "gitops_target_resolution": WORKLOAD_SNAPSHOT_SOURCE,
        }
    )
    evidence["metadata"]["current_workload_snapshots"] = [  # type: ignore[index]
        snapshot(
            "checkout",
            replicasets=("checkout-774544b4fb",),
        )
    ]
    db = AuthorityDb(evidence=evidence)

    authority = load_authority(db, query())

    assert authority is not None
    assert authority.resource == "Deployment/checkout"


def test_authority_accepts_exact_query_manifest_kind_not_only_deployment() -> None:
    db = AuthorityDb(kind="StatefulSet", name="tempo")

    authority = load_authority(db, query("StatefulSet", "tempo"))

    assert authority is not None
    assert authority.desired_manifest["kind"] == "StatefulSet"


def test_authority_rejects_desired_manifest_kind_that_differs_from_query() -> None:
    db = AuthorityDb(kind="StatefulSet", name="tempo")
    db.desired = manifest("Deployment", "tempo")
    db.digest = canonical_manifest_digest(db.desired)

    assert load_authority(db, query("StatefulSet", "tempo")) is None


@pytest.mark.parametrize("source_origin", ["git_cache", "github_tree"])
def test_authority_accepts_commit_pinned_kustomize_render_provenance(
    source_origin: str,
) -> None:
    db = AuthorityDb()

    async def kustomize_provenance(*args: object) -> dict[str, object]:
        return {
            **db.identity,
            "artifact_digest": db.digest,
            "source_type": "kustomize",
            "source_origin": source_origin,
            "source_is_file": False,
            "source_document_count": 3,
            "artifact_count": 3,
            "source_manifest_sha256": db.digest,
        }

    db.get_manifest_artifact_provenance = kustomize_provenance  # type: ignore[method-assign]

    authority = load_authority(db, query())

    assert authority is not None
    # The semantic patch remains raw-yaml; SCM independently resolves the
    # Kustomize graph to one exact raw object file at this commit.
    assert authority.source_type == "raw-yaml"
    assert authority.manifest_path == "deploy/app.yaml"


def test_authority_rejects_incomplete_kustomize_render_provenance() -> None:
    db = AuthorityDb()

    async def truncated_kustomize_provenance(*args: object) -> dict[str, object]:
        return {
            **db.identity,
            "artifact_digest": db.digest,
            "source_type": "kustomize",
            "source_origin": "git_cache",
            "source_is_file": False,
            "source_document_count": 3,
            "artifact_count": 2,
            "source_manifest_sha256": db.digest,
        }

    db.get_manifest_artifact_provenance = truncated_kustomize_provenance  # type: ignore[method-assign]

    assert load_authority(db, query()) is None
