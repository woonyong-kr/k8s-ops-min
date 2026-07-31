"""리소스 소유권 겹침 감지(no-conflict) 순수 로직 검증.

같은 리소스를 두 추적 대상이 선언하면 SSA force-apply 로 조용히 thrash 하므로,
연결 시점에 후보 리소스가 이미 소유된 것과 겹치는지 결정적으로 판정한다.
"""

from __future__ import annotations

from types import SimpleNamespace

from domains.applications.ownership import (
    candidate_identity_keys,
    find_resource_conflicts,
    resource_identity_key,
)


def test_identity_key_matches_stored_format() -> None:
    # persist_repository_connect_validation 과 동일 포맷:
    # apiVersion/kind_casefold/namespace_or__cluster/name
    assert resource_identity_key("apps/v1", "Deployment", "prod", "api") == "apps/v1/deployment/prod/api"


def test_cluster_scoped_resource_uses_sentinel() -> None:
    # 네임스페이스 없는(클러스터 스코프) 리소스는 _cluster 로 정규화.
    assert resource_identity_key("v1", "Namespace", None, "team-a") == "v1/namespace/_cluster/team-a"
    assert resource_identity_key("v1", "Namespace", "  ", "team-a") == "v1/namespace/_cluster/team-a"


def test_candidate_keys_from_resources() -> None:
    resources = [
        SimpleNamespace(api_version="apps/v1", kind="Deployment", namespace="prod", name="api"),
        SimpleNamespace(api_version="v1", kind="Service", namespace="prod", name="api"),
        # 식별자 불완전(name 없음)은 제외.
        SimpleNamespace(api_version="v1", kind="Service", namespace="prod", name=""),
    ]
    keys = candidate_identity_keys(resources)
    assert keys == ["apps/v1/deployment/prod/api", "v1/service/prod/api"]


def test_conflict_detected_against_owned_index() -> None:
    owned = {
        "apps/v1/deployment/prod/api": {"application_id": "app-1", "app_name": "checkout"},
    }
    candidate = ["apps/v1/deployment/prod/api", "v1/service/prod/api"]
    conflicts = find_resource_conflicts(candidate, owned)
    assert len(conflicts) == 1
    assert conflicts[0]["resource_identity"] == "apps/v1/deployment/prod/api"
    assert conflicts[0]["owner_app_name"] == "checkout"
    assert conflicts[0]["owner_application_id"] == "app-1"


def test_no_conflict_when_disjoint() -> None:
    owned = {"apps/v1/deployment/prod/api": {"application_id": "app-1", "app_name": "checkout"}}
    assert find_resource_conflicts(["v1/service/prod/web"], owned) == []


def test_duplicate_candidate_key_reported_once() -> None:
    owned = {"apps/v1/deployment/prod/api": {"application_id": "app-1", "app_name": "checkout"}}
    conflicts = find_resource_conflicts(
        ["apps/v1/deployment/prod/api", "apps/v1/deployment/prod/api"], owned
    )
    assert len(conflicts) == 1


def test_kind_case_insensitive_match() -> None:
    # 저장은 casefold 되므로 후보 kind 대소문자가 달라도 같은 리소스로 겹침 감지.
    owned = {resource_identity_key("apps/v1", "Deployment", "prod", "api"): {"application_id": "app-1", "app_name": "checkout"}}
    candidate = candidate_identity_keys(
        [SimpleNamespace(api_version="apps/v1", kind="DEPLOYMENT", namespace="prod", name="api")]
    )
    assert len(find_resource_conflicts(candidate, owned)) == 1
