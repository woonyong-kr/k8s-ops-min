"""Safe PR 복구의 배포 후 안정화 판정.

이 모듈은 입력 evidence와 내구 lifecycle만 받아 순수하게 판정한다. 시간은 호출자가
주입하므로 테스트에서 sleep이 필요 없고, worker 재시작 뒤에도 healthy_since가
recovery_plans.payload에 남아 동일한 최소 안정화 창을 이어간다.
"""

from __future__ import annotations

import math
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from packages.contracts.event_bus.interfaces import JsonObject

STANDARD_FAILURE_RATIO_METRIC = "opsia_sli_failure_ratio"
STANDARD_REQUEST_RATE_METRIC = "opsia_sli_request_rate"
STANDARD_SLI_ALERT_NAME = "OpsiaSliFailureRatioHigh"
CONTINUITY_ACTIVE_SESSIONS_METRIC = "opsia_continuity_active_sessions"
CONTINUITY_SAMPLE_MAX_AGE_SECONDS = 30
STANDARD_SLI_IDENTITY_LABELS = (
    "namespace",
    "resource_kind",
    "resource_name",
    "service",
    "sli",
    "symptom",
)
RECOVERY_CONTINUITY_LABEL = "opsia.dev/recovery-continuity"
RECOVERY_CONTINUITY_PROTECTED_VALUE = "protected"
DEFAULT_MINIMUM_SECONDS = 300
DEFAULT_MAXIMUM_SECONDS = 600


@dataclass(frozen=True)
class VerificationDecision:
    status: str
    reason_code: str
    reason: str
    after: JsonObject
    healthy_since: str | None
    last_healthy_observed_at: str | None
    distinct_evidence_count: int
    last_evidence_key: str | None


def verification_deadline(started_at: datetime, maximum_seconds: int) -> datetime:
    return started_at + timedelta(seconds=maximum_seconds)


