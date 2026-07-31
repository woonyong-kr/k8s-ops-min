"""Load real, scope-filtered pod measurements for Opsia alert rules."""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping
from datetime import UTC, datetime
from typing import Any

from domains.alert.evaluation import AlertMeasurement
from domains.inventory_filter.query import parse_resource_filters

JsonObject = dict[str, Any]
DEFAULT_MEASUREMENT_MAX_AGE_SECONDS = 30.0


class AlertRuleMeasurementLoader:
    """Resolve canonical rule scope and join it to the newest persisted live sample."""

    def __init__(
        self,
        db: Any,
        *,
        now: Callable[[], datetime] | None = None,
        max_age_seconds: float = DEFAULT_MEASUREMENT_MAX_AGE_SECONDS,
    ) -> None:
        if not math.isfinite(max_age_seconds) or max_age_seconds <= 0:
            raise ValueError("alert measurement max age must be positive")
        self.db = db
        self.now = now or (lambda: datetime.now(UTC))
        self.max_age_seconds = max_age_seconds

    async def __call__(self, rule: JsonObject) -> list[AlertMeasurement]:
        workspace_id = str(rule.get("workspace_id") or "")
        scope = _mapping(rule.get("scope"))
        cluster_ids = _scope_cluster_ids(scope)
        if not cluster_ids:
            cluster_ids = set(await self.db.list_workspace_cluster_ids(workspace_id))
        if not workspace_id or not cluster_ids:
            return []

        allowed_application_ids = set(await self.db.list_workspace_application_ids(workspace_id))
        context = await self.db.filter_snapshot_context(workspace_id, cluster_ids)
        snapshot_revision = int(context.get("snapshot_revision") or 0)
        if snapshot_revision <= 0:
            return []
        filters = parse_resource_filters(
            clusters=_joined(scope.get("clusters")),
            namespaces=_joined(scope.get("namespaces")),
            applications=_joined(scope.get("applications")),
            resource_types="pod",
            health=None,
            labels=_joined(scope.get("labels")),
            query=None,
            include_deleted=False,
        )
        resources = await self._list_pods(
            workspace_id=workspace_id,
            cluster_ids=cluster_ids,
            application_ids=allowed_application_ids,
            filters=filters,
            snapshot_revision=snapshot_revision,
        )
        samples = await self._latest_samples(workspace_id, cluster_ids)
        metric = str(rule.get("metric") or "")
        measurements: list[AlertMeasurement] = []
        for item in resources:
            resource = _mapping(item.get("resource"))
            cluster_id = str(resource.get("cluster_id") or "")
            namespace = str(resource.get("namespace") or "")
            name = str(resource.get("name") or "")
            if not cluster_id or not namespace or not name:
                continue
            sample = samples.get(cluster_id)
            if sample is None:
                continue
            pod = _mapping(_mapping(sample.get("usage")).get("pods")).get(f"{namespace}/{name}")
            pod = _mapping(pod)
            value = _metric_value(metric, pod)
            if value is None:
                continue
            subject = {
                "cluster": cluster_id,
                "namespace": namespace,
                "kind": str(resource.get("kind") or "Pod"),
                "name": name,
            }
            observed_at = sample["sampled_at"]
            measurements.append(
                AlertMeasurement(
                    subject=subject,
                    observed_value=value,
                    observed_at=observed_at,
                    evidence=(
                        {
                            "type": "metric_sample",
                            "metric": metric,
                            "observed_at": observed_at.isoformat(),
                            "subject": subject,
                            "value": value,
                            "summary": _evidence_summary(metric, value, pod),
                        },
                    ),
                )
            )
        return measurements

    async def _list_pods(
        self,
        *,
        workspace_id: str,
        cluster_ids: set[str],
        application_ids: set[str],
        filters: Any,
        snapshot_revision: int,
    ) -> list[JsonObject]:
        position: Mapping[str, Any] | None = None
        items: list[JsonObject] = []
        while True:
            page = await self.db.list_filtered_resources(
                workspace_id=workspace_id,
                allowed_cluster_ids=cluster_ids,
                allowed_application_ids=application_ids,
                filters=filters,
                snapshot_revision=snapshot_revision,
                position=position,
                limit=200,
            )
            items.extend(dict(item) for item in page.get("items") or [])
            position = page.get("next_position")
            if not page.get("has_more") or not isinstance(position, Mapping):
                return items

    async def _latest_samples(
        self,
        workspace_id: str,
        cluster_ids: set[str],
    ) -> dict[str, JsonObject]:
        loaded: dict[str, JsonObject] = {}
        now = self.now()
        for cluster_id in sorted(cluster_ids):
            samples = await self.db.list_cluster_usage_samples(
                workspace_id,
                cluster_id,
                limit=1,
            )
            if not samples:
                continue
            sample = dict(samples[-1])
            sampled_at = _timestamp(sample.get("sampled_at"))
            if sampled_at is None:
                continue
            age = (now - sampled_at).total_seconds()
            if age < 0 or age > self.max_age_seconds:
                continue
            sample["sampled_at"] = sampled_at
            loaded[cluster_id] = sample
        return loaded


