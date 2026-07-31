"""Validated, environment-overridable refresh policy registry."""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from functools import lru_cache
from typing import Any, cast

from packages.config.settings import env
from packages.contracts.freshness import (
    BrowserRefreshPoliciesResponse,
    BrowserRefreshPolicy,
    RefreshPolicyKey,
)

REFRESH_POLICIES_ENV = "OPSIA_BROWSER_REFRESH_POLICIES_JSON"

# Canonical upstream-equivalent defaults live only on the Python side.  A
# deployment may replace individual policy fields through the JSON environment
# value; browsers always consume a validated response value.
_DEFAULT_POLICIES: dict[RefreshPolicyKey, dict[str, Any]] = {
    "dashboard": {
        "stale_after_seconds": 15,
        "refresh_after_seconds": 30,
        "event_invalidation": True,
    },
    "issues_audit": {"stale_after_seconds": 30, "refresh_after_seconds": 60},
    "applications": {"stale_after_seconds": 30, "refresh_after_seconds": 60},
    "resource_list": {
        "stale_after_seconds": 30,
        "refresh_after_seconds": 60,
        "event_invalidation": True,
    },
    "resource_list_slow": {
        "stale_after_seconds": 30,
        "refresh_after_seconds": 120,
        "event_invalidation": True,
    },
    "changes": {"stale_after_seconds": 5, "refresh_after_seconds": 15, "event_invalidation": True},
    "metrics_kubernetes": {
        # 신선도 창은 수집 파이프라인 주기의 합보다 커야 한다: kubelet 자체 갱신
        # (~15s) + 에이전트 evidence 주기(15~30s) + 전송 지연. 20s 는 정상 동작
        # 중에도 주기적으로 초과되어 cpu/mem 이 null 로 떨어지고 화면에
        # "메트릭 수집 대기"가 플리커했다. 2×주기+여유 = 45s 로 정합시킨다.
        "stale_after_seconds": 45,
        "refresh_after_seconds": 30,
        "retry_after_seconds": 5,
        "retry_limit": 2,
    },
    "metrics_prometheus": {"stale_after_seconds": 30, "refresh_after_seconds": 60},
    "metrics_pvc": {"stale_after_seconds": 30, "refresh_after_seconds": 120},
    "metrics_rightsizing": {"stale_after_seconds": 30, "refresh_after_seconds": 600},
    "gitops_rows": {
        "stale_after_seconds": 30,
        "refresh_after_seconds": 120,
        "retry_after_seconds": 2,
        "retry_limit": 4,
    },
    "gitops_counts": {"stale_after_seconds": 10, "refresh_after_seconds": 60},
    "helm_list": {
        "stale_after_seconds": 30,
        "refresh_after_seconds": 30,
        "post_mutation_refresh_after_seconds": 1.2,
    },
    "helm_detail": {
        "stale_after_seconds": 30,
        "refresh_after_seconds": 10,
        "post_mutation_refresh_after_seconds": 1.2,
    },
    "cost_summary": {"stale_after_seconds": 30, "refresh_after_seconds": 60},
    "cost_trend": {"stale_after_seconds": 30, "refresh_after_seconds": 120},
    "cost_nodes": {"stale_after_seconds": 30, "refresh_after_seconds": 120},
    "port_sessions": {
        "refresh_after_seconds": 10,
        "post_mutation_refresh_after_seconds": 0.5,
    },
}


@lru_cache(maxsize=1)
def browser_refresh_policies() -> BrowserRefreshPoliciesResponse:
    raw_override = env(REFRESH_POLICIES_ENV, "").strip()
    override = _parse_override(raw_override)
    merged = {
        key: {
            **values,
            **_mapping(override.get(key)),
        }
        for key, values in _DEFAULT_POLICIES.items()
    }
    policies = {key: BrowserRefreshPolicy.model_validate(values) for key, values in merged.items()}
    revision_payload = json.dumps(
        {key: policy.model_dump(mode="json") for key, policy in sorted(policies.items())},
        ensure_ascii=True,
        separators=(",", ":"),
        sort_keys=True,
    )
    return BrowserRefreshPoliciesResponse(
        revision=hashlib.sha256(revision_payload.encode("utf-8")).hexdigest(),
        policies=policies,
    )


def browser_refresh_policy(key: RefreshPolicyKey) -> BrowserRefreshPolicy:
    return browser_refresh_policies().policies[key]


def integral_refresh_after_seconds(key: RefreshPolicyKey) -> int:
    value = browser_refresh_policy(key).refresh_after_seconds
    if not value.is_integer():
        raise RuntimeError(f"{key} refresh_after_seconds must be an integer")
    return int(value)


def post_mutation_refresh_after_seconds(key: RefreshPolicyKey) -> float:
    value = browser_refresh_policy(key).post_mutation_refresh_after_seconds
    if value is None:
        raise RuntimeError(f"{key} post-mutation refresh policy is required")
    return value


def _parse_override(raw: str) -> Mapping[str, Any]:
    if not raw:
        return {}
    try:
        value = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"{REFRESH_POLICIES_ENV} must be valid JSON") from exc
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{REFRESH_POLICIES_ENV} must be a JSON object")
    unknown = set(value) - set(_DEFAULT_POLICIES)
    if unknown:
        raise RuntimeError(f"{REFRESH_POLICIES_ENV} contains unknown policies: {sorted(unknown)}")
    return cast(Mapping[str, Any], value)


def _mapping(value: object) -> Mapping[str, Any]:
    if value is None:
        return {}
    if not isinstance(value, Mapping):
        raise RuntimeError(f"{REFRESH_POLICIES_ENV} policy overrides must be JSON objects")
    return cast(Mapping[str, Any], value)
