"""연결 시점 desired vs live 프리뷰 순수 계산 — 재구성·분류·필드 diff."""

from __future__ import annotations

from domains.gitops.live_projection import (
    CHANGE_CREATE,
    CHANGE_IN_SYNC,
    CHANGE_UPDATE,
    project_resource_diff,
    reconstruct_live_object,
    resource_ref,
)


def _deployment_desired(image: str, replicas: int = 3):
    return {
        "apiVersion": "apps/v1",
        "kind": "Deployment",
        "metadata": {"name": "api", "namespace": "prod"},
        "spec": {
            "replicas": replicas,
            "template": {"spec": {"containers": [{"name": "api", "image": image}]}},
        },
    }


def _deployment_raw(image: str, replicas: int = 3):
    return {
        "kind": "Deployment",
        "api_version": "apps/v1",
        "name": "api",
        "namespace": "prod",
        "desired_replicas": replicas,
        "pod_template": {"spec": {"containers": [{"name": "api", "image": image}]}},
    }


def test_resource_ref_matches_inventory_key() -> None:
    assert resource_ref("Deployment", "api") == "deployment/api"


def test_reconstruct_workload_from_observed_summary() -> None:
    live = reconstruct_live_object("Deployment", _deployment_raw("repo/api:v1"))
    assert live is not None
    assert live["spec"]["replicas"] == 3
    assert live["spec"]["template"]["spec"]["containers"][0]["image"] == "repo/api:v1"
    assert live["metadata"] == {"name": "api", "namespace": "prod"}


def test_reconstruct_service_from_observed_summary() -> None:
    live = reconstruct_live_object(
        "Service",
        {"name": "api", "namespace": "prod", "type": "ClusterIP", "selector": {"app": "api"},
         "ports": [{"port": 80}]},
    )
    assert live is not None
    assert live["spec"] == {"type": "ClusterIP", "selector": {"app": "api"}, "ports": [{"port": 80}]}


def test_reconstruct_returns_none_when_nothing_observed() -> None:
    # 관측 요약에 재구성할 필드가 없으면 live 미관측(None).
    assert reconstruct_live_object("Deployment", {"name": "api", "namespace": "prod"}) is None
    assert reconstruct_live_object("Service", {"name": "api"}) is None
    assert reconstruct_live_object("", {"spec": {}}) is None


def test_reconstruct_custom_resource_passthrough() -> None:
    live = reconstruct_live_object(
        "Rollout",
        {"apiVersion": "argoproj.io/v1alpha1", "name": "r", "namespace": "prod",
         "spec": {"replicas": 2}},
    )
    assert live is not None
    assert live["kind"] == "Rollout"
    assert live["spec"] == {"replicas": 2}


def test_project_create_when_live_absent() -> None:
    result = project_resource_diff(_deployment_desired("repo/api:v2"), None)
    assert result["change"] == CHANGE_CREATE
    assert result["field_changes"] == []


def test_project_in_sync_when_managed_fields_match() -> None:
    desired = _deployment_desired("repo/api:v1")
    live = reconstruct_live_object("Deployment", _deployment_raw("repo/api:v1"))
    result = project_resource_diff(desired, live)
    assert result["change"] == CHANGE_IN_SYNC
    assert result["field_changes"] == []


def test_project_update_reports_image_before_after() -> None:
    desired = _deployment_desired("repo/api:v2")
    live = reconstruct_live_object("Deployment", _deployment_raw("repo/api:v1"))
    result = project_resource_diff(desired, live)
    assert result["change"] == CHANGE_UPDATE
    image_change = next(
        c for c in result["field_changes"] if c["field_path"].endswith(".image")
    )
    assert image_change["before"] == "repo/api:v1"
    assert image_change["after"] == "repo/api:v2"


def test_project_update_detects_replica_scale() -> None:
    desired = _deployment_desired("repo/api:v1", replicas=5)
    live = reconstruct_live_object("Deployment", _deployment_raw("repo/api:v1", replicas=2))
    result = project_resource_diff(desired, live)
    assert result["change"] == CHANGE_UPDATE
    replica_change = next(
        c for c in result["field_changes"] if c["field_path"] == "spec.replicas"
    )
    assert replica_change["before"] == "2"
    assert replica_change["after"] == "5"
