"""Cost projections from bounded observations persisted by outbound agents."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import ROUND_HALF_UP, Decimal, InvalidOperation
from typing import Any

from domains.inventory_filter.snapshot_scope import project_snapshot_scope
from packages.config.refresh_policies import integral_refresh_after_seconds
from packages.contracts.cost.observations import (
    COST_NAMESPACE_HOURLY_METRIC,
    COST_NAMESPACE_STORAGE_METRIC,
    COST_POD_CPU_HOURLY_METRIC,
    COST_POD_CPU_USE_METRIC,
    COST_POD_MEMORY_HOURLY_METRIC,
    COST_POD_MEMORY_USE_METRIC,
    MAX_COST_TREND_POINTS,
    MAX_COST_TREND_SERIES,
    MAX_SAFE_JSON_INTEGER,
    CostCurrentAllocation,
    CostObservationStatus,
    CostObservationSummary,
    CostObservedObservationStatus,
    CostObservedObservationSummary,
    CostObservedTrend,
    CostObservedWorkloadAllocation,
    CostOverviewResponse,
    CostScopeCoverage,
    CostTimeRange,
    CostTrendPoint,
    CostTrendSeries,
    CostUnavailableTrend,
    CostUnavailableWorkloadAllocation,
    CostWorkloadAllocation,
)

COST_OBSERVATION_UNAVAILABLE = "cost_observation_unavailable"
COST_OBSERVATION_PARTIAL = "cost_observation_partial"
COST_STORAGE_OBSERVATION_PARTIAL = "cost_storage_observation_partial"
COST_SCOPE_PARTIAL = "cost_scope_partial"
COST_TREND_HISTORY_INSUFFICIENT = "cost_trend_history_insufficient"
COST_TREND_SERIES_TRUNCATED = "cost_trend_series_truncated"
COST_WORKLOAD_OBSERVATION_UNAVAILABLE = "cost_workload_observation_unavailable"
COST_WORKLOAD_OBSERVATION_PARTIAL = "cost_workload_observation_partial"
COST_WORKLOAD_HISTORY_INSUFFICIENT = "cost_workload_history_insufficient"
COST_WORKLOAD_USAGE_PARTIAL = "cost_workload_usage_partial"
COST_CURRENCY = "USD"
COST_ALLOCATION_WINDOW = "1h"
MONTHLY_PROJECTION_HOURS = 730
DAILY_PROJECTION_HOURS = 24
WORKLOAD_USAGE_WINDOW_SECONDS = 300
MICROS_PER_UNIT = Decimal("1000000")
BASIS_POINTS_PER_UNIT = Decimal("10000")


@dataclass(frozen=True)
class _CostWindow:
    cluster_id: str
    timestamp: int
    observed_at: str
    rates: Mapping[str, int]
    storage_rates: Mapping[str, int] | None
    invalid: bool


@dataclass(frozen=True)
class _WorkloadCostWindow:
    timestamp: int
    observed_at: str
    cpu_rates: Mapping[str, int]
    memory_rates: Mapping[str, int]
    cpu_use_basis_points: Mapping[str, int] | None
    memory_use_basis_points: Mapping[str, int] | None
    invalid: bool
    missing_pods: tuple[str, ...]


def cost_overview(
    *,
    workspace_id: str,
    contexts: Mapping[str, Mapping[str, Any]],
    selected_cluster_ids: Iterable[str],
    namespace_refs: Iterable[tuple[str, str]] = (),
    time_range: CostTimeRange = "24h",
    evidence_windows: Sequence[Mapping[str, Any]] = (),
) -> CostOverviewResponse:
    """Project truthful money only from authorized, persisted agent evidence."""

    selected = tuple(sorted(set(selected_cluster_ids)))
    namespaces = tuple(sorted(set(namespace_refs)))
    coverage = cost_scope_coverage(
        workspace_id=workspace_id,
        contexts=contexts,
        selected_cluster_ids=selected,
        namespace_refs=namespaces,
    )

    windows = tuple(
        window
        for row in evidence_windows
        if (window := _cost_window(row, selected, namespaces)) is not None
    )
    latest = _latest_windows(windows)
    if not latest:
        return _unavailable_overview(coverage=coverage, time_range=time_range)

    missing_clusters = set(selected) - set(latest)
    invalid = any(window.invalid for window in latest.values())
    storage_partial = any(window.storage_rates is None for window in latest.values())
    reasons = _unique_reasons(
        (COST_OBSERVATION_PARTIAL,) if missing_clusters or invalid else (),
        (COST_STORAGE_OBSERVATION_PARTIAL,) if storage_partial else (),
        (COST_SCOPE_PARTIAL,) if coverage.availability != "available" else (),
    )
    availability = "partial" if reasons else "available"
    hourly_cost = _safe_total(_window_total(window) for window in latest.values())
    if hourly_cost is None:
        return _unavailable_overview(coverage=coverage, time_range=time_range)
    storage_cost = (
        None
        if storage_partial
        else _safe_total(
            sum(window.storage_rates.values())
            for window in latest.values()
            if window.storage_rates is not None
        )
    )
    monthly_projection = _safe_multiply(hourly_cost, MONTHLY_PROJECTION_HOURS)
    if monthly_projection is None:
        return _unavailable_overview(coverage=coverage, time_range=time_range)

    observed_at = max(window.observed_at for window in latest.values())
    trend = _cost_trend(
        windows=windows,
        time_range=time_range,
        partial_reasons=reasons,
        cluster_count=len(selected),
    )
    return CostOverviewResponse(
        scope_coverage=coverage,
        observation=CostObservedObservationStatus(
            availability=availability,
            observed_at=observed_at,
            currency=COST_CURRENCY,
            data_window=COST_ALLOCATION_WINDOW,
            reason_codes=reasons,
        ),
        summary=CostObservedObservationSummary(
            availability=availability,
            hourly_cost=hourly_cost,
            monthly_projection=monthly_projection,
            storage_cost=storage_cost,
            idle_cost=None,
            efficiency=None,
            savings_recommendations=None,
            reason_codes=reasons,
        ),
        trend=trend,
        refresh_after_seconds=integral_refresh_after_seconds("cost_summary"),
        trend_refresh_after_seconds=integral_refresh_after_seconds("cost_trend"),
        nodes_refresh_after_seconds=integral_refresh_after_seconds("cost_nodes"),
    )


def cost_workload_allocation(
    *,
    cluster_id: str,
    namespace: str,
    workload_name: str,
    pod_names: Iterable[str],
    replicas: int | None,
    evidence_windows: Sequence[Mapping[str, Any]] = (),
    time_range: CostTimeRange = "24h",
    membership_complete: bool = True,
) -> CostWorkloadAllocation:
    """Project one authorized workload from agent-persisted pod allocation evidence."""

    selected_pods = tuple(sorted({pod for pod in pod_names if pod}))
    if (
        not cluster_id
        or not namespace
        or not workload_name
        or not selected_pods
        or replicas is None
    ):
        return CostUnavailableWorkloadAllocation(
            reason_codes=(COST_WORKLOAD_OBSERVATION_UNAVAILABLE,)
        )
    windows = tuple(
        window
        for row in evidence_windows
        if (
            window := _workload_cost_window(
                row,
                cluster_id=cluster_id,
                namespace=namespace,
                pod_names=selected_pods,
            )
        )
        is not None
    )
    if not windows:
        return CostUnavailableWorkloadAllocation(
            reason_codes=(COST_WORKLOAD_OBSERVATION_UNAVAILABLE,)
        )
    latest = max(windows, key=lambda window: window.timestamp)
    cpu_rate = _safe_total(latest.cpu_rates.values())
    memory_rate = _safe_total(latest.memory_rates.values())
    if cpu_rate is None or memory_rate is None:
        return CostUnavailableWorkloadAllocation(
            reason_codes=(COST_WORKLOAD_OBSERVATION_UNAVAILABLE,)
        )
    hourly_rate = _safe_total((cpu_rate, memory_rate))
    if hourly_rate is None:
        return CostUnavailableWorkloadAllocation(
            reason_codes=(COST_WORKLOAD_OBSERVATION_UNAVAILABLE,)
        )
    projected_daily = _safe_multiply(hourly_rate, DAILY_PROJECTION_HOURS)
    projected_monthly = _safe_multiply(hourly_rate, MONTHLY_PROJECTION_HOURS)
    if projected_daily is None or projected_monthly is None:
        return CostUnavailableWorkloadAllocation(
            reason_codes=(COST_WORKLOAD_OBSERVATION_UNAVAILABLE,)
        )

    cpu_use = _weighted_basis_points(latest.cpu_use_basis_points, latest.cpu_rates)
    memory_use = _weighted_basis_points(latest.memory_use_basis_points, latest.memory_rates)
    reasons = _unique_reasons(
        (COST_WORKLOAD_OBSERVATION_PARTIAL,)
        if latest.invalid or latest.missing_pods or not membership_complete
        else (),
        (COST_WORKLOAD_USAGE_PARTIAL,) if cpu_use is None or memory_use is None else (),
    )
    trend = _workload_cost_trend(
        windows=windows,
        workload_name=workload_name,
        time_range=time_range,
        partial_reasons=reasons,
    )
    if isinstance(trend, CostUnavailableTrend):
        reasons = _unique_reasons(reasons, (COST_WORKLOAD_HISTORY_INSUFFICIENT,))
    return CostObservedWorkloadAllocation(
        availability="partial" if reasons else "available",
        observed_at=latest.observed_at,
        currency=COST_CURRENCY,
        current=CostCurrentAllocation(
            replicas=replicas,
            hourly_rate_micros=hourly_rate,
            projected_daily_micros=projected_daily,
            projected_monthly_micros=projected_monthly,
            cpu_rate_micros=cpu_rate,
            memory_rate_micros=memory_rate,
            cpu_allocation_use_basis_points=cpu_use,
            memory_allocation_use_basis_points=memory_use,
            cpu_usage_window_seconds=(
                WORKLOAD_USAGE_WINDOW_SECONDS if cpu_use is not None else None
            ),
            memory_usage_window_seconds=(
                WORKLOAD_USAGE_WINDOW_SECONDS if memory_use is not None else None
            ),
        ),
        trend=trend,
        reason_codes=reasons,
    )


def _workload_cost_window(
    row: Mapping[str, Any],
    *,
    cluster_id: str,
    namespace: str,
    pod_names: tuple[str, ...],
) -> _WorkloadCostWindow | None:
    row_cluster_id = row.get("cluster_id")
    payload = row.get("payload")
    if row_cluster_id != cluster_id or not isinstance(payload, Mapping):
        return None
    if payload.get("cluster_id") not in (None, cluster_id):
        return None
    timestamp = _timestamp(row.get("updated_at"))
    results = _metric_results(payload)
    if timestamp is None or results is None:
        return None
    cpu_rates, cpu_invalid = _pod_micros(
        results.get(COST_POD_CPU_HOURLY_METRIC),
        namespace=namespace,
        pod_names=pod_names,
    )
    memory_rates, memory_invalid = _pod_micros(
        results.get(COST_POD_MEMORY_HOURLY_METRIC),
        namespace=namespace,
        pod_names=pod_names,
    )
    if not cpu_rates or not memory_rates:
        return None
    cpu_use, cpu_use_invalid = _pod_basis_points(
        results.get(COST_POD_CPU_USE_METRIC),
        namespace=namespace,
        pod_names=pod_names,
    )
    memory_use, memory_use_invalid = _pod_basis_points(
        results.get(COST_POD_MEMORY_USE_METRIC),
        namespace=namespace,
        pod_names=pod_names,
    )
    observed_pods = set(cpu_rates) & set(memory_rates)
    return _WorkloadCostWindow(
        timestamp=timestamp,
        observed_at=datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z"),
        cpu_rates=cpu_rates,
        memory_rates=memory_rates,
        cpu_use_basis_points=cpu_use,
        memory_use_basis_points=memory_use,
        invalid=cpu_invalid or memory_invalid or cpu_use_invalid or memory_use_invalid,
        missing_pods=tuple(sorted(set(pod_names) - observed_pods)),
    )


def _pod_micros(
    result: Any,
    *,
    namespace: str,
    pod_names: tuple[str, ...],
) -> tuple[dict[str, int] | None, bool]:
    return _pod_values(
        result,
        namespace=namespace,
        pod_names=pod_names,
        convert=_to_micros,
    )


def _pod_basis_points(
    result: Any,
    *,
    namespace: str,
    pod_names: tuple[str, ...],
) -> tuple[dict[str, int] | None, bool]:
    return _pod_values(
        result,
        namespace=namespace,
        pod_names=pod_names,
        convert=_to_basis_points,
    )


def _pod_values(
    result: Any,
    *,
    namespace: str,
    pod_names: tuple[str, ...],
    convert: Callable[[Any], int | None],
) -> tuple[dict[str, int] | None, bool]:
    if not isinstance(result, Mapping):
        return None, False
    samples = result.get("samples")
    if not isinstance(samples, list):
        return None, True
    allowed_pods = set(pod_names)
    values: dict[str, int] = {}
    invalid = False
    for sample in samples:
        if not isinstance(sample, Mapping):
            invalid = True
            continue
        metric = sample.get("metric")
        if not isinstance(metric, Mapping):
            invalid = True
            continue
        sample_namespace = metric.get("namespace") or metric.get("exported_namespace")
        pod = metric.get("pod") or metric.get("pod_name")
        if sample_namespace != namespace or pod not in allowed_pods:
            continue
        value = convert(sample.get("value"))
        if value is None:
            invalid = True
            continue
        previous = values.get(str(pod), 0)
        if previous > MAX_SAFE_JSON_INTEGER - value:
            invalid = True
            continue
        values[str(pod)] = previous + value
    return values, invalid


def _to_basis_points(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        decimal = Decimal(str(value))
    except InvalidOperation:
        return None
    if not decimal.is_finite() or decimal < 0 or decimal > 1:
        return None
    return int((decimal * BASIS_POINTS_PER_UNIT).quantize(Decimal("1"), rounding=ROUND_HALF_UP))


def _weighted_basis_points(
    values: Mapping[str, int] | None,
    weights: Mapping[str, int],
) -> int | None:
    if values is None or set(weights) - set(values):
        return None
    weight_total = sum(weights.values())
    if weight_total == 0:
        return 0
    numerator = sum(values[pod] * weight for pod, weight in weights.items())
    weighted = (Decimal(numerator) / Decimal(weight_total)).quantize(
        Decimal("1"), rounding=ROUND_HALF_UP
    )
    return min(10_000, int(weighted))


def _workload_cost_trend(
    *,
    windows: Sequence[_WorkloadCostWindow],
    workload_name: str,
    time_range: CostTimeRange,
    partial_reasons: tuple[str, ...],
) -> CostObservedTrend | CostUnavailableTrend:
    points: dict[int, int] = {}
    historical_partial = False
    for window in windows:
        cpu_rate = _safe_total(window.cpu_rates.values())
        memory_rate = _safe_total(window.memory_rates.values())
        total = _safe_total(value for value in (cpu_rate, memory_rate) if value is not None)
        if cpu_rate is None or memory_rate is None or total is None:
            historical_partial = True
            continue
        points[window.timestamp] = total
        historical_partial = historical_partial or window.invalid or bool(window.missing_pods)
    ordered = tuple(sorted(points.items()))[-MAX_COST_TREND_POINTS:]
    if len(ordered) < 2:
        return CostUnavailableTrend(
            range=time_range,
            reason_codes=(COST_WORKLOAD_HISTORY_INSUFFICIENT,),
        )
    reasons = _unique_reasons(
        partial_reasons,
        (COST_WORKLOAD_OBSERVATION_PARTIAL,) if historical_partial else (),
    )
    return CostObservedTrend(
        availability="partial" if reasons else "available",
        range=time_range,
        currency=COST_CURRENCY,
        series=(_trend_series("workload", workload_name, ordered),),
        reason_codes=reasons,
    )


def _unavailable_overview(
    *,
    coverage: CostScopeCoverage,
    time_range: CostTimeRange,
) -> CostOverviewResponse:
    reasons = (COST_OBSERVATION_UNAVAILABLE,)
    return CostOverviewResponse(
        scope_coverage=coverage,
        observation=CostObservationStatus(reason_codes=reasons),
        summary=CostObservationSummary(reason_codes=reasons),
        trend=CostUnavailableTrend(range=time_range, reason_codes=reasons),
        refresh_after_seconds=integral_refresh_after_seconds("cost_summary"),
        trend_refresh_after_seconds=integral_refresh_after_seconds("cost_trend"),
        nodes_refresh_after_seconds=integral_refresh_after_seconds("cost_nodes"),
    )


def _cost_window(
    row: Mapping[str, Any],
    selected_cluster_ids: tuple[str, ...],
    namespace_refs: tuple[tuple[str, str], ...],
) -> _CostWindow | None:
    cluster_id = row.get("cluster_id")
    payload = row.get("payload")
    if not isinstance(cluster_id, str) or cluster_id not in selected_cluster_ids:
        return None
    if not isinstance(payload, Mapping) or payload.get("cluster_id") not in (None, cluster_id):
        return None
    timestamp = _timestamp(row.get("updated_at"))
    if timestamp is None:
        return None
    results = _metric_results(payload)
    if results is None:
        return None
    allowed_namespaces = {
        namespace
        for namespace_cluster, namespace in namespace_refs
        if namespace_cluster == cluster_id
    }
    scoped = bool(namespace_refs)
    rates, rates_invalid = _namespace_rates(
        results.get(COST_NAMESPACE_HOURLY_METRIC),
        allowed_namespaces=allowed_namespaces,
        scoped=scoped,
    )
    if rates is None or not rates:
        return None
    storage, storage_invalid = _namespace_rates(
        results.get(COST_NAMESPACE_STORAGE_METRIC),
        allowed_namespaces=allowed_namespaces,
        scoped=scoped,
    )
    return _CostWindow(
        cluster_id=cluster_id,
        timestamp=timestamp,
        observed_at=datetime.fromtimestamp(timestamp, tz=UTC).isoformat().replace("+00:00", "Z"),
        rates=rates,
        storage_rates=storage,
        invalid=rates_invalid or storage_invalid,
    )


def _metric_results(payload: Mapping[str, Any]) -> Mapping[str, Any] | None:
    metrics = payload.get("metrics")
    if not isinstance(metrics, Mapping):
        return None
    results = metrics.get("results")
    return results if isinstance(results, Mapping) else None


def _namespace_rates(
    result: Any,
    *,
    allowed_namespaces: set[str],
    scoped: bool,
) -> tuple[dict[str, int] | None, bool]:
    if not isinstance(result, Mapping):
        return None, False
    samples = result.get("samples")
    if not isinstance(samples, list):
        return None, True
    rates: dict[str, int] = defaultdict(int)
    invalid = False
    for sample in samples:
        if not isinstance(sample, Mapping):
            invalid = True
            continue
        metric = sample.get("metric")
        namespace = (
            metric.get("namespace") or metric.get("exported_namespace")
            if isinstance(metric, Mapping)
            else None
        )
        if not isinstance(namespace, str) or not namespace:
            invalid = True
            continue
        if scoped and namespace not in allowed_namespaces:
            continue
        micros = _to_micros(sample.get("value"))
        if micros is None or rates[namespace] > MAX_SAFE_JSON_INTEGER - micros:
            invalid = True
            continue
        rates[namespace] += micros
    return dict(rates), invalid


def _to_micros(value: Any) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float, str, Decimal)):
        return None
    try:
        decimal = Decimal(str(value))
    except InvalidOperation:
        return None
    if not decimal.is_finite() or decimal < 0:
        return None
    micros = int((decimal * MICROS_PER_UNIT).quantize(Decimal("1"), rounding=ROUND_HALF_UP))
    return micros if micros <= MAX_SAFE_JSON_INTEGER else None


def _timestamp(value: Any) -> int | None:
    parsed: datetime | None = None
    if isinstance(value, datetime):
        parsed = value
    elif isinstance(value, str) and value:
        try:
            parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if parsed is None:
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    timestamp = int(parsed.timestamp())
    return timestamp if timestamp >= 0 else None


def _latest_windows(windows: Sequence[_CostWindow]) -> dict[str, _CostWindow]:
    latest: dict[str, _CostWindow] = {}
    for window in windows:
        current = latest.get(window.cluster_id)
        if current is None or window.timestamp >= current.timestamp:
            latest[window.cluster_id] = window
    return latest


def _window_total(window: _CostWindow) -> int:
    return sum(window.rates.values()) + (
        sum(window.storage_rates.values()) if window.storage_rates is not None else 0
    )


def _safe_total(values: Iterable[int]) -> int | None:
    total = 0
    for value in values:
        if value < 0 or total > MAX_SAFE_JSON_INTEGER - value:
            return None
        total += value
    return total


def _safe_multiply(value: int, multiplier: int) -> int | None:
    if value < 0 or multiplier < 0 or value > MAX_SAFE_JSON_INTEGER // multiplier:
        return None
    return value * multiplier


def _cost_trend(
    *,
    windows: Sequence[_CostWindow],
    time_range: CostTimeRange,
    partial_reasons: tuple[str, ...],
    cluster_count: int,
) -> CostObservedTrend | CostUnavailableTrend:
    points_by_key: dict[str, dict[int, int]] = defaultdict(dict)
    labels: dict[str, str] = {}
    for window in windows:
        namespace_rates = dict(window.rates)
        if window.storage_rates is not None:
            for namespace, storage_rate in window.storage_rates.items():
                namespace_rates[namespace] = namespace_rates.get(namespace, 0) + storage_rate
        for namespace, rate in namespace_rates.items():
            if rate > MAX_SAFE_JSON_INTEGER:
                continue
            key = f"{window.cluster_id}/{namespace}"
            labels[key] = namespace if cluster_count == 1 else f"{window.cluster_id} · {namespace}"
            points_by_key[key][window.timestamp] = rate

    eligible = {
        key: tuple(sorted(points.items()))[-MAX_COST_TREND_POINTS:]
        for key, points in points_by_key.items()
        if len(points) >= 2
    }
    if not eligible:
        return CostUnavailableTrend(
            range=time_range,
            reason_codes=(COST_TREND_HISTORY_INSUFFICIENT,),
        )

    ranked = sorted(
        eligible,
        key=lambda key: (-eligible[key][-1][1], key),
    )
    truncated = len(ranked) > MAX_COST_TREND_SERIES
    direct_limit = MAX_COST_TREND_SERIES - 1 if truncated else MAX_COST_TREND_SERIES
    direct_keys = ranked[:direct_limit]
    series = [_trend_series(key, labels[key], eligible[key]) for key in direct_keys]
    if truncated:
        other_points: dict[int, int] = defaultdict(int)
        for key in ranked[direct_limit:]:
            for timestamp, rate in eligible[key]:
                if other_points[timestamp] <= MAX_SAFE_JSON_INTEGER - rate:
                    other_points[timestamp] += rate
        if len(other_points) >= 2:
            series.append(
                _trend_series(
                    "other",
                    "Other",
                    tuple(sorted(other_points.items()))[-MAX_COST_TREND_POINTS:],
                )
            )
    reasons = _unique_reasons(
        partial_reasons,
        (COST_TREND_SERIES_TRUNCATED,) if truncated else (),
    )
    return CostObservedTrend(
        availability="partial" if reasons else "available",
        range=time_range,
        currency=COST_CURRENCY,
        series=tuple(series),
        reason_codes=reasons,
    )


def _trend_series(
    key: str,
    label: str,
    points: Sequence[tuple[int, int]],
) -> CostTrendSeries:
    return CostTrendSeries(
        key=key,
        label=label,
        points=tuple(
            CostTrendPoint(timestamp=timestamp, rate_micros=rate) for timestamp, rate in points
        ),
    )


def _unique_reasons(*groups: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(reason for group in groups for reason in group if reason))


def cost_scope_coverage(
    *,
    workspace_id: str,
    contexts: Mapping[str, Mapping[str, Any]],
    selected_cluster_ids: Iterable[str],
    namespace_refs: Iterable[tuple[str, str]] = (),
) -> CostScopeCoverage:
    projection = project_snapshot_scope(
        workspace_id=workspace_id,
        contexts=contexts,
        namespace_refs=namespace_refs,
        selected_cluster_ids=selected_cluster_ids,
    )
    return CostScopeCoverage(
        availability=projection.availability,
        scopes=projection.scopes,
        observed_at=projection.observed_at,
        reason_codes=projection.reason_codes,
    )
