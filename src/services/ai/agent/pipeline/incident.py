from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from domains.rca.events import (
    Evidence,
    EvidenceBundleBuiltBody,
    IncidentDetectedBody,
    IncidentRecord,
    RcaActionRequiredBody,
)
from packages.contracts.event_bus.bodies import EventBody, JsonObject
from services.ai.agent.defaults import IncidentMessages, RcaMessages
from services.ai.agent.pipeline.evidence_bundle import (
    build_incident_evidence_bundle,
    compact_evidence_reference,
)
from services.ai.agent.pipeline.symptom import (
    SYMPTOM_INGRESS_5XX,
    UNKNOWN_SYMPTOM,
    derive_symptom,
    incident_category_for_signal,
    resolve_resource,
)

APPLICATION_5XX_SIGNAL = "Application5xx"
APPLICATION_TIMEOUT_SIGNAL = "ApplicationTimeout"
APP_5XX_FIELDS = ("status", "status_code", "upstream_status")
APP_5XX_TEXT_PATTERNS = (
    'upstream_status":500',
    'upstream_status": 500',
    'upstream_status":502',
    'upstream_status": 502',
    'upstream_status":503',
    'upstream_status": 503',
    'upstream_status":504',
    'upstream_status": 504',
    'status":500',
    'status": 500',
    'status":502',
    'status": 502',
    'status":503',
    'status": 503',
    'status":504',
    'status": 504',
    "/api/orders/error",
    "intentional_error_endpoint",
    "intentional error endpoint called",
)
APP_TIMEOUT_TEXT_PATTERNS = (
    "dependency_timeout",
    "dependency call timed out",
    "timeout",
)
MAX_LOG_SIGNAL_AGE = timedelta(minutes=5)
# 플랫폼 제어 컴포넌트의 재시작·폴링 오류는 애플리케이션 장애가 아니다.
# 해당 로그는 운영 관측에는 보존하되 app-runtime incident 판별에서 제외한다.
CONTROL_PLANE_LOG_NAMESPACES = frozenset({"management", "target"})


@dataclass(frozen=True)
class LogIncidentSignal:
    signal: str
    symptom: str
    resource_kind: str
    resource_name: str
    namespace: str | None