def evaluate_recovery_evidence(
    *,
    plan_payload: Mapping[str, Any],
    lifecycle: Mapping[str, Any],
    evidence: Mapping[str, Any],
    alerts: Sequence[Mapping[str, Any]],
    now: datetime,
) -> VerificationDecision:
    """한 evidence 창을 복구 조건과 대조한다.

    완료는 모든 조건이 연속 ``minimum_seconds`` 이상 유지된 경우뿐이다. 결손이나
    회귀는 healthy_since를 지워 다음 창부터 다시 시간을 재며, ``deadline_at``을
    넘겼을 때만 실패로 종결한다.
    """

    verification = mapping(lifecycle.get("verification"))
    expected = mapping(verification.get("expected"))
    target = dict(mapping(verification.get("target"))) or recovery_target(plan_payload)
    started_at = parse_datetime(verification.get("started_at"))
    deadline_at = parse_datetime(verification.get("deadline_at"))
    minimum_seconds = bounded_seconds(
        verification.get("minimum_seconds"),
        default=DEFAULT_MINIMUM_SECONDS,
        minimum=DEFAULT_MINIMUM_SECONDS,
        maximum=DEFAULT_MAXIMUM_SECONDS,
    )
    evidence_key = text(evidence.get("evidence_key"))
    evidence_observed_at = parse_datetime(evidence.get("window_start"))
    protected_baseline = verification.get("protected_baseline")
    protected_session_baseline = verification.get("protected_session_baseline")
    previous_session_samples = verification.get("last_session_samples")
    after = observed_after(
        evidence,
        target,
        expected,
        protected_baseline=protected_baseline,
        protected_session_baseline=protected_session_baseline,
        previous_session_samples=previous_session_samples,
    )
    checks = verification_checks(
        after=after,
        alerts=alerts,
        before=mapping(verification.get("before")),
        target=target,
        expected=expected,
        protected_baseline=protected_baseline,
        protected_session_baseline=protected_session_baseline,
        verification_started_at=started_at,
    )
    after["checks"] = checks
    after["observed_at"] = normalized_utc(now).isoformat()
    if evidence_observed_at is not None:
        after["evidence_observed_at"] = evidence_observed_at.isoformat()
    if evidence_key:
        after["evidence_ref"] = evidence_key
    previous_key = text(verification.get("last_evidence_key"))
    previous_observed_at = parse_datetime(verification.get("last_healthy_observed_at"))
    distinct_count = nonnegative_int(verification.get("distinct_evidence_count")) or 0
    if evidence_key and evidence_key == previous_key:
        return VerificationDecision(
            status="pending",
            reason_code="duplicate_evidence_window",
            reason="이미 판정한 evidence 창의 재전달은 안정화 시간에 포함하지 않습니다.",
            after=after,
            healthy_since=text(verification.get("healthy_since")) or None,
            last_healthy_observed_at=(
                previous_observed_at.isoformat() if previous_observed_at is not None else None
            ),
            distinct_evidence_count=distinct_count,
            last_evidence_key=previous_key or None,
        )
    if (
        evidence_observed_at is None
        or started_at is None
        or evidence_observed_at < started_at
        or evidence_observed_at > normalized_utc(now)
    ):
        return VerificationDecision(
            status="pending",
            reason_code="stale_evidence_window",
            reason="검증 시작 이후의 trusted window_start를 가진 evidence만 사용합니다.",
            after=after,
            healthy_since=text(verification.get("healthy_since")) or None,
            last_healthy_observed_at=(
                previous_observed_at.isoformat() if previous_observed_at is not None else None
            ),
            distinct_evidence_count=distinct_count,
            last_evidence_key=previous_key or None,
        )

    failed_checks = [name for name, value in checks.items() if value is False]
    missing_checks = [name for name, value in checks.items() if value is None]
    healthy_since = parse_datetime(verification.get("healthy_since"))
    if failed_checks or missing_checks:
        reason_code = "verification_regressed" if failed_checks else "verification_evidence_missing"
        reason = (
            f"복구 검증 조건이 충족되지 않았습니다: {', '.join(failed_checks)}"
            if failed_checks
            else f"복구 검증 근거가 아직 없습니다: {', '.join(missing_checks)}"
        )
        if deadline_at is not None and normalized_utc(now) >= deadline_at:
            return VerificationDecision(
                status="failed",
                reason_code="verification_window_expired",
                reason=f"최대 검증 시간 안에 정상화 근거를 확보하지 못했습니다. {reason}",
                after=after,
                healthy_since=None,
                last_healthy_observed_at=None,
                distinct_evidence_count=0,
                last_evidence_key=evidence_key or previous_key or None,
            )
        return VerificationDecision(
            status="pending",
            reason_code=reason_code,
            reason=reason,
            after=after,
            healthy_since=None,
            last_healthy_observed_at=None,
            distinct_evidence_count=0,
            last_evidence_key=evidence_key or previous_key or None,
        )

    # 설치 시 실제로 승인된 evidence cadence를 사용한다. cadence가 누락되면
    # 임의 상수로 안정화 시간을 추정하지 않고 fail closed 한다.
    cadence_seconds = nonnegative_int(expected.get("evidence_cadence_seconds"))
    if cadence_seconds is None or cadence_seconds <= 0:
        return VerificationDecision(
            status="pending",
            reason_code="verification_evidence_missing",
            reason="설치 정책의 evidence 수집 주기를 확인할 수 없어 안정화 시간을 계산하지 않습니다.",
            after=after,
            healthy_since=None,
            last_healthy_observed_at=None,
            distinct_evidence_count=0,
            last_evidence_key=evidence_key or previous_key or None,
        )
    max_gap_seconds = cadence_seconds * 1.5
    gap_seconds = (
        (evidence_observed_at - previous_observed_at).total_seconds()
        if previous_observed_at is not None
        else 0.0
    )
    if previous_observed_at is not None and (
        gap_seconds <= 0 or gap_seconds > max_gap_seconds
    ):
        healthy_since = None
        distinct_count = 0
    stable_from = healthy_since or evidence_observed_at
    distinct_count += 1
    stable_seconds = max(0.0, (evidence_observed_at - stable_from).total_seconds())
    after["healthy_since"] = stable_from.isoformat()
    after["stable_seconds"] = stable_seconds
    after["distinct_evidence_count"] = distinct_count
    minimum_distinct = max(2, math.ceil(minimum_seconds / cadence_seconds) + 1)
    if stable_seconds < minimum_seconds or distinct_count < minimum_distinct:
        if deadline_at is not None and normalized_utc(now) >= deadline_at:
            return VerificationDecision(
                status="failed",
                reason_code="verification_window_expired",
                reason="최대 검증 시간 안에 최소 연속 정상화 시간을 충족하지 못했습니다.",
                after=after,
                healthy_since=stable_from.isoformat(),
                last_healthy_observed_at=evidence_observed_at.isoformat(),
                distinct_evidence_count=distinct_count,
                last_evidence_key=evidence_key or previous_key or None,
            )
        return VerificationDecision(
            status="pending",
            reason_code="stabilization_window_in_progress",
            reason=(
                f"정상화 조건이 {int(stable_seconds)}초 유지됐습니다. "
                f"최소 {minimum_seconds}초 연속 관측 후 완료합니다."
            ),
            after=after,
            healthy_since=stable_from.isoformat(),
            last_healthy_observed_at=evidence_observed_at.isoformat(),
            distinct_evidence_count=distinct_count,
            last_evidence_key=evidence_key or previous_key or None,
        )
    return VerificationDecision(
        status="completed",
        reason_code="recovery_verified",
        reason=(
            "동일 부하에서 실패율·Deployment replica·Alertmanager 해소 상태가 "
            f"{minimum_seconds}초 이상 안정적으로 유지됐습니다."
        ),
        after=after,
        healthy_since=stable_from.isoformat(),
        last_healthy_observed_at=evidence_observed_at.isoformat(),
        distinct_evidence_count=distinct_count,
        last_evidence_key=evidence_key or previous_key or None,
    )


def observed_after(
    evidence: Mapping[str, Any],
    target: Mapping[str, str],
    expected: Mapping[str, Any] | None = None,
    *,
    protected_baseline: object = None,
    protected_session_baseline: object = None,
    previous_session_samples: object = None,
) -> JsonObject:
    expected_values = expected or {}
    return {
        "failure_ratio": metric_sample(
            evidence,
            STANDARD_FAILURE_RATIO_METRIC,
            target,
            expected_identity=mapping(
                expected_values.get("failure_ratio_metric_identity")
            ),
        ),
        "request_rate": metric_sample(
            evidence,
            STANDARD_REQUEST_RATE_METRIC,
            target,
            expected_identity=mapping(
                expected_values.get("request_rate_metric_identity")
            ),
        ),
        "deployment": deployment_replicas(evidence, target),
        "protected_workloads": protected_workloads(
            evidence,
            target,
            baseline=protected_baseline,
        ),
        "protected_active_sessions": protected_active_session_series(
            evidence,
            protected_baseline,
            expected_baseline=protected_session_baseline,
            previous_samples=previous_session_samples or protected_session_baseline,
            evidence_observed_at=parse_datetime(evidence.get("window_start")),
            max_sample_age_seconds=CONTINUITY_SAMPLE_MAX_AGE_SECONDS,
        ),
    }


