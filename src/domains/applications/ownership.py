"""리소스 소유권 겹침 감지 — 하나의 k8s 리소스에는 추적 대상(소유자)이 하나여야 한다.

두 개의 서로 다른 연결이 같은 리소스(kind/namespace/name)를 선언하면, SSA 가
force=true 로 적용되므로 크래시 없이 소유권을 서로 뺏고 뺏는 무한 드리프트가
생긴다. 이를 연결 시점에 미리 잡아 경고/차단하기 위한 순수 로직이다(DB·네트워크
무접근, 오프라인 검증 가능).

식별자 포맷은 저장된 형식(``persist_repository_connect_validation``)과 정확히
일치시켜 후보(새로 검증한 것)와 기존(저장된 것)이 문자열로 바로 비교되게 한다:

    ``{api_version}/{kind_casefold}/{namespace_or__cluster}/{name}``
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

CLUSTER_SCOPE_SENTINEL = "_cluster"


def resource_identity_key(
    api_version: str,
    kind: str,
    namespace: str | None,
    name: str,
) -> str:
    """저장 형식과 동일한 리소스 식별 키(대소문자·클러스터 스코프 규칙 포함)."""
    return "/".join(
        (
            api_version.strip(),
            kind.strip().casefold(),
            (namespace.strip() if namespace and namespace.strip() else CLUSTER_SCOPE_SENTINEL),
            name.strip(),
        )
    )


def candidate_identity_keys(resources: Iterable[Any]) -> list[str]:
    """검증 결과 리소스들(RepositoryManifestResource 등)에서 식별 키 목록을 만든다."""
    keys: list[str] = []
    for resource in resources:
        api_version = str(getattr(resource, "api_version", "") or "")
        kind = str(getattr(resource, "kind", "") or "")
        namespace = getattr(resource, "namespace", None)
        name = str(getattr(resource, "name", "") or "")
        if api_version and kind and name:
            keys.append(resource_identity_key(api_version, kind, namespace, name))
    return keys


def find_resource_conflicts(
    candidate_keys: Iterable[str],
    owned_index: Mapping[str, Mapping[str, str]],
) -> list[dict[str, str]]:
    """후보 리소스가 이미 다른 추적 대상이 소유한 것과 겹치면 그 목록을 돌려준다.

    ``owned_index`` 는 ``{resource_identity: {application_id, app_name}}`` 형태.
    같은 키가 중복 등장해도 한 번만 보고한다(멱등).
    """
    conflicts: list[dict[str, str]] = []
    seen: set[str] = set()
    for key in candidate_keys:
        if key in seen:
            continue
        owner = owned_index.get(key)
        if owner:
            seen.add(key)
            conflicts.append(
                {
                    "resource_identity": key,
                    "owner_application_id": str(owner.get("application_id") or ""),
                    "owner_app_name": str(owner.get("app_name") or ""),
                }
            )
    return conflicts
