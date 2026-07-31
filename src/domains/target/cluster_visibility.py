"""Blocked test-cluster 표시 규칙을 cluster 목록과 scope 투영이 공유한다.

`list_clusters`는 이 규칙으로 테스트 클러스터를 숨기지만, 과거 checks/cost/traffic의
scope 투영은 `resolve_allowed_cluster_ids`(인가 결과)를 그대로 써서 테스트 클러스터가
scope_coverage로 새어 count 불일치(예: checks scope 9 vs 목록 5)를 만들었다.
이 모듈은 단일 predicate를 제공해 표시 universe를 일치시킨다. 인가 자체(누가 어떤
클러스터를 볼 수 있는가)는 바꾸지 않는다 — 표시/기본 scope에서만 숨긴다.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any

BLOCKED_TEST_CLUSTER_IDS = {"bruno-api-test"}
BLOCKED_TEST_CLUSTER_NAME_PARTS = ("bruno api test",)


def is_blocked_test_cluster(cluster_id: str, name: str = "") -> bool:
    """표시/scope에서 숨기는 테스트 클러스터인지 여부(id 또는 이름 규칙)."""
    if cluster_id in BLOCKED_TEST_CLUSTER_IDS:
        return True
    lowered = str(name).lower()
    return any(marker in lowered for marker in BLOCKED_TEST_CLUSTER_NAME_PARTS)


def visible_allowed_cluster_ids(
    db: Any, workspace_id: str, allowed_cluster_ids: Iterable[str]
) -> set[str]:
    """authorized cluster id 집합을 `list_clusters`와 동일한 표시 universe로 축소한다.

    표시 universe = **allowed ∩ (list_cluster_registrations가 반환한 active
    registration id) ∩ (blocked-test 아님)**. 즉 authorized여도 현재 등록 레코드가
    반환되지 않는 id(미등록/reader가 제외한 것)는 기본 scope에서 제외한다 —
    `resolve_allowed_cluster_ids`(인가)는 축소만 하며 확장하지 않는다.

    `list_cluster_registrations` reader가 없으면(테스트 double 등) 이름을 얻을 수 없어
    **id 규칙만** 적용하는 축소된 fallback을 쓴다. 이 경로는 이름-only blocked나
    미등록 allowed id를 완전히 걸러내지 못하므로 정확한 universe 정합은 reader 경로에서만
    보장된다.
    """
    allowed = {cluster_id for cluster_id in allowed_cluster_ids if cluster_id}
    if not allowed:
        return set()
    reader = getattr(db, "list_cluster_registrations", None)
    if not callable(reader):
        return {cluster_id for cluster_id in allowed if cluster_id not in BLOCKED_TEST_CLUSTER_IDS}
    records = reader(workspace_id, cluster_ids=set(allowed), limit=len(allowed))
    return {
        cluster_id
        for record in records
        if isinstance(record, Mapping)
        for cluster_id in (str(record.get("cluster_id") or ""),)
        if cluster_id in allowed
        and not is_blocked_test_cluster(cluster_id, str(record.get("name") or ""))
    }