def verification_checks(
    *,
    after: Mapping[str, Any],
    alerts: Sequence[Mapping[str, Any]],
    before: Mapping[str, Any],
    target: Mapping[str, str],
    expected: Mapping[str, Any],
    protected_baseline: object,
    protected_session_baseline: object,
    verification_started_at: datetime | None,
) -> JsonObject:
    failure_ratio = finite_float(after.get("failure_ratio"))
    request_rate = finite_float(after.get("request_rate"))
    failure_ratio_max = finite_float(expected.get("failure_ratio_max"))
    request_rate_baseline = finite_float(expected.get("request_rate_baseline"))
    request_rate_tolerance_ratio = finite_float(
        expected.get("request_rate_tolerance_ratio")
    )
    replicas = nonnegative_int(expected.get("replicas"))
    deployment = mapping(after.get("deployment"))
    desired = nonnegative_int(deployment.get("desired_replicas"))
    ready = nonnegative_int(deployment.get("ready_replicas"))
    updated = nonnegative_int(deployment.get("updated_replicas"))
    available = nonnegative_int(deployment.get("available_replicas"))
    unavailable = nonnegative_int(deployment.get("unavailable_replicas"))
    unavailable_omitted = deployment.get("unavailable_replicas_omitted") is True
    generation = nonnegative_int(deployment.get("generation"))
    observed_generation = nonnegative_int(deployment.get("observed_generation"))
    omitted_unavailable_means_zero = (
        unavailable_omitted
        and unavailable is None
        and replicas is not None
        and desired == replicas
        and ready == replicas
        and updated == replicas
        and available == replicas
        and generation is not None
        and observed_generation is not None
        and observed_generation >= generation
    )
    protected_expected = nonnegative_int(expected.get("protected_workloads")) or 0
    protected = after.get("protected_workloads")
    protected_count = len(protected) if isinstance(protected, list) else None
    protected_healthy = (
        all(item.get("healthy") is True for item in protected)
        if isinstance(protected, list)
        else None
    )
    protected_unchanged = protected_workloads_unchanged(
        protected_baseline,
        protected,
    )
    protected_session_expected = (
        len(protected_session_baseline)
        if isinstance(protected_session_baseline, list)
        else None
    )
    protected_sessions = after.get("protected_active_sessions")
    protected_session_count = (
        len(protected_sessions) if isinstance(protected_sessions, list) else None
    )
    protected_sessions_maintained = protected_active_sessions_maintained(
        protected_session_baseline,
        protected_sessions,
    )
    alert_resolved, no_refire = alert_checks(
        alerts,
        before,
        target,
        verification_started_at,
    )
    return {
        "failure_ratio_below_threshold": (
            failure_ratio <= failure_ratio_max
            if failure_ratio is not None and failure_ratio_max is not None
            else None
        ),
        "request_rate_near_baseline": (
            abs(request_rate - request_rate_baseline)
            <= request_rate_baseline * request_rate_tolerance_ratio
            if (
                request_rate is not None
                and request_rate_baseline is not None
                and request_rate_baseline > 0
                and request_rate_tolerance_ratio is not None
                and 0 <= request_rate_tolerance_ratio < 1
            )
            else None
        ),
        "desired_replicas_restored": (
            desired == replicas if desired is not None and replicas is not None else None
        ),
        "ready_replicas_restored": (
            ready == replicas if ready is not None and replicas is not None else None
        ),
        "updated_replicas_restored": (
            updated == replicas if updated is not None and replicas is not None else None
        ),
        "available_replicas_restored": (
            available == replicas if available is not None and replicas is not None else None
        ),
        "unavailable_replicas_zero": (
            unavailable == 0
            if unavailable is not None
            else True
            if omitted_unavailable_means_zero
            else None
        ),
        "deployment_generation_observed": (
            observed_generation == generation
            if observed_generation is not None and generation is not None
            else None
        ),
        "protected_workloads_present": (
            protected_count >= protected_expected
            if protected_expected > 0 and protected_count is not None
            else True
            if protected_expected == 0
            else None
        ),
        "protected_workloads_healthy": (
            protected_healthy
            if protected_expected > 0
            else True
        ),
        "protected_workloads_uninterrupted": (
            protected_unchanged
            if protected_expected > 0
            else True
        ),
        "protected_active_session_series_present": (
            protected_session_count == protected_session_expected
            if protected_expected > 0
            and protected_session_count is not None
            and protected_session_expected is not None
            else True
            if protected_expected == 0
            else None
        ),
        "protected_active_sessions_maintained": (
            protected_sessions_maintained
            if protected_expected > 0
            else True
        ),
        "alertmanager_resolved": alert_resolved,
        "alertmanager_no_refire": no_refire,
    }


def alert_checks(
    alerts: Sequence[Mapping[str, Any]],
    before: Mapping[str, Any],
    target: Mapping[str, str],
    verification_started_at: datetime | None,
) -> tuple[bool | None, bool | None]:
    original_id = text(before.get("alert_event_id"))
    original: Mapping[str, Any] | None = None
    refired = False
    for alert in alerts:
        if not alert_matches_original_series(alert, before, target):
            continue
        event_id = text(alert.get("event_id"))
        if original_id and event_id == original_id:
            original = alert
            continue
        fired_at = parse_datetime(alert.get("fired_at"))
        if (
            verification_started_at is not None
            and fired_at is not None
            and fired_at >= verification_started_at
        ):
            # 새 exact occurrence가 잠깐 firing 후 이미 resolved 상태여도 검증
            # 구간의 재발이다. 현재 상태만 보면 짧은 refire를 놓치므로 fired_at을
            # 기준으로 fail closed 한다.
            refired = True
    if original is None:
        return None, False if refired else None
    resolved = (
        text(original.get("status")).casefold() == "resolved"
        and parse_datetime(original.get("resolved_at")) is not None
    )
    return resolved, not refired


