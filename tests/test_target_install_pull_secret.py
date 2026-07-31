"""옵트인 image pull secret — 비공개 레지스트리 에이전트 이미지를 어떤 클러스터에서나 pull.

미설정이면 매니페스트 불변(회귀 0), 설정 시 dockerconfigjson Secret + ServiceAccount
imagePullSecrets 를 발급한다. 삭제된 테스트 픽스처에 의존하지 않는 자체 완결 테스트.
"""

from __future__ import annotations

import yaml

from domains.target.install_manifest import (
    IMAGE_PULL_SECRET_NAME,
    image_pull_secret_manifest,
    service_account_manifest,
    target_install_manifest,
)
from packages.contracts.gateway.requests import TargetRegisterRequest

DOCKERCONFIG = '{"auths":{"registry.example.com":{"auth":"dXNlcjpwYXNz"}}}'


def _payload() -> TargetRegisterRequest:
    return TargetRegisterRequest(cloud_provider="existing-k8s", deploy_provider="manual-manifest")


def _docs(manifest: str) -> list[dict]:
    return [doc for doc in yaml.safe_load_all(manifest) if isinstance(doc, dict)]


def test_service_account_has_no_pull_secret_by_default() -> None:
    doc = yaml.safe_load(service_account_manifest("target"))
    assert doc["kind"] == "ServiceAccount"
    assert "imagePullSecrets" not in doc


def test_service_account_references_pull_secret_when_named() -> None:
    doc = yaml.safe_load(service_account_manifest("target", IMAGE_PULL_SECRET_NAME))
    assert doc["imagePullSecrets"] == [{"name": IMAGE_PULL_SECRET_NAME}]


def test_pull_secret_manifest_is_dockerconfigjson() -> None:
    doc = yaml.safe_load(image_pull_secret_manifest(DOCKERCONFIG, "target"))
    assert doc["kind"] == "Secret"
    assert doc["type"] == "kubernetes.io/dockerconfigjson"
    assert doc["metadata"]["name"] == IMAGE_PULL_SECRET_NAME
    assert doc["metadata"]["namespace"] == "target"
    # stringData 의 .dockerconfigjson 이 원본 자격증명을 그대로 담는다.
    assert doc["stringData"][".dockerconfigjson"] == DOCKERCONFIG


def test_install_manifest_unchanged_without_pull_secret() -> None:
    # 옵트인 미설정(기본)이면 pull secret 요소가 없어야 한다(회귀 0). RBAC 규칙 문자열에
    # imagePullSecrets 가 등장할 수 있으므로 매니페스트 전체 문자열이 아니라 실제 객체로 검증.
    manifest = target_install_manifest(_payload(), "tok")
    assert "kubernetes.io/dockerconfigjson" not in manifest
    sa = next(
        d
        for d in _docs(manifest)
        if d.get("kind") == "ServiceAccount" and d.get("metadata", {}).get("name") == "cluster-agent"
    )
    assert "imagePullSecrets" not in sa


def test_install_manifest_includes_pull_secret_when_configured() -> None:
    manifest = target_install_manifest(_payload(), "tok", image_pull_secret=DOCKERCONFIG)
    docs = _docs(manifest)
    # dockerconfigjson Secret 이 정확히 하나 발급된다.
    pull_secrets = [
        d
        for d in docs
        if d.get("kind") == "Secret" and d.get("type") == "kubernetes.io/dockerconfigjson"
    ]
    assert len(pull_secrets) == 1
    assert pull_secrets[0]["stringData"][".dockerconfigjson"] == DOCKERCONFIG
    # cluster-agent ServiceAccount 가 그 secret 을 참조한다.
    sa = next(
        d
        for d in docs
        if d.get("kind") == "ServiceAccount" and d.get("metadata", {}).get("name") == "cluster-agent"
    )
    assert sa["imagePullSecrets"] == [{"name": IMAGE_PULL_SECRET_NAME}]


def test_install_manifest_pull_secret_whitespace_is_ignored() -> None:
    # 공백만 있는 값은 미설정과 동일하게 취급(빈 secret 발급 금지).
    manifest = target_install_manifest(_payload(), "tok", image_pull_secret="   ")
    assert "kubernetes.io/dockerconfigjson" not in manifest
