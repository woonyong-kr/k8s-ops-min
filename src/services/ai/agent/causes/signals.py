"""RCA 판별 신호(signal) 평가 — 근거 번들 내용으로 원인 후보를 실제로 구별한다.

배경: 기존 점수는 "근거 소스가 존재하는가"만 봐서, kubernetes/metrics/logs 가 다 있으면
아무 후보나(예: exit code 1 크래시에 oom_killed) 1.0 으로 완결되는 오판이 있었다.
이 모듈은 카탈로그 후보의 `signals` 그룹을 근거 번들 **내용**과 대조한다.

DSL(카탈로그 YAML `signals`) — 그룹 목록이며, 그룹마다 `any_of` matcher 중 하나라도
매칭되면 충족. 선언된 그룹이 모두 충족돼야 후보가 완결 점수(1.0)에 도달할 수 있다.

matcher 종류(정확히 하나의 키만 사용):
- `fact`: kubernetes snapshot 에서 뽑은 정규화 토큰과 일치.
  토큰 어휘: `waiting_reason=<r>`, `terminated_reason=<r>`, `event_reason=<r>`,
  `exit_code=<n>`, `pod_label:<key>=<value>`, 그리고 파생 토큰
  `exit_code=non_oom`(0/137 이 아닌 종료 코드 관측).
- `log_pattern`: 수집 로그 라인(단순 line + Loki streams.values.line) 대소문자 무시 부분일치.
- `event_pattern`: warning 이벤트 "reason message" 문자열 대소문자 무시 부분일치.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime

from domains.rca.events import EvidenceBundle
from packages.contracts.event_bus.bodies import JsonObject

# matcher 키 어휘 — 로더(causes/loader.py) 스키마 검증과 평가가 같은 표를 쓴다.
MATCHER_KEYS = ("fact", "log_pattern", "event_pattern")

FACT_WAITING_REASON = "waiting_reason"
FACT_TERMINATED_REASON = "terminated_reason"
FACT_EVENT_REASON = "event_reason"
FACT_EXIT_CODE = "exit_code"
# Alertmanager firing 알림 — Prometheus 가 실제 평가한 관측 결과를 fact 로 승격한다.
# 토큰 어휘: `alert_name=<alertname>`.
FACT_ALERT_NAME = "alert_name"
FACT_STANDARD_SLI_ALERT_IDENTITY_VERIFIED = "standard_sli_alert_identity=verified"
FACT_STRUCTURED_REJECTION_REASON_PREFIX = (
    "structured_rejection_identity=verified:reason="
)
FACT_REPLICA_REDUCTION_TIME_ALIGNED = "replica_reduction_time_aligned=verified"
FACT_POD_LABEL_PREFIX = "pod_label:"
# OOM(137)도 정상 종료(0)도 아닌 종료 코드 — 일반 앱/설정 크래시(exit 1 등) 판별용.
FACT_EXIT_CODE_NON_OOM = "exit_code=non_oom"
OOM_EXIT_CODE = 137


@dataclass(frozen=True)
class BundleSignals:
    """근거 번들에서 추출한 매칭 대상 — facts + 로그 라인 + 이벤트 문자열."""

    facts: frozenset[str] = frozenset()
    log_lines: tuple[str, ...] = ()
    event_texts: tuple[str, ...] = ()


@dataclass(frozen=True)
class MatchmakingCorrelationAttestation:
    candidate_id: str
    namespace: str
    resource_kind: str
    resource_name: str
    service: str
    sli: str
    symptom: str
    failure_started_at: str
    rejection_reason: str
    rejection_log_count: int
    observed_failure_ratio: float
    failure_ratio_threshold: float
    deployment_changed_at: str | None = None
    replica_before: int | None = None
    replica_after: int | None = None


@dataclass
class _SignalCollector:
    facts: set[str] = field(default_factory=set)
    log_lines: list[str] = field(default_factory=list)
    event_texts: list[str] = field(default_factory=list)
    alert_claims: list[_AlertClaim] = field(default_factory=list)
    log_rejections: list[_LogRejection] = field(default_factory=list)
    replica_changes: list[_ReplicaChange] = field(default_factory=list)


@dataclass(frozen=True)
class _AlertClaim:
    namespace: str
    resource_kind: str
    resource_name: str
    service: str
    sli: str
    symptom: str
    started_at: datetime | None
    observed_failure_ratio: float | None
    failure_ratio_threshold: float | None


@dataclass(frozen=True)
class _LogRejection:
    namespace: str
    pod: str
    reason: str
    resource_kind: str
    resource_name: str
    service: str
    sli: str
    symptom: str
    occurred_at: datetime | None


@dataclass(frozen=True)
class _ReplicaChange:
    namespace: str
    resource_kind: str
    resource_name: str
    before: int
    after: int
    changed_at: datetime | None


def extract_bundle_signals(evidence_bundle: EvidenceBundle) -> BundleSignals:
    """근거 번들 items 를 순회하며 fact 토큰·로그 라인·이벤트 문자열을 추출한다."""
    collector = collect_bundle_signal_values(evidence_bundle)
    return BundleSignals(
        facts=frozenset(collector.facts),
        log_lines=tuple(collector.log_lines),
        event_texts=tuple(collector.event_texts),
    )


def collect_bundle_signal_values(evidence_bundle: EvidenceBundle) -> _SignalCollector:
    collector = _SignalCollector()
    for item in evidence_bundle.items:
        if item.source == "kubernetes":
            collect_kubernetes_signals(item.value, collector)
        elif item.source == "logs":
            collect_log_lines(item.value, collector)
        elif item.source == "metrics":
            collect_alert_facts(item.value, collector)
        elif item.source == "metadata" and item.name == "change_context":
            collect_replica_changes(item.value, collector)
    collect_correlated_matchmaking_facts(collector)
    return collector


def extract_matchmaking_correlation_attestation(
    evidence_bundle: EvidenceBundle,
    candidate_id: str,
    *,
    accepted_reasons: frozenset[str],
    require_replica_change: bool,
) -> MatchmakingCorrelationAttestation | None:
    """Apply a catalog-declared candidate contract to one exact correlation."""

    collector = collect_bundle_signal_values(evidence_bundle)
    attestation = unique_rejection_attestation(collector)
    if attestation is None:
        return None
    alert, scoped = attestation
    if scoped[0].reason not in accepted_reasons:
        return None
    changes = matching_replica_changes(collector, alert)
    if require_replica_change and len(changes) != 1:
        return None
    change = changes[0] if len(changes) == 1 else None
    return matchmaking_attestation(candidate_id, alert, scoped, change=change)


def matchmaking_attestation(
    candidate_id: str,
    alert: _AlertClaim,
    scoped: list[_LogRejection],
    *,
    change: _ReplicaChange | None = None,
) -> MatchmakingCorrelationAttestation:
    return MatchmakingCorrelationAttestation(
        candidate_id=candidate_id,
        namespace=alert.namespace,
        resource_kind=alert.resource_kind,
        resource_name=alert.resource_name,
        service=alert.service,
        sli=alert.sli,
        symptom=alert.symptom,
        failure_started_at=alert.started_at.isoformat(),
        rejection_reason=scoped[0].reason,
        rejection_log_count=len(scoped),
        observed_failure_ratio=alert.observed_failure_ratio,
        failure_ratio_threshold=alert.failure_ratio_threshold,
        deployment_changed_at=change.changed_at.isoformat() if change else None,
        replica_before=change.before if change else None,
        replica_after=change.after if change else None,
    )


def collect_kubernetes_signals(value: JsonObject, collector: _SignalCollector) -> None:
    for pod in dict_items(value.get("pods")):
        collect_pod_label_facts(pod, collector)
        for reason in text_items(pod.get("waiting_reasons")):
            collector.facts.add(f"{FACT_WAITING_REASON}={reason}")
        for reason in text_items(pod.get("terminated_reasons")):
            collector.facts.add(f"{FACT_TERMINATED_REASON}={reason}")
        for container in dict_items(pod.get("containers")):
            add_exit_code_facts(container, collector)
    for event in dict_items(value.get("events")):
        reason = str(event.get("reason") or "").strip()
        message = str(event.get("message") or "").strip()
        if reason:
            collector.facts.add(f"{FACT_EVENT_REASON}={reason}")
        if reason or message:
            collector.event_texts.append(f"{reason} {message}".strip())


def collect_pod_label_facts(pod: JsonObject, collector: _SignalCollector) -> None:
    """Promote bounded, non-secret Pod labels into exact-match RCA facts."""
    labels = pod.get("labels")
    if not isinstance(labels, dict):
        return
    for raw_key, raw_value in labels.items():
        key = str(raw_key).strip()
        value = str(raw_value).strip()
        if key and value:
            collector.facts.add(f"{FACT_POD_LABEL_PREFIX}{key}={value}")


def add_exit_code_facts(container: JsonObject, collector: _SignalCollector) -> None:
    """현재 상태(exit_code)와 직전 상태(last_exit_code) 종료 코드를 fact 로 승격."""
    for key in ("exit_code", "last_exit_code"):
        raw = container.get(key)
        if raw is None or isinstance(raw, bool):
            continue
        try:
            code = int(raw)
        except (TypeError, ValueError):
            continue
        collector.facts.add(f"{FACT_EXIT_CODE}={code}")
        if code not in (0, OOM_EXIT_CODE):
            collector.facts.add(FACT_EXIT_CODE_NON_OOM)


def collect_alert_facts(value: JsonObject, collector: _SignalCollector) -> None:
    """metrics["alertmanager"] 의 firing 알림 이름을 fact 토큰으로 추출한다."""
    alertmanager = value.get("alertmanager")
    if not isinstance(alertmanager, dict):
        return
    for alert in dict_items(alertmanager.get("alerts")):
        if str(alert.get("status") or "firing") != "firing":
            continue
        labels = alert.get("labels")
        if not isinstance(labels, dict):
            continue
        name = str(labels.get("alertname") or "").strip()
        if name:
            collector.facts.add(f"{FACT_ALERT_NAME}={name}")
        if name != "OpsiaSliFailureRatioHigh":
            continue
        identity = tuple(
            str(labels.get(key) or "").strip()
            for key in (
                "opsia_namespace",
                "opsia_resource_kind",
                "opsia_resource_name",
                "opsia_service",
                "opsia_sli",
                "opsia_symptom",
            )
        )
        if not all(identity):
            continue
        annotations = alert.get("annotations")
        annotations = annotations if isinstance(annotations, dict) else {}
        observed_failure_ratio = bounded_ratio(annotations.get("opsia_observed_value"))
        failure_ratio_threshold = bounded_ratio(annotations.get("opsia_threshold"))
        if (
            observed_failure_ratio is None
            or failure_ratio_threshold is None
            or observed_failure_ratio <= failure_ratio_threshold
        ):
            continue
        collector.alert_claims.append(
            _AlertClaim(
                namespace=identity[0],
                resource_kind=identity[1],
                resource_name=identity[2],
                service=identity[3],
                sli=identity[4],
                symptom=identity[5],
                started_at=parse_timestamp(alert.get("startsAt")),
                observed_failure_ratio=observed_failure_ratio,
                failure_ratio_threshold=failure_ratio_threshold,
            )
        )


def collect_log_lines(value: JsonObject, collector: _SignalCollector) -> None:
    """단순 {"line": ...} 항목과 Loki 정규화 payload(streams[].values[].line) 모두 지원."""
    for entry in dict_items(value.get("entries")):
        line = entry.get("line")
        if isinstance(line, str) and line:
            collector.log_lines.append(line)
        for stream in dict_items(entry.get("streams")):
            labels = stream.get("stream")
            labels = labels if isinstance(labels, dict) else {}
            namespace = first_label(labels, ("k8s_namespace_name", "namespace"))
            pod = first_label(
                labels,
                ("k8s_pod_name", "pod", "pod_name", "kubernetes_pod_name"),
            )
            for sample in dict_items(stream.get("values")):
                sample_line = sample.get("line")
                if isinstance(sample_line, str) and sample_line:
                    collector.log_lines.append(sample_line)
                    rejection = parse_structured_rejection(
                        sample_line,
                        namespace,
                        pod,
                        sample.get("timestamp"),
                    )
                    if rejection is not None:
                        collector.log_rejections.append(rejection)


def parse_structured_rejection(
    line: str,
    namespace: str,
    pod: str,
    sample_timestamp: object = None,
) -> _LogRejection | None:
    """Parse only structured admission logs; arbitrary message text is not proof."""

    try:
        payload = json.loads(line)
    except (TypeError, ValueError, json.JSONDecodeError):
        return None
    if not isinstance(payload, dict) or str(payload.get("event") or "") != "find_game_rejected":
        return None
    outcome = str(payload.get("outcome") or "").strip()
    if outcome and outcome != "rejected":
        return None
    detail = payload.get("detail")
    reason = str(
        payload.get("reason")
        or (detail.get("reason") if isinstance(detail, dict) else "")
        or ""
    ).strip()
    log_namespace = str(payload.get("namespace") or namespace).strip()
    log_pod = str(
        payload.get("pod")
        or payload.get("workload")
        or payload.get("resource_name")
        or pod
    ).strip()
    resource_kind = str(payload.get("resource_kind") or "").strip()
    resource_name = str(payload.get("resource_name") or "").strip()
    service = str(payload.get("service") or "").strip()
    sli = str(payload.get("sli") or "").strip()
    symptom = str(payload.get("symptom") or "").strip()
    occurred_at = parse_timestamp(payload.get("timestamp")) or parse_log_sample_timestamp(
        sample_timestamp
    )
    if (
        not reason
        or not log_namespace
        or not log_pod
        or not service
        or not sli
        or occurred_at is None
    ):
        return None
    return _LogRejection(
        namespace=log_namespace,
        pod=log_pod,
        reason=reason,
        resource_kind=resource_kind,
        resource_name=resource_name,
        service=service,
        sli=sli,
        symptom=symptom,
        occurred_at=occurred_at,
    )


def parse_log_sample_timestamp(value: object) -> datetime | None:
    parsed = parse_timestamp(value)
    if parsed is not None:
        return parsed
    if isinstance(value, bool) or value in (None, ""):
        return None
    try:
        raw = int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    if raw > 10_000_000_000_000_000:
        seconds = raw / 1_000_000_000
    elif raw > 10_000_000_000_000:
        seconds = raw / 1_000
    else:
        seconds = raw
    try:
        return datetime.fromtimestamp(seconds, tz=UTC)
    except (OverflowError, OSError, ValueError):
        return None


def collect_replica_changes(value: JsonObject, collector: _SignalCollector) -> None:
    resource = value.get("resource")
    resource = resource if isinstance(resource, dict) else {}
    recent_changes = value.get("recent_changes")
    for change in recent_changes if isinstance(recent_changes, list) else []:
        if not isinstance(change, dict):
            continue
        field = str(change.get("field_path") or change.get("field") or "")
        if field != "spec.replicas":
            continue
        before = integer(change.get("before"))
        after = integer(change.get("after"))
        if before is None or after is None:
            continue
        kind, name = change_target(change, resource)
        namespace = str(
            change.get("namespace")
            or resource.get("namespace")
            or ""
        ).strip()
        if not namespace or not kind or not name:
            continue
        collector.replica_changes.append(
            _ReplicaChange(
                namespace=namespace,
                resource_kind=kind,
                resource_name=name,
                before=before,
                after=after,
                changed_at=parse_timestamp(change.get("changed_at")),
            )
        )


def collect_correlated_matchmaking_facts(collector: _SignalCollector) -> None:
    """Promote data-derived facts from one exact structured correlation."""

    attestation = unique_rejection_attestation(collector)
    if attestation is None:
        return
    alert, scoped = attestation
    collector.facts.update(
        {
            FACT_STANDARD_SLI_ALERT_IDENTITY_VERIFIED,
            structured_rejection_reason_fact(scoped[0].reason),
        }
    )
    if len(matching_replica_changes(collector, alert)) == 1:
        collector.facts.add(FACT_REPLICA_REDUCTION_TIME_ALIGNED)


def matching_replica_changes(
    collector: _SignalCollector,
    alert: _AlertClaim,
) -> list[_ReplicaChange]:
    candidates = [
        item
        for item in collector.replica_changes
        if replica_change_matches_alert(item, alert) and item.changed_at is not None
    ]
    if not candidates:
        return []
    nearest_changed_at = max(
        item.changed_at for item in candidates if item.changed_at is not None
    )
    # Repeated demo/production deploys can legitimately leave several
    # reductions inside the correlation window.  The latest preceding change
    # is the causal candidate; equal-timestamp duplicates remain ambiguous and
    # therefore still fail closed through the caller's len(...) == 1 check.
    return [item for item in candidates if item.changed_at == nearest_changed_at]


def structured_rejection_reason_fact(reason: str) -> str:
    return f"{FACT_STRUCTURED_REJECTION_REASON_PREFIX}{reason}"


def declared_structured_rejection_reasons(facts: set[str]) -> frozenset[str]:
    return frozenset(
        fact.removeprefix(FACT_STRUCTURED_REJECTION_REASON_PREFIX)
        for fact in facts
        if fact.startswith(FACT_STRUCTURED_REJECTION_REASON_PREFIX)
        and fact.removeprefix(FACT_STRUCTURED_REJECTION_REASON_PREFIX)
    )


def iter_rejection_attestations(
    collector: _SignalCollector,
):
    """Yield exact alert/log correlations without assigning a domain outcome."""

    for alert in collector.alert_claims:
        if (
            alert.started_at is None
            or alert.observed_failure_ratio is None
            or alert.failure_ratio_threshold is None
        ):
            continue
        scoped_rejections = [
            item for item in collector.log_rejections if log_rejection_matches_alert(item, alert)
        ]
        reasons = {item.reason for item in scoped_rejections}
        if len(reasons) != 1:
            continue
        yield alert, scoped_rejections


def unique_rejection_attestation(
    collector: _SignalCollector,
) -> tuple[_AlertClaim, list[_LogRejection]] | None:
    """Return one unique incident correlation; reject mixed identities or reasons."""

    alert_identities = {
        (
            alert.namespace,
            alert.resource_kind.casefold(),
            alert.resource_name,
            alert.service,
            alert.sli,
            alert.symptom,
            alert.started_at,
        )
        for alert in collector.alert_claims
    }
    if len(alert_identities) != 1:
        return None
    attestations: dict[
        tuple[str, str, str, str, str, str, str, datetime | None],
        tuple[_AlertClaim, list[_LogRejection]],
    ] = {}
    for alert, scoped in iter_rejection_attestations(collector):
        key = (
            scoped[0].reason,
            alert.namespace,
            alert.resource_kind.casefold(),
            alert.resource_name,
            alert.service,
            alert.sli,
            alert.symptom,
            alert.started_at,
        )
        attestations[key] = (alert, scoped)
    if len(attestations) != 1:
        return None
    return next(iter(attestations.values()))


def replica_change_matches_alert(change: _ReplicaChange, alert: _AlertClaim) -> bool:
    if (
        change.namespace != alert.namespace
        or change.resource_kind.casefold() != alert.resource_kind.casefold()
        or change.resource_name != alert.resource_name
        or change.before <= change.after
        or change.changed_at is None
        or alert.started_at is None
    ):
        return False
    seconds = (alert.started_at - change.changed_at).total_seconds()
    return 0 <= seconds <= 600


def log_rejection_matches_alert(item: _LogRejection, alert: _AlertClaim) -> bool:
    if item.namespace != alert.namespace or item.occurred_at is None or alert.started_at is None:
        return False
    if item.resource_kind and item.resource_kind.casefold() != alert.resource_kind.casefold():
        return False
    if item.resource_name:
        if item.resource_name != alert.resource_name:
            return False
    elif not workload_matches_pod(alert.resource_name, item.pod):
        return False
    if item.service != alert.service or item.sli != alert.sli:
        return False
    if item.symptom and item.symptom != alert.symptom:
        return False
    seconds = (alert.started_at - item.occurred_at).total_seconds()
    return -60 <= seconds <= 600


def workload_matches_pod(workload: str, pod: str) -> bool:
    return pod == workload or pod.startswith(f"{workload}-")


def change_target(change: JsonObject, fallback: JsonObject) -> tuple[str, str]:
    target = str(change.get("target_resource") or "").strip()
    if "/" in target:
        kind, name = target.split("/", 1)
        return kind.strip(), name.strip()
    return (
        str(fallback.get("workload_kind") or fallback.get("kind") or "").strip(),
        str(fallback.get("workload_name") or fallback.get("name") or "").strip(),
    )


def first_label(labels: JsonObject, keys: tuple[str, ...]) -> str:
    for key in keys:
        value = labels.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return ""


def integer(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def bounded_ratio(value: object) -> float | None:
    try:
        ratio = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None
    return ratio if 0 <= ratio <= 1 else None


def parse_timestamp(value: object) -> datetime | None:
    if not isinstance(value, str) or not value.strip():
        return None
    try:
        parsed = datetime.fromisoformat(value.strip().replace("Z", "+00:00"))
    except ValueError:
        return None
    return parsed.replace(tzinfo=UTC) if parsed.tzinfo is None else parsed.astimezone(UTC)


def match_signal_group(group: JsonObject, signals: BundleSignals) -> bool:
    """그룹 충족 여부 — any_of matcher 중 하나라도 매칭되면 True."""
    matchers = group.get("any_of")
    if not isinstance(matchers, list) or not matchers:
        return False
    return any(isinstance(matcher, dict) and match_one(matcher, signals) for matcher in matchers)


def match_one(matcher: JsonObject, signals: BundleSignals) -> bool:
    fact = matcher.get("fact")
    if isinstance(fact, str):
        return fact in signals.facts
    log_pattern = matcher.get("log_pattern")
    if isinstance(log_pattern, str):
        needle = log_pattern.casefold()
        return any(needle in line.casefold() for line in signals.log_lines)
    event_pattern = matcher.get("event_pattern")
    if isinstance(event_pattern, str):
        needle = event_pattern.casefold()
        return any(needle in text.casefold() for text in signals.event_texts)
    return False


def split_signal_groups(
    candidate_signals: list[JsonObject],
    signals: BundleSignals,
) -> tuple[list[JsonObject], list[JsonObject]]:
    """후보 signals 를 (matched, unmatched) 로 분리한다(순서 보존)."""
    matched: list[JsonObject] = []
    unmatched: list[JsonObject] = []
    for group in candidate_signals:
        if match_signal_group(group, signals):
            matched.append(group)
        else:
            unmatched.append(group)
    return matched, unmatched


def signal_group_id(group: JsonObject) -> str:
    return str(group.get("id") or "unnamed")


def signal_missing_token(group: JsonObject) -> str:
    """미충족 그룹의 missing_evidence 토큰 — `signal:<group id>`."""
    return f"signal:{signal_group_id(group)}"


def describe_signal_group(group: JsonObject) -> str:
    """미충족 사유 문구용 — any_of matcher 를 사람이 읽을 요약으로 변환."""
    parts: list[str] = []
    for matcher in group.get("any_of") or []:
        if not isinstance(matcher, dict):
            continue
        for key in MATCHER_KEYS:
            value = matcher.get(key)
            if isinstance(value, str):
                parts.append(f"{key}:{value}")
    return " | ".join(parts) if parts else "정의된 matcher 없음"


def dict_items(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def text_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]