def before_alert_snapshot(
    alerts: Sequence[Mapping[str, Any]],
    *,
    target: Mapping[str, str],
    correlation_id: str,
    incident_id: str,
    expected_series_identity: Mapping[str, Any] | None = None,
) -> JsonObject:
    """Capture one exact Alertmanager SLI occurrence before recovery.

    Alertmanager resolution updates the durable occurrence in place. Therefore an
    exact occurrence that fired for this incident remains valid after it reaches
    the terminal ``resolved`` state, provided its firing/resolution chronology is
    complete. This lets an operator start recovery after the alert clears without
    weakening target, incident, threshold, or series-identity checks.
    """

    expected_identity = (
        standard_sli_series_identity(expected_series_identity)
        if expected_series_identity is not None
        else None
    )
    if expected_series_identity is not None and expected_identity is None:
        return {
            "available": False,
            "reason_code": "pre_recovery_alert_missing",
        }

    matches: list[Mapping[str, Any]] = []
    for alert in alerts:
        if not alert_matches_target(alert, target):
            continue
        if text(alert.get("incident_id")) not in {correlation_id, incident_id}:
            continue
        if (
            text(alert.get("source")).casefold() != "alertmanager"
            or text(alert.get("rule_name")) != STANDARD_SLI_ALERT_NAME
        ):
            continue
        status = text(alert.get("status")).casefold()
        if status not in {"firing", "acked", "resolved"}:
            continue
        if status == "resolved":
            fired_at = parse_datetime(alert.get("fired_at"))
            resolved_at = parse_datetime(alert.get("resolved_at"))
            if (
                fired_at is None
                or resolved_at is None
                or resolved_at < fired_at
            ):
                continue
        elif parse_datetime(alert.get("fired_at")) is None:
            continue
        observed = finite_float(alert.get("observed_value"))
        threshold = finite_float(alert.get("threshold"))
        if observed is None or threshold is None or observed <= threshold:
            continue
        series_identity = standard_sli_series_identity(
            alert.get("series_identity")
        )
        if series_identity is None or not metric_labels_match_target(
            series_identity,
            target,
        ):
            continue
        if expected_identity is not None and series_identity != expected_identity:
            continue
        matches.append(alert)
    if len(matches) != 1:
        return {
            "available": False,
            "reason_code": (
                "pre_recovery_alert_missing"
                if not matches
                else "pre_recovery_alert_ambiguous"
            ),
        }
    alert = matches[0]
    return {
        "available": True,
        "alert_event_id": alert.get("event_id"),
        "rule_id": alert.get("rule_id"),
        "rule_name": alert.get("rule_name"),
        "source": alert.get("source"),
        "subject_key": alert.get("subject_key"),
        "series_identity": alert.get("series_identity"),
        "observed_value": alert.get("observed_value"),
        "threshold": alert.get("threshold"),
        "fired_at": alert.get("fired_at"),
        "subject": alert.get("subject"),
    }


def alert_matches_original_series(
    alert: Mapping[str, Any],
    before: Mapping[str, Any],
    target: Mapping[str, str],
) -> bool:
    """Match the exact pre-recovery Alertmanager occurrence series."""

    if not alert_matches_target(alert, target):
        return False
    expected_series_identity = standard_sli_series_identity(
        before.get("series_identity")
    )
    current_series_identity = standard_sli_series_identity(
        alert.get("series_identity")
    )
    if text(before.get("rule_name")) == STANDARD_SLI_ALERT_NAME:
        if (
            expected_series_identity is None
            or current_series_identity != expected_series_identity
        ):
            return False
    expected_fields = ("rule_id", "rule_name", "source", "subject_key")
    return all(
        not text(before.get(field))
        or text(alert.get(field)) == text(before.get(field))
        for field in expected_fields
    )


def standard_sli_series_identity(value: object) -> JsonObject | None:
    """Return the exact six-label identity of the standard admission SLI."""

    raw = mapping(value)
    identity: JsonObject = {
        "namespace": text(raw.get("namespace")),
        "resource_kind": text(raw.get("resource_kind")),
        "resource_name": text(raw.get("resource_name")),
        "service": text(raw.get("service")),
        "sli": text(raw.get("sli")),
        "symptom": text(raw.get("symptom")),
    }
    return identity if all(identity.values()) else None


def alert_matches_target(alert: Mapping[str, Any], target: Mapping[str, str]) -> bool:
    subject = mapping(alert.get("subject"))
    return (
        text(subject.get("cluster")) == target.get("cluster_id", "")
        and text(subject.get("namespace")) == target.get("namespace", "")
        and text(subject.get("kind")).casefold() == target.get("resource_kind", "").casefold()
        and text(subject.get("name")) == target.get("resource_name", "")
    )


def metric_sample(
    evidence: Mapping[str, Any],
    metric_name: str,
    target: Mapping[str, str],
    *,
    expected_identity: Mapping[str, Any] | None = None,
) -> float | None:
    value, _identity = metric_sample_with_identity(
        evidence,
        metric_name,
        target,
        expected_identity=expected_identity,
    )
    return value