@dataclass(frozen=True)
class IncidentDetector:
    messages: IncidentMessages = field(default_factory=IncidentMessages)

    def detect_body(self, evidence: Evidence, correlation_id: str) -> IncidentDetectedBody:
        incident = self.classify(evidence, correlation_id)
        detected = self.has_signal(evidence)
        if not detected:
            return IncidentDetectedBody(
                cluster_id=evidence.cluster_id,
                detected=False,
                reason=self.messages.not_detected_reason,
                workspace_id=evidence.workspace_id,
                severity=None,
                affected=[],
                evidence=compact_evidence_reference(evidence),
                incident=None,
            )
        return IncidentDetectedBody(
            cluster_id=evidence.cluster_id,
            detected=True,
            reason=self.messages.detected_reason,
            workspace_id=evidence.workspace_id,
            severity=incident.severity,
            affected=self.affected_resources(incident),
            evidence=compact_evidence_reference(evidence),
            incident=incident,
        )

    def has_signal(self, evidence: Evidence) -> bool:
        derived = derive_symptom(evidence.kubernetes)
        if derived.signal is not None or derived.symptom != UNKNOWN_SYMPTOM:
            return True
        collected_at = evidence_collected_at(evidence.kubernetes)
        if derive_log_incident_signal(evidence.logs, collected_at=collected_at) is not None:
            return True

        # Alertmanager webhook evidence arrives through metrics, not the Kubernetes
        # snapshot. Keep firing alert groups incident-worthy without treating every
        # normal metrics sample as an incident.
        alertmanager = (
            evidence.metrics.get("alertmanager") if isinstance(evidence.metrics, dict) else None
        )
        if isinstance(alertmanager, dict):
            alerts = alertmanager.get("alerts")
            return isinstance(alerts, list) and any(
                isinstance(alert, dict) and alert.get("status") == "firing" for alert in alerts
            )
        return False

    def classify(self, evidence: Evidence, incident_id: str) -> IncidentRecord:
        # 명시 symptom(webhook/레거시 데이터)이 있으면 그대로, 없으면 snapshot 신호에서 유도.
        # 우선순위·판정 기준은 pipeline/symptom.py 상수 표 참조(명시 > 유도 > unknown).
        derived = derive_symptom(evidence.kubernetes)
        log_signal = None
        if derived.signal is None and derived.symptom == UNKNOWN_SYMPTOM:
            log_signal = derive_log_incident_signal(
                evidence.logs,
                collected_at=evidence_collected_at(evidence.kubernetes),
            )
        if log_signal is not None:
            resource_kind = log_signal.resource_kind
            resource_name = log_signal.resource_name
            namespace = log_signal.namespace
            symptom = log_signal.symptom
            secondary_symptoms = [log_signal.signal]
        else:
            resource_kind, resource_name, namespace = resolve_resource(
                evidence.kubernetes, derived.signal
            )
            symptom = derived.symptom
            secondary_symptoms = derived.secondary_symptoms
        severity = str(evidence.kubernetes.get("severity", "medium"))
        explicit_category = str(evidence.kubernetes.get("category") or "").strip().casefold()
        category = explicit_category or incident_category_for_signal(derived.signal)
        if log_signal is not None:
            category = "application_runtime"
        return IncidentRecord(
            incident_id=incident_id,
            cluster_id=evidence.cluster_id,
            resource_kind=resource_kind,
            resource_name=resource_name,
            namespace=namespace,
            symptom=symptom,
            severity=severity,
            category=category,
            first_seen_at=evidence.kubernetes.get("first_seen_at"),
            summary=f"{resource_kind} {resource_name} has {symptom}",
            workspace_id=evidence.workspace_id,
            secondary_symptoms=secondary_symptoms,
        )

    def affected_resources(self, incident: IncidentRecord) -> list[JsonObject]:
        return [
            {
                "cluster_id": incident.cluster_id,
                "workspace_id": incident.workspace_id,
                "namespace": incident.namespace,
                "resource_kind": incident.resource_kind,
                "resource_name": incident.resource_name,
                "symptom": incident.symptom,
                "severity": incident.severity,
            }
        ]


def derive_log_incident_signal(
    logs: list[JsonObject],
    *,
    collected_at: datetime | None = None,
) -> LogIncidentSignal | None:
    for sample in iter_log_samples(logs, collected_at=collected_at):
        if str(sample.get("namespace") or "").strip() in CONTROL_PLANE_LOG_NAMESPACES:
            continue
        parsed = parse_json_line(sample["line"])
        if has_5xx_status(parsed, sample["line"]):
            signal = APPLICATION_5XX_SIGNAL
        elif has_timeout_signal(parsed, sample["line"]):
            signal = APPLICATION_TIMEOUT_SIGNAL
        else:
            continue

        resource_name = resource_name_for_log_sample(sample, parsed)
        if resource_name is None:
            continue
        namespace = str(sample.get("namespace") or "").strip() or None
        return LogIncidentSignal(
            signal=signal,
            symptom=SYMPTOM_INGRESS_5XX,
            resource_kind="Deployment",
            resource_name=resource_name,
            namespace=namespace,
        )
    return None