def _scope_cluster_ids(scope: Mapping[str, Any]) -> set[str]:
    clusters = {str(value) for value in _strings(scope.get("clusters"))}
    namespaces = {
        str(value).rpartition("/")[0]
        for value in _strings(scope.get("namespaces"))
        if "/" in str(value)
    }
    return {value for value in clusters | namespaces if value}


def _metric_value(metric: str, pod: Mapping[str, Any]) -> float | None:
    if metric == "cpu_pct":
        return _percentage(
            pod,
            percentage_key="cpu_request_pct",
            value_key="cpu_mcores",
            request_key="cpu_request_mcores",
        )
    if metric == "mem_pct":
        return _percentage(
            pod,
            percentage_key="mem_request_pct",
            value_key="mem_mib",
            request_key="mem_request_mib",
        )
    if metric == "restart_count":
        return _nonnegative(pod.get("restarts"))
    if metric == "pod_not_ready":
        ready = pod.get("ready")
        if isinstance(ready, bool):
            return 0.0 if ready else 1.0
        if isinstance(ready, str) and "/" in ready:
            current, _separator, expected = ready.partition("/")
            try:
                return 0.0 if int(current) >= int(expected) and int(expected) > 0 else 1.0
            except ValueError:
                return None
    return None


def _percentage(
    pod: Mapping[str, Any],
    *,
    percentage_key: str,
    value_key: str,
    request_key: str,
) -> float | None:
    direct = _nonnegative(pod.get(percentage_key))
    if direct is not None:
        return direct
    value = _nonnegative(pod.get(value_key))
    request = _positive(pod.get(request_key))
    return value / request * 100.0 if value is not None and request is not None else None


def _evidence_summary(metric: str, value: float, pod: Mapping[str, Any]) -> str:
    labels = {
        "cpu_pct": "CPU 요청량 대비 사용률",
        "mem_pct": "메모리 요청량 대비 사용률",
        "restart_count": "컨테이너 재시작 횟수",
        "pod_not_ready": "파드 준비 실패 상태",
    }
    if metric == "cpu_pct":
        detail = _ratio_detail(pod, "cpu_mcores", "cpu_request_mcores", "mCPU")
    elif metric == "mem_pct":
        detail = _ratio_detail(pod, "mem_mib", "mem_request_mib", "MiB")
    else:
        detail = ""
    suffix = f" ({detail})" if detail else ""
    return f"{labels.get(metric, metric)} {value:.1f}{suffix}"


def _ratio_detail(pod: Mapping[str, Any], value_key: str, request_key: str, unit: str) -> str:
    value = _nonnegative(pod.get(value_key))
    request = _positive(pod.get(request_key))
    if value is None or request is None:
        return ""
    return f"{value:.2f}/{request:.2f} {unit}"


def _timestamp(value: object) -> datetime | None:
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    else:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed


def _mapping(value: object) -> JsonObject:
    return dict(value) if isinstance(value, Mapping) else {}


def _strings(value: object) -> list[str]:
    if not isinstance(value, list | tuple):
        return []
    return [str(item) for item in value if str(item)]


def _joined(value: object) -> str | None:
    values = _strings(value)
    return ",".join(values) if values else None


def _nonnegative(value: object) -> float | None:
    if value is None or isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) and parsed >= 0 else None


def _positive(value: object) -> float | None:
    parsed = _nonnegative(value)
    return parsed if parsed is not None and parsed > 0 else None