def metric_sample_with_identity(
    evidence: Mapping[str, Any],
    metric_name: str,
    target: Mapping[str, str],
    *,
    expected_identity: Mapping[str, Any] | None = None,
) -> tuple[float | None, JsonObject | None]:
    metrics = mapping(evidence.get("metrics"))
    result = mapping(mapping(metrics.get("results")).get(metric_name))
    samples = result.get("samples")
    if not isinstance(samples, list):
        return None, None
    matched: list[tuple[float, JsonObject]] = []
    for sample in samples:
        if not isinstance(sample, Mapping):
            continue
        labels = mapping(sample.get("metric"))
        if not exact_standard_sli_label_set(labels, metric_name):
            continue
        if not metric_labels_match_target(labels, target):
            continue
        identity = metric_identity(labels)
        if identity is None:
            continue
        if expected_identity and any(
            text(identity.get(key)) != text(value)
            for key, value in expected_identity.items()
        ):
            continue
        value = finite_float(sample.get("value"))
        if value is not None:
            matched.append((value, identity))
    return matched[0] if len(matched) == 1 else (None, None)


def exact_standard_sli_label_set(
    labels: Mapping[str, Any],
    metric_name: str,
) -> bool:
    """Accept only the aggregate six-label SLI vector.

    During a rolling migration Prometheus can retain legacy raw application
    series under the same metric name. Raw series carry Pod/scrape labels and
    must not compete with the recording-rule output consumed by RCA. A bare
    aggregation omits ``__name__`` while a recording-rule selector may preserve
    it, so both exact shapes are valid.
    """

    keys = {str(key) for key in labels}
    expected = set(STANDARD_SLI_IDENTITY_LABELS)
    if keys == expected:
        return True
    return (
        keys == {*expected, "__name__"}
        and text(labels.get("__name__")) == metric_name
    )


def metric_identity(labels: Mapping[str, Any]) -> JsonObject | None:
    """Return the immutable SLI series identity used before and after recovery."""

    identity = {key: text(labels.get(key)) for key in STANDARD_SLI_IDENTITY_LABELS}
    if any(not value for value in identity.values()):
        return None
    return identity


def metric_labels_match_target(
    labels: Mapping[str, Any],
    target: Mapping[str, str],
) -> bool:
    return (
        text(labels.get("namespace")) == target.get("namespace", "")
        and text(labels.get("resource_kind")).casefold()
        == target.get("resource_kind", "").casefold()
        and text(labels.get("resource_name")) == target.get("resource_name", "")
    )


def deployment_replicas(
    evidence: Mapping[str, Any],
    target: Mapping[str, str],
) -> JsonObject | None:
    metadata = mapping(evidence.get("metadata"))
    snapshots = metadata.get("current_workload_snapshots")
    if not isinstance(snapshots, list):
        change_context = mapping(metadata.get("change_context"))
        snapshots = change_context.get("current_workload_snapshots")
    if not isinstance(snapshots, list):
        return None
    matches: list[JsonObject] = []
    for snapshot in snapshots:
        if not isinstance(snapshot, Mapping):
            continue
        workload = mapping(snapshot.get("workload"))
        if (
            text(workload.get("kind")).casefold()
            != target.get("resource_kind", "").casefold()
            or text(workload.get("namespace")) != target.get("namespace", "")
            or text(workload.get("name")) != target.get("resource_name", "")
        ):
            continue
        status = mapping(snapshot.get("deployment_status"))
        matches.append(
            {
                "generation": nonnegative_int(workload.get("generation")),
                "observed_generation": nonnegative_int(status.get("observed_generation")),
                "desired_replicas": nonnegative_int(status.get("desired_replicas")),
                "ready_replicas": nonnegative_int(status.get("ready_replicas")),
                "updated_replicas": nonnegative_int(status.get("updated_replicas")),
                "available_replicas": nonnegative_int(status.get("available_replicas")),
                "unavailable_replicas": nonnegative_int(status.get("unavailable_replicas")),
                "unavailable_replicas_omitted": (
                    "unavailable_replicas" not in status
                ),
            }
        )
    return matches[0] if len(matches) == 1 else None


