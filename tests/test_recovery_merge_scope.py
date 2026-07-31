from __future__ import annotations

from copy import deepcopy
from dataclasses import replace

from domains.gitops.events import Diff
from domains.gitops.recovery_merge import recovery_diff_matches_approved_scope
from packages.config.constants import RiskLevel


def recovery_payload() -> dict[str, object]:
    return {
        "target": {
            "cluster_id": "cluster-a",
            "namespace": "sandbox",
            "resource_kind": "Deployment",
            "resource_name": "api-server",
        },
        "lifecycle": {
            "authorization": {
                "target": {
                    "cluster_id": "cluster-a",
                    "namespace": "sandbox",
                    "resource_kind": "Deployment",
                    "resource_name": "api-server",
                },
                "changes": [
                    {
                        "field_path": "spec.replicas",
                        "current_value": 1,
                        "desired_value": 2,
                    }
                ],
            },
            "verification": {
                "target": {
                    "cluster_id": "cluster-a",
                    "namespace": "sandbox",
                    "resource_kind": "Deployment",
                    "resource_name": "api-server",
                },
                "expected": {"replicas": 2},
            }
        },
    }


def recovery_diff() -> Diff:
    return Diff(
        resource="deployment/api-server",
        namespace="sandbox",
        desired_image="example/game:v2",
        actual_image="example/game:v2",
        risk=RiskLevel.REVIEW_REQUIRED,
        workspace_id="workspace-a",
        repository_id="repository-a",
        watch_target_id="watch-a",
        binding_id="binding-a",
        application_id="application-a",
        workflow_run_id="workflow-a",
        environment="production",
        cluster_id="cluster-a",
        manifest_path="deploy/k8s",
        desired_manifest={
            "apiVersion": "apps/v1",
            "kind": "Deployment",
            "metadata": {"name": "api-server", "namespace": "sandbox"},
            "spec": {
                "replicas": 2,
                "template": {
                    "spec": {
                        "containers": [
                            {"name": "api-server", "image": "example/game:v2"}
                        ]
                    }
                },
            },
        },
        status="intended_change",
        has_changes=True,
        changes=[
            {
                "field_path": "spec.replicas",
                "classification": "intended_change",
                "old_desired": 1,
                "live": 1,
                "new_desired": 2,
                "before": 1,
                "after": 2,
            }
        ],
        basis={"artifact_digest": "sha256:" + "a" * 64},
    )


def test_exact_replica_recovery_diff_matches_approved_scope() -> None:
    assert recovery_diff_matches_approved_scope(recovery_payload(), recovery_diff()) is True


def test_exact_replica_recovery_accepts_already_converged_live_state() -> None:
    diff = replace(
        recovery_diff(),
        status="already_converged",
        has_changes=False,
        changes=[
            {
                "field_path": "spec.replicas",
                "classification": "already_converged",
                "old_desired": 1,
                "live": 2,
                "new_desired": 2,
                "before": 2,
                "after": 2,
            }
        ],
    )

    assert recovery_diff_matches_approved_scope(recovery_payload(), diff) is True


def test_exact_replica_recovery_accepts_no_change_after_snapshot_advance() -> None:
    diff = replace(
        recovery_diff(),
        status="no_change",
        has_changes=False,
        changes=[],
    )

    assert recovery_diff_matches_approved_scope(recovery_payload(), diff) is True


def test_unrelated_resource_in_same_workflow_fails_closed() -> None:
    diff = replace(
        recovery_diff(),
        resource="rolebinding/api-server",
        desired_manifest={
            "apiVersion": "rbac.authorization.k8s.io/v1",
            "kind": "RoleBinding",
            "metadata": {"name": "api-server", "namespace": "sandbox"},
        },
    )

    assert recovery_diff_matches_approved_scope(recovery_payload(), diff) is False


def test_extra_actionable_change_in_target_workload_fails_closed() -> None:
    diff = recovery_diff()
    diff = replace(
        diff,
        changes=[
            *diff.changes,
            {
                "field_path": "spec.template.spec.containers[name=api-server].image",
                "classification": "intended_change",
                "before": "example/game:v1",
                "after": "example/game:v2",
            },
        ],
    )

    assert recovery_diff_matches_approved_scope(recovery_payload(), diff) is False


def test_replica_value_must_match_persisted_verification_contract() -> None:
    diff = recovery_diff()
    desired = deepcopy(diff.desired_manifest)
    desired["spec"]["replicas"] = 3  # type: ignore[index]
    diff = replace(
        diff,
        desired_manifest=desired,
        changes=[
            {
                "field_path": "spec.replicas",
                "classification": "intended_change",
                "old_desired": 1,
                "live": 1,
                "new_desired": 3,
                "before": 1,
                "after": 3,
            }
        ],
    )

    assert recovery_diff_matches_approved_scope(recovery_payload(), diff) is False


def test_missing_authorized_change_contract_fails_closed() -> None:
    payload = recovery_payload()
    lifecycle = payload["lifecycle"]
    assert isinstance(lifecycle, dict)
    lifecycle.pop("authorization")

    assert recovery_diff_matches_approved_scope(payload, recovery_diff()) is False


def test_image_recovery_uses_the_same_authorized_change_contract() -> None:
    payload = recovery_payload()
    authorization = payload["lifecycle"]["authorization"]  # type: ignore[index]
    assert isinstance(authorization, dict)
    authorization["changes"] = [
        {
            "field_path": "spec.template.spec.containers[name=api-server].image",
            "current_value": "example/game:v1",
            "desired_value": "example/game:v2",
        }
    ]
    diff = recovery_diff()
    diff = replace(
        diff,
        actual_image="example/game:v1",
        changes=[
            {
                "field_path": "spec.template.spec.containers[name=api-server].image",
                "classification": "intended_change",
                "old_desired": "example/game:v1",
                "live": "example/game:v1",
                "new_desired": "example/game:v2",
                "before": "example/game:v1",
                "after": "example/game:v2",
            }
        ],
    )

    assert recovery_diff_matches_approved_scope(payload, diff) is True


def test_parent_object_change_rejects_an_unapproved_sibling_mutation() -> None:
    payload = recovery_payload()
    authorization = payload["lifecycle"]["authorization"]  # type: ignore[index]
    assert isinstance(authorization, dict)
    authorization["changes"] = [
        {
            "field_path": (
                "spec.template.spec.containers[name=api-server]"
                ".resources.requests.memory"
            ),
            "current_value": "128Mi",
            "desired_value": "256Mi",
        }
    ]
    diff = recovery_diff()
    desired = deepcopy(diff.desired_manifest)
    container = desired["spec"]["template"]["spec"]["containers"][0]  # type: ignore[index]
    container["resources"] = {
        "requests": {"memory": "256Mi", "cpu": "200m"},
    }
    before = {"requests": {"memory": "128Mi", "cpu": "100m"}}
    after = {"requests": {"memory": "256Mi", "cpu": "200m"}}
    diff = replace(
        diff,
        desired_manifest=desired,
        changes=[
            {
                "field_path": (
                    "spec.template.spec.containers[name=api-server].resources"
                ),
                "classification": "intended_change",
                "old_desired": before,
                "live": before,
                "new_desired": after,
                "before": before,
                "after": after,
            }
        ],
    )

    assert recovery_diff_matches_approved_scope(payload, diff) is False