def iter_log_samples(
    logs: list[JsonObject],
    *,
    collected_at: datetime | None = None,
) -> list[JsonObject]:
    samples: list[JsonObject] = []
    for entry in logs:
        if not isinstance(entry, dict):
            continue
        entry_namespace = namespace_from_query(entry.get("query"))
        line = entry.get("line")
        entry_ts = log_timestamp(entry.get("timestamp"))
        if isinstance(line, str) and log_sample_is_current(entry_ts, collected_at):
            samples.append(
                {"namespace": entry_namespace, "container": entry.get("container"), "line": line}
            )
        for stream in dict_items(entry.get("streams")):
            stream_labels = stream.get("stream")
            labels = stream_labels if isinstance(stream_labels, dict) else {}
            namespace = str(labels.get("k8s_namespace_name") or entry_namespace or "")
            container = str(
                labels.get("k8s_container_name")
                or labels.get("container")
                or labels.get("app")
                or ""
            )
            for value in dict_items(stream.get("values")):
                stream_line = value.get("line")
                sample_ts = log_timestamp(value.get("timestamp"))
                if (
                    isinstance(stream_line, str)
                    and stream_line
                    and log_sample_is_current(sample_ts, collected_at)
                ):
                    samples.append(
                        {"namespace": namespace, "container": container, "line": stream_line}
                    )
    return samples


def evidence_collected_at(kubernetes: JsonObject) -> datetime | None:
    cluster = kubernetes.get("cluster")
    if not isinstance(cluster, dict):
        return None
    return parse_datetime(cluster.get("collected_at"))


def log_sample_is_current(
    timestamp: datetime | None,
    collected_at: datetime | None,
) -> bool:
    if timestamp is None or collected_at is None:
        return True
    return abs(collected_at - timestamp) <= MAX_LOG_SIGNAL_AGE


def log_timestamp(value: object) -> datetime | None:
    parsed = parse_datetime(value)
    if parsed is not None:
        return parsed
    if not isinstance(value, str) or not value:
        return None
    try:
        raw = int(value)
    except ValueError:
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


def parse_datetime(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def namespace_from_query(query: object) -> str | None:
    if not isinstance(query, str):
        return None
    marker = 'k8s_namespace_name="'
    if marker not in query:
        return None
    return query.split(marker, 1)[1].split('"', 1)[0]


def parse_json_line(line: str) -> JsonObject:
    try:
        value = json.loads(line)
    except json.JSONDecodeError:
        return {}
    return value if isinstance(value, dict) else {}


def has_5xx_status(parsed: JsonObject, line: str) -> bool:
    for status_field in APP_5XX_FIELDS:
        if is_5xx(parsed.get(status_field)):
            return True
    normalized = line.casefold()
    return any(pattern in normalized for pattern in APP_5XX_TEXT_PATTERNS)


def has_timeout_signal(parsed: JsonObject, line: str) -> bool:
    event = str(parsed.get("event") or "").casefold()
    message = str(parsed.get("message") or "").casefold()
    combined = f"{event} {message} {line.casefold()}"
    return any(pattern in combined for pattern in APP_TIMEOUT_TEXT_PATTERNS)


def is_5xx(value: object) -> bool:
    if isinstance(value, bool) or value is None:
        return False
    try:
        status = int(value)
    except (TypeError, ValueError):
        return False
    return 500 <= status <= 599


def resource_name_for_log_sample(sample: JsonObject, parsed: JsonObject) -> str | None:
    for field_name in ("service", "service_name", "app", "deployment"):
        resource_name = str(parsed.get(field_name) or "").strip()
        if resource_name:
            return resource_name
    container = str(sample.get("container") or "").strip()
    return container or None


def dict_items(value: object) -> list[JsonObject]:
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


@dataclass(frozen=True)
class EvidenceBundler:
    messages: RcaMessages = field(default_factory=RcaMessages)

    def build_body(self, evt: IncidentDetectedBody) -> EventBody:
        evidence = evt.evidence
        incident = evt.incident
        if evidence is None or incident is None:
            return RcaActionRequiredBody(
                reason=self.messages.missing_incident_context,
                evidence_ref=evidence.object_ref if evidence else "unknown",
                workspace_id=evt.workspace_id,
            )
        if not evt.detected:
            return RcaActionRequiredBody(
                reason=self.messages.no_incident_action_required,
                evidence_ref=evidence.object_ref,
                workspace_id=evidence.workspace_id,
            )
        return EvidenceBundleBuiltBody(
            evidence=compact_evidence_reference(evidence),
            incident=incident,
            evidence_bundle=build_incident_evidence_bundle(evidence, incident),
        )