def protected_workloads(
    evidence: Mapping[str, Any],
    target: Mapping[str, str],
    *,
    baseline: object = None,
) -> list[JsonObject] | None:
    """Observe exact pre-recovery workload identities without demo name rules.

    At PR preflight time, only healthy peer Deployments explicitly opted in with
    ``opsia.dev/recovery-continuity=protected`` are snapshotted. During
    verification, only those persisted identities are selected. The recovery
    target itself is excluded because its rollout is validated separately by
    ``deployment_replicas``.
    """

    metadata = mapping(evidence.get("metadata"))
    snapshots = metadata.get("current_workload_snapshots")
    if not isinstance(snapshots, list):
        snapshots = mapping(metadata.get("change_context")).get("current_workload_snapshots")
    if not isinstance(snapshots, list):
        return None
    expected_identities: set[tuple[str, str, str]] | None = None
    if isinstance(baseline, list):
        expected_identities = {
            (
                text(item.get("kind")).casefold(),
                text(item.get("namespace")),
                text(item.get("name")),
            )
            for item in baseline
            if isinstance(item, Mapping)
        }
    workloads: list[JsonObject] = []
    for snapshot in snapshots:
        if not isinstance(snapshot, Mapping):
            continue
        workload = mapping(snapshot.get("workload"))
        name = text(workload.get("name"))
        kind = text(workload.get("kind"))
        namespace = text(workload.get("namespace"))
        identity = (kind.casefold(), namespace, name)
        if namespace != target.get("namespace", "") or kind.casefold() != "deployment":
            continue
        if (
            kind.casefold() == target.get("resource_kind", "").casefold()
            and name == target.get("resource_name", "")
        ):
            continue
        if expected_identities is not None and identity not in expected_identities:
            continue
        if expected_identities is None:
            labels = mapping(snapshot.get("deployment_labels"))
            pod_template_labels = mapping(snapshot.get("pod_template_labels"))
            if (
                text(labels.get(RECOVERY_CONTINUITY_LABEL)).casefold()
                != RECOVERY_CONTINUITY_PROTECTED_VALUE
                or text(
                    pod_template_labels.get(RECOVERY_CONTINUITY_LABEL)
                ).casefold()
                != RECOVERY_CONTINUITY_PROTECTED_VALUE
            ):
                continue
        status = mapping(snapshot.get("deployment_status"))
        desired = nonnegative_int(status.get("desired_replicas"))
        ready = nonnegative_int(status.get("ready_replicas"))
        available = nonnegative_int(status.get("available_replicas"))
        pods = snapshot.get("pod_statuses")
        pod_values = (
            [pod for pod in pods if isinstance(pod, Mapping)]
            if isinstance(pods, list)
            else []
        )
        # A continuity-protected workload can intentionally retain its serving
        # Pod while a rolling-update candidate is Pending or unready. The
        # candidate is not carrying an active session and must not make the
        # pre-recovery baseline disappear. Persist only the exact serving Pods;
        # post-merge verification still requires those identities to remain.
        serving_pods = [
            pod
            for pod in pod_values
            if pod.get("ready") is True and not text(pod.get("deletion_timestamp"))
        ]
        workloads.append(
            {
                "kind": kind,
                "namespace": namespace,
                "name": name,
                "uid": workload.get("uid"),
                "pod_uids": sorted(
                    text(pod.get("uid"))
                    for pod in serving_pods
                    if text(pod.get("uid"))
                ),
                "pod_start_times": sorted(
                    text(pod.get("start_time"))
                    for pod in serving_pods
                    if text(pod.get("start_time"))
                ),
                "restart_count": sum(
                    nonnegative_int(pod.get("restart_count")) or 0
                    for pod in serving_pods
                ),
                "healthy": bool(
                    desired is not None
                    and desired > 0
                    and ready is not None
                    and ready >= desired
                    and available is not None
                    and available >= desired
                    and len(serving_pods) >= desired
                    and snapshot.get("pod_statuses_truncated") is not True
                ),
            }
        )
    return sorted(
        workloads,
        key=lambda item: (
            str(item["namespace"]),
            str(item["kind"]),
            str(item["name"]),
        ),
    )


def protected_workloads_unchanged(
    baseline: object,
    current: object,
) -> bool | None:
    if not isinstance(current, list):
        return None
    if baseline in (None, []):
        # Post-deploy evidence로 baseline을 새로 만들면 rollout 중 재시작을
        # 놓칠 수 있으므로 pre-recovery baseline이 없을 때는 fail closed 한다.
        return None
    if not isinstance(baseline, list):
        return None
    def normalized(values: list[object]) -> list[JsonObject]:
        return [
            {
                "name": item.get("name"),
                "kind": item.get("kind"),
                "namespace": item.get("namespace"),
                "uid": item.get("uid"),
                "pod_uids": item.get("pod_uids"),
                "pod_start_times": item.get("pod_start_times"),
                "restart_count": item.get("restart_count"),
            }
            for item in values
            if isinstance(item, Mapping)
        ]

    return normalized(baseline) == normalized(current)


def protected_workload_baseline(
    workloads: Sequence[Mapping[str, Any]],
) -> list[JsonObject]:
    """Persist exact identities/counters for healthy pre-recovery workloads."""

    baseline = [
        {
            "kind": workload.get("kind"),
            "namespace": workload.get("namespace"),
            "name": workload.get("name"),
            "uid": workload.get("uid"),
            "pod_uids": workload.get("pod_uids"),
            "pod_start_times": workload.get("pod_start_times"),
            "restart_count": workload.get("restart_count"),
        }
        for workload in workloads
        if workload.get("healthy") is True
    ]
    identities = [
        (
            text(item.get("kind")).casefold(),
            text(item.get("namespace")),
            text(item.get("name")),
        )
        for item in baseline
    ]
    workload_uids = [text(item.get("uid")) for item in baseline]
    pod_uids = [
        text(pod_uid)
        for item in baseline
        for pod_uid in (
            item.get("pod_uids") if isinstance(item.get("pod_uids"), list) else []
        )
    ]
    valid_pod_metadata = all(
        isinstance(item.get("pod_uids"), list)
        and bool(item["pod_uids"])
        and isinstance(item.get("pod_start_times"), list)
        and len(item["pod_start_times"]) == len(item["pod_uids"])
        and all(text(value) for value in item["pod_start_times"])
        and nonnegative_int(item.get("restart_count")) is not None
        for item in baseline
    )
    if (
        not baseline
        or any(not all(identity) for identity in identities)
        or len(set(identities)) != len(identities)
        or any(not uid for uid in workload_uids)
        or len(set(workload_uids)) != len(workload_uids)
        or any(not uid for uid in pod_uids)
        or len(set(pod_uids)) != len(pod_uids)
        or not valid_pod_metadata
    ):
        return []
    return baseline


def protected_active_session_series(
    evidence: Mapping[str, Any],
    protected_baseline: object,
    *,
    expected_baseline: object = None,
    previous_samples: object = None,
    evidence_observed_at: datetime | None = None,
    max_sample_age_seconds: int | None = None,
) -> list[JsonObject] | None:
    """Read one active-session gauge series for every protected workload."""

    if not isinstance(protected_baseline, list) or not protected_baseline:
        return None
    protected_by_workload = {
        (
            text(item.get("namespace")),
            text(item.get("kind")).casefold(),
            text(item.get("name")),
        ): item
        for item in protected_baseline
        if isinstance(item, Mapping)
    }
    if len(protected_by_workload) != len(protected_baseline):
        return None
    expected_series: dict[tuple[str, str, str, str, str], float] | None = None
    if isinstance(expected_baseline, list):
        expected_series = {
            (
                text(item.get("namespace")),
                text(item.get("kind")).casefold(),
                text(item.get("name")),
                text(item.get("continuity_id")),
                text(item.get("pod_uid")),
            ): value
            for item in expected_baseline
            if isinstance(item, Mapping)
            and (value := finite_float(item.get("value"))) is not None
        }
    previous_timestamps: dict[tuple[str, str, str, str, str], float] | None = None
    if isinstance(previous_samples, list):
        previous_timestamps = {
            (
                text(item.get("namespace")),
                text(item.get("kind")).casefold(),
                text(item.get("name")),
                text(item.get("continuity_id")),
                text(item.get("pod_uid")),
            ): timestamp
            for item in previous_samples
            if isinstance(item, Mapping)
            and (timestamp := finite_float(item.get("sample_timestamp"))) is not None
        }
    if (
        evidence_observed_at is None
        or max_sample_age_seconds is None
        or max_sample_age_seconds <= 0
    ):
        return None
    if expected_series is not None and (
        len(expected_series) != len(expected_baseline)
        or len({identity[3] for identity in expected_series}) != len(expected_series)
        or len({identity[4] for identity in expected_series}) != len(expected_series)
        or
        previous_timestamps is None
        or set(previous_timestamps) != set(expected_series)
    ):
        return None
    result = mapping(
        mapping(mapping(evidence.get("metrics")).get("results")).get(
            CONTINUITY_ACTIVE_SESSIONS_METRIC
        )
    )
    samples = result.get("samples")
    if not isinstance(samples, list):
        return None
    matches: dict[tuple[str, str, str, str, str], list[tuple[float, float]]] = {}
    for sample in samples:
        if not isinstance(sample, Mapping):
            continue
        labels = mapping(sample.get("metric"))
        workload_identity = (
            text(labels.get("namespace")),
            text(labels.get("resource_kind")).casefold(),
            text(labels.get("resource_name")),
        )
        continuity_id = text(labels.get("continuity_id"))
        pod_uid = text(labels.get("pod_uid"))
        identity = (*workload_identity, continuity_id, pod_uid)
        protected = protected_by_workload.get(workload_identity)
        pod_uids = protected.get("pod_uids") if isinstance(protected, Mapping) else None
        if (
            protected is None
            or not continuity_id
            or not pod_uid
            or not isinstance(pod_uids, list)
            or pod_uid not in {text(value) for value in pod_uids}
            or (expected_series is not None and identity not in expected_series)
        ):
            continue
        value = finite_float(sample.get("value"))
        sample_timestamp = finite_float(sample.get("timestamp"))
        if (
            value is None
            or value <= 0
            or not value.is_integer()
            or sample_timestamp is None
        ):
            continue
        try:
            sample_time = datetime.fromtimestamp(sample_timestamp, tz=UTC)
        except (OverflowError, OSError, ValueError):
            continue
        sample_age = abs(
            (normalized_utc(evidence_observed_at) - sample_time).total_seconds()
        )
        if sample_age > max_sample_age_seconds:
            continue
        if (
            previous_timestamps is not None
            and identity in previous_timestamps
            and sample_timestamp < previous_timestamps[identity]
        ):
            continue
        matches.setdefault(identity, []).append((value, sample_timestamp))
    if any(len(values) != 1 for values in matches.values()):
        return None
    if expected_series is not None:
        if set(matches) != set(expected_series):
            return None
    else:
        matched_workloads = {identity[:3] for identity in matches}
        if (
            matched_workloads != set(protected_by_workload)
            or len(matches) != len(protected_by_workload)
        ):
            return None
    if (
        len({identity[3] for identity in matches}) != len(matches)
        or len({identity[4] for identity in matches}) != len(matches)
    ):
        return None
    return [
        {
            "namespace": namespace,
            "kind": text(protected_by_workload[(namespace, kind, name)].get("kind")),
            "name": name,
            "continuity_id": continuity_id,
            "pod_uid": pod_uid,
            "value": matches[(namespace, kind, name, continuity_id, pod_uid)][0][0],
            "sample_timestamp": matches[
                (namespace, kind, name, continuity_id, pod_uid)
            ][0][1],
        }
        for namespace, kind, name, continuity_id, pod_uid in sorted(matches)
    ]


def protected_active_sessions_maintained(
    baseline: object,
    current: object,
) -> bool | None:
    if not isinstance(baseline, list) or not baseline or not isinstance(current, list):
        return None

    def values(
        items: list[object],
    ) -> dict[tuple[str, str, str, str, str], float] | None:
        parsed: dict[tuple[str, str, str, str, str], float] = {}
        for item in items:
            if not isinstance(item, Mapping):
                return None
            identity = (
                text(item.get("namespace")),
                text(item.get("kind")).casefold(),
                text(item.get("name")),
                text(item.get("continuity_id")),
                text(item.get("pod_uid")),
            )
            value = finite_float(item.get("value"))
            if not all(identity) or value is None or identity in parsed:
                return None
            parsed[identity] = value
        return parsed

    before = values(baseline)
    after = values(current)
    if before is None or after is None or before.keys() != after.keys():
        return None
    # 신규 session 증가는 허용하지만, 어느 protected identity도 복구 전보다
    # active session 수가 줄어든 창은 "무중단"으로 판정하지 않는다.
    return all(after[identity] >= value for identity, value in before.items())


def recovery_target(plan_payload: Mapping[str, Any]) -> dict[str, str]:
    target = mapping(plan_payload.get("target"))
    candidates = plan_payload.get("candidates")
    selected_id = text(plan_payload.get("recommended_action_id"))
    selected: Mapping[str, Any] = {}
    if isinstance(candidates, list):
        for candidate in candidates:
            if isinstance(candidate, Mapping) and text(candidate.get("action_id")) == selected_id:
                selected = candidate
                break
    draft = mapping(selected.get("draft"))
    return {
        "cluster_id": text(target.get("cluster_id")),
        "namespace": text(target.get("namespace")) or text(draft.get("namespace")),
        "resource_kind": text(target.get("resource_kind")) or text(draft.get("resource_kind")),
        "resource_name": text(target.get("resource_name")) or text(draft.get("resource_name")),
    }


def bounded_seconds(value: object, *, default: int, minimum: int, maximum: int) -> int:
    parsed = nonnegative_int(value)
    if parsed is None:
        return default
    return max(minimum, min(parsed, maximum))


def mapping(value: object) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def finite_float(value: object) -> float | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = float(value)
    except (TypeError, ValueError):
        return None
    return parsed if math.isfinite(parsed) else None


def nonnegative_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        parsed = int(value)
    except (TypeError, ValueError):
        return None
    return parsed if parsed >= 0 else None


def parse_datetime(value: object) -> datetime | None:
    if isinstance(value, datetime):
        return normalized_utc(value)
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    return normalized_utc(parsed)


def trusted_evidence_window_start(
    evidence: Mapping[str, Any],
    *,
    expected_workspace_id: str = "",
    expected_cluster_id: str = "",
) -> datetime | None:
    """Read the collection time from raw or EvidenceBuilder-persisted evidence.

    Raw ``ClusterEvidenceReceivedBody`` values carry ``window_start`` at the
    top level. Persisted ``Evidence.to_body()`` values carry the original
    identity in each non-empty source's ``_lineage`` instead. A persisted
    metrics window is trusted only when all available source lineages agree.
    """

    top_window_start = parse_datetime(evidence.get("window_start"))
    top_workspace_id = text(evidence.get("workspace_id"))
    top_cluster_id = text(evidence.get("cluster_id"))
    top_evidence_key = text(evidence.get("evidence_key"))
    if (
        expected_workspace_id
        and top_workspace_id
        and top_workspace_id != expected_workspace_id
    ):
        return None
    if (
        expected_cluster_id
        and top_cluster_id
        and top_cluster_id != expected_cluster_id
    ):
        return None

    lineages = evidence_source_lineages(evidence)
    if not lineages:
        if (
            top_window_start is None
            or (expected_workspace_id and top_workspace_id != expected_workspace_id)
            or (expected_cluster_id and top_cluster_id != expected_cluster_id)
        ):
            return None
        return top_window_start

    metrics_lineage = mapping(mapping(evidence.get("metrics")).get("_lineage"))
    if not metrics_lineage:
        return None
    identities: list[tuple[str, str, str, datetime]] = []
    for lineage in lineages:
        workspace_id = text(lineage.get("workspace_id"))
        cluster_id = text(lineage.get("cluster_id"))
        evidence_key = text(lineage.get("evidence_key"))
        window_start = parse_datetime(lineage.get("window_start"))
        if not workspace_id or not cluster_id or not evidence_key or window_start is None:
            return None
        identities.append((workspace_id, cluster_id, evidence_key, window_start))
    if len(set(identities)) != 1:
        return None
    workspace_id, cluster_id, evidence_key, window_start = identities[0]
    if (
        (expected_workspace_id and workspace_id != expected_workspace_id)
        or (expected_cluster_id and cluster_id != expected_cluster_id)
        or (top_workspace_id and workspace_id != top_workspace_id)
        or (top_cluster_id and cluster_id != top_cluster_id)
        or (top_evidence_key and evidence_key != top_evidence_key)
        or (top_window_start is not None and window_start != top_window_start)
    ):
        return None
    return window_start


def evidence_source_lineages(
    evidence: Mapping[str, Any],
) -> list[Mapping[str, Any]]:
    lineages: list[Mapping[str, Any]] = []
    for source in ("kubernetes", "metrics", "traces", "metadata"):
        payload = evidence.get(source)
        if not isinstance(payload, Mapping):
            continue
        lineage = payload.get("_lineage")
        if isinstance(lineage, Mapping):
            lineages.append(lineage)
    logs = evidence.get("logs")
    if isinstance(logs, list):
        for entry in logs:
            if not isinstance(entry, Mapping):
                continue
            lineage = entry.get("_lineage")
            if isinstance(lineage, Mapping):
                lineages.append(lineage)
    return lineages


def normalized_utc(value: datetime) -> datetime:
    return (
        value.replace(tzinfo=UTC)
        if value.tzinfo is None
        else value.astimezone(UTC)
    )
