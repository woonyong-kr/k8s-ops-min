"""kubernetes snapshot 신호 → RCA 카탈로그 증상(symptom) 결정적 승격.

에이전트 snapshot 요약(pods.waiting_reasons/terminated_reasons, events, services,
endpoints)에서 장애 신호를 찾아 causes/catalog/*.yaml 이 매칭하는 symptom 어휘로
변환한다. 우선순위는 아래 상수 표 하나로 고정되어 같은 입력이면 항상 같은 결과다.

계약: 명시 > 유도 > "unknown".
- kubernetes["symptom"] 이 명시되어 있으면(예: alertmanager/webhook, 테스트 레거시 데이터)
  절대 덮어쓰지 않는다.
- 신호가 하나도 없으면 기존과 동일하게 "unknown" 으로 남아 backlog 경로로 흐른다.
- 신호가 여러 개면 우선순위가 가장 높은 신호가 대표 symptom 이 되고, 나머지 신호
  라벨은 secondary_symptoms 로 보존한다(정보 손실 없음).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from packages.contracts.event_bus.bodies import JsonObject

UNKNOWN_SYMPTOM = "unknown"

# 카탈로그(causes/catalog/*.yaml) symptom 어휘 — 유도 결과는 반드시 이 값들로 수렴한다.
SYMPTOM_IMAGE_PULL = "ImagePullBackOff"
SYMPTOM_CRASHLOOP = "CrashLoopBackOff"
SYMPTOM_FAILED_SCHEDULING = "FailedScheduling"
SYMPTOM_INGRESS_5XX = "Ingress 502/503"
SYMPTOM_READINESS_PROBE_FAILURE = "Readiness probe response failure"
SYMPTOM_LIVENESS_PROBE_FAILURE = "Liveness probe response failure"
SYMPTOM_STARTUP_PROBE_FAILURE = "Startup probe response failure"
SYMPTOM_PROBE_FAILURE = "Probe response failure"
SYMPTOM_POD_NOT_READY = "Pod readiness failure"
SYMPTOM_SERVICE_ENDPOINTS_EMPTY = "Service has no ready endpoints"

# 우선순위 근거 — 운영자가 실제로 triage 하는 순서를 그대로 고정한다.
# 1. image pull: 파드가 아예 뜨지 못하는 배포 산출물/레지스트리 문제. 원인 폭이 가장
#    좁고(태그/시크릿/레지스트리) 재시작·readiness 미달 등 하위 신호를 연쇄 유발한다.
# 2. crashloop: 이미지는 받았지만 컨테이너가 기동 직후 죽는 상태. pull 문제가 없을 때만
#    대표가 된다.
# 3. oom: crash 계열의 특수형(메모리 한계, terminated.reason=OOMKilled). CrashLoopBackOff
#    waiting 신호가 함께 있으면 그쪽이 대표가 되고 OOMKilled 는 secondary 로 남는다 —
#    카탈로그 증상은 같은 crashloop 룰(oom_killed 후보 포함)로 수렴한다.
# 4. scheduling: 파드가 노드에 배치되지 못함(FailedScheduling/Pending). 실행이 시작된
#    워크로드의 장애(1~3)보다 사용자 영향 관측이 늦어 후순위.
# 5. probe event: 실제 5xx 관측 없이 Unhealthy 이벤트만 존재할 때는 probe 응답 실패로
#    표현한다. Ingress 502/503은 실제 application/ingress 5xx 근거가 있을 때만 사용한다.
# 6. pod readiness: probe 종류를 특정할 이벤트가 없을 때의 일반 Ready=False 신호.
# 7. service selector/endpoint: ready endpoint가 비어 있는 배선 문제를 그대로 표현한다.
PRIORITY_IMAGE_PULL = 1
PRIORITY_CRASHLOOP = 2
PRIORITY_OOM = 3
PRIORITY_SCHEDULING = 4
PRIORITY_PROBE = 5
PRIORITY_POD_NOT_READY = 6
PRIORITY_SERVICE_ENDPOINT = 7

# 컨테이너 waiting reason 중 image pull 계열로 분류하는 값들.
IMAGE_PULL_WAITING_REASONS = frozenset(
    {"ImagePullBackOff", "ErrImagePull", "InvalidImageName", "ErrImageNeverPull"}
)

SIGNAL_OOM_KILLED = "OOMKilled"
SIGNAL_FAILED_SCHEDULING = "FailedScheduling"
SIGNAL_POD_NOT_READY = "PodNotReady"
SIGNAL_PROBE_FAILED = "ProbeFailed"
SIGNAL_SERVICE_ENDPOINTS_EMPTY = "ServiceEndpointsEmpty"
EVENT_SIGNAL_MAX_AGE = timedelta(minutes=10)

INCIDENT_CATEGORY_IMAGE_PULL = "image_pull"
INCIDENT_CATEGORY_CONTAINER_RESTART = "container_restart"
INCIDENT_CATEGORY_SCHEDULING = "scheduling"
INCIDENT_CATEGORY_PROBE = "probe"
INCIDENT_CATEGORY_READINESS = "readiness"
INCIDENT_CATEGORY_SERVICE_ROUTING = "service_routing"


# 같은 우선순위 안에서의 근거 출처 순위 — 파드 상태가 1차 근거, 이벤트는 정황 보강,
# service/endpoint 는 간접 신호. 대표 리소스 힌트가 워크로드(소유자)로 잡히게 한다.
SOURCE_RANK_POD = 0
SOURCE_RANK_EVENT = 1
SOURCE_RANK_SERVICE = 2


@dataclass(frozen=True)
class SymptomSignal:
    """snapshot 에서 관측한 장애 신호 하나 + 대상 리소스 힌트."""

    signal: str
    symptom: str
    priority: int
    resource_kind: str
    resource_name: str
    namespace: str | None
    # 신호 출처 순위(SOURCE_RANK_*) — 낮을수록 대표로 우선.
    source_rank: int = SOURCE_RANK_POD
    # 같은 우선순위·출처 안에서 대표를 고르는 가중치(재시작 수, 이벤트 count) — 클수록 우선.
    weight: int = 0


@dataclass(frozen=True)
class DerivedSymptom:
    """유도 결과 — 대표 symptom + 보존할 나머지 신호 라벨."""

    symptom: str
    secondary_symptoms: list[str] = field(default_factory=list)
    signal: SymptomSignal | None = None


def derive_symptom(kubernetes: JsonObject) -> DerivedSymptom:
    """대표 symptom 결정 — 명시 값이 있으면 그대로, 없으면 snapshot 신호에서 유도."""
    explicit = str(kubernetes.get("symptom") or "").strip()
    if explicit:
        return DerivedSymptom(symptom=explicit)
    signals = collect_signals(kubernetes)
    if not signals:
        return DerivedSymptom(symptom=UNKNOWN_SYMPTOM)
    ordered = sorted(signals, key=signal_sort_key)
    primary = ordered[0]
    secondary = unique_signal_labels(ordered[1:], exclude=primary.signal)
    return DerivedSymptom(
        symptom=primary.symptom,
        secondary_symptoms=secondary,
        signal=primary,
    )


def resolve_resource(
    kubernetes: JsonObject,
    signal: SymptomSignal | None = None,
) -> tuple[str, str, str | None]:
    """incident 대상 리소스 결정 — 명시 resource dict 가 있으면 그대로(명시 > 유도)."""
    resource = kubernetes.get("resource")
    if isinstance(resource, dict) and resource:
        return (
            str(resource.get("kind", "Unknown")),
            str(resource.get("name", "unknown")),
            resource.get("namespace"),
        )
    if signal is not None:
        return (signal.resource_kind, signal.resource_name, signal.namespace)
    return ("Unknown", "unknown", None)


def signal_sort_key(signal: SymptomSignal) -> tuple[int, int, int, str, str]:
    return (
        signal.priority,
        signal.source_rank,
        -signal.weight,
        signal.resource_name,
        signal.signal,
    )


def unique_signal_labels(signals: list[SymptomSignal], *, exclude: str) -> list[str]:
    labels: list[str] = []
    for signal in signals:
        if signal.signal == exclude or signal.signal in labels:
            continue
        labels.append(signal.signal)
    return labels


def incident_category_for_signal(signal: SymptomSignal | None) -> str | None:
    """Map one detector-owned primary signal to its stable issue category."""
    if signal is None:
        return None
    if signal.signal in IMAGE_PULL_WAITING_REASONS or signal.signal == SYMPTOM_IMAGE_PULL:
        return INCIDENT_CATEGORY_IMAGE_PULL
    if signal.signal in {SYMPTOM_CRASHLOOP, SIGNAL_OOM_KILLED}:
        return INCIDENT_CATEGORY_CONTAINER_RESTART
    if signal.signal == SIGNAL_FAILED_SCHEDULING:
        return INCIDENT_CATEGORY_SCHEDULING
    if signal.signal in {
        SIGNAL_PROBE_FAILED,
        "ReadinessProbeFailed",
        "LivenessProbeFailed",
        "StartupProbeFailed",
    }:
        return INCIDENT_CATEGORY_PROBE
    if signal.signal == SIGNAL_POD_NOT_READY:
        return INCIDENT_CATEGORY_READINESS
    if signal.signal == SIGNAL_SERVICE_ENDPOINTS_EMPTY:
        return INCIDENT_CATEGORY_SERVICE_ROUTING
    return None


def collect_signals(kubernetes: JsonObject) -> list[SymptomSignal]:
    signals: list[SymptomSignal] = []
    pods = snapshot_items(kubernetes, "pods")
    for pod in pods:
        signals.extend(pod_signals(pod))
    collected_at = collected_at_time(kubernetes)
    for event in snapshot_items(kubernetes, "events"):
        if not event_is_current_warning(event, collected_at):
            continue
        if not event_is_active_for_snapshot(event, pods):
            continue
        signals.extend(event_signals(event, pods))
    signals.extend(service_endpoint_signals(kubernetes))
    return signals


def collected_at_time(kubernetes: JsonObject) -> datetime | None:
    cluster = kubernetes.get("cluster")
    if not isinstance(cluster, dict):
        return None
    return parse_event_time(cluster.get("collected_at"))


def event_is_current_warning(event: JsonObject, collected_at: datetime | None) -> bool:
    """Kubernetes Event 객체는 해결 뒤에도 남으므로 오래된 Warning은 신호에서 제외한다."""
    if str(event.get("type") or "") not in {"", "Warning"}:
        return False
    if collected_at is None:
        return True
    last_seen = parse_event_time(event.get("last_timestamp") or event.get("first_timestamp"))
    if last_seen is None:
        return True
    return collected_at - last_seen <= EVENT_SIGNAL_MAX_AGE


def event_is_active_for_snapshot(event: JsonObject, pods: list[JsonObject]) -> bool:
    """Pod 이벤트는 현재 Pod 상태와 맞을 때만 장애 신호로 사용한다.

    Kubernetes Event는 Pod 삭제 뒤에도 잠시 남는다. 롤아웃으로 사라진 Pod의 최근
    readiness 실패를 시간 조건만으로 다시 승격하면 evidence 주기마다 새 인시던트가
    생긴다. Pod 대상 이벤트는 같은 namespace/name의 현재 Pod가 있어야 하며, probe
    실패는 그 Pod가 아직 Ready가 아닐 때만 유효하다.
    """
    if str(event.get("involved_kind") or "") != "Pod":
        return True
    involved_name = str(event.get("involved_name") or "")
    namespace = optional_text(event.get("namespace"))
    current_pod = next(
        (
            pod
            for pod in pods
            if str(pod.get("name") or "") == involved_name
            and optional_text(pod.get("namespace")) == namespace
        ),
        None,
    )
    if current_pod is None:
        return False
    message = str(event.get("message") or "").lower()
    if str(event.get("reason") or "") == "Unhealthy" and "probe" in message:
        return pod_not_ready(current_pod)
    return True


def parse_event_time(value: object) -> datetime | None:
    if not isinstance(value, str) or not value:
        return None
    try:
        normalized = value.replace("Z", "+00:00")
        parsed = datetime.fromisoformat(normalized)
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def snapshot_items(kubernetes: JsonObject, key: str) -> list[JsonObject]:
    value = kubernetes.get(key)
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


def pod_signals(pod: JsonObject) -> list[SymptomSignal]:
    """pod 요약 한 건에서 신호 추출 — waiting/terminated reason 이 1차 근거."""
    signals: list[SymptomSignal] = []
    for reason in text_items(pod.get("waiting_reasons")):
        if reason in IMAGE_PULL_WAITING_REASONS:
            signals.append(pod_signal(pod, reason, SYMPTOM_IMAGE_PULL, PRIORITY_IMAGE_PULL))
        elif reason == SYMPTOM_CRASHLOOP:
            signals.append(pod_signal(pod, reason, SYMPTOM_CRASHLOOP, PRIORITY_CRASHLOOP))
    if SIGNAL_OOM_KILLED in text_items(pod.get("terminated_reasons")):
        signals.append(pod_signal(pod, SIGNAL_OOM_KILLED, SYMPTOM_CRASHLOOP, PRIORITY_OOM))
    if str(pod.get("phase") or "") == "Pending" and not pod.get("node_name"):
        signals.append(
            pod_signal(
                pod, SIGNAL_FAILED_SCHEDULING, SYMPTOM_FAILED_SCHEDULING, PRIORITY_SCHEDULING
            )
        )
    # readiness 신호는 상위 신호가 없는 파드에서만 — crashloop 파드의 Ready=False 는
    # 원인이 아니라 결과라 secondary 노이즈만 만든다.
    if not signals and str(pod.get("phase") or "") == "Running" and pod_not_ready(pod):
        signals.append(
            pod_signal(
                pod,
                SIGNAL_POD_NOT_READY,
                SYMPTOM_POD_NOT_READY,
                PRIORITY_POD_NOT_READY,
            )
        )
    return signals


def pod_signal(pod: JsonObject, signal: str, symptom: str, priority: int) -> SymptomSignal:
    kind, name = pod_resource_hint(pod)
    return SymptomSignal(
        signal=signal,
        symptom=symptom,
        priority=priority,
        resource_kind=kind,
        resource_name=name,
        namespace=optional_text(pod.get("namespace")),
        weight=int(pod.get("restart_total") or 0),
    )


def pod_resource_hint(pod: JsonObject) -> tuple[str, str]:
    """incident 대상 힌트 — 소유 워크로드(ReplicaSet 등)가 있으면 그쪽, 없으면 Pod."""
    owner_kind = optional_text(pod.get("owner_kind"))
    owner_name = optional_text(pod.get("owner_name"))
    if owner_kind and owner_name:
        return (owner_kind, owner_name)
    return ("Pod", str(pod.get("name") or "unknown"))


def pod_not_ready(pod: JsonObject) -> bool:
    for condition in pod.get("conditions") or []:
        if not isinstance(condition, dict):
            continue
        if str(condition.get("type")) == "Ready":
            return str(condition.get("status")) != "True"
    return False


def event_signals(
    event: JsonObject,
    pods: list[JsonObject] | None = None,
) -> list[SymptomSignal]:
    """warning 이벤트에서 신호 추출 — reason/message 조합이 근거."""
    reason = str(event.get("reason") or "")
    message = str(event.get("message") or "").lower()
    if reason == "FailedScheduling":
        return [
            event_signal(
                event, SIGNAL_FAILED_SCHEDULING, SYMPTOM_FAILED_SCHEDULING, PRIORITY_SCHEDULING
            )
        ]
    if reason == "OOMKilling" or "oomkilled" in message:
        return [event_signal(event, SIGNAL_OOM_KILLED, SYMPTOM_CRASHLOOP, PRIORITY_OOM)]
    if reason == "Failed" and ("pull image" in message or "errimagepull" in message):
        return [event_signal(event, "ErrImagePull", SYMPTOM_IMAGE_PULL, PRIORITY_IMAGE_PULL)]
    if reason == "BackOff" and "pulling image" in message:
        return [event_signal(event, SYMPTOM_IMAGE_PULL, SYMPTOM_IMAGE_PULL, PRIORITY_IMAGE_PULL)]
    if reason == "BackOff" and "restarting failed container" in message:
        return [event_signal(event, SYMPTOM_CRASHLOOP, SYMPTOM_CRASHLOOP, PRIORITY_CRASHLOOP)]
    if reason == "Unhealthy" and "probe" in message:
        return [
            event_signal(
                event,
                probe_signal_label(message),
                probe_failure_symptom(message),
                PRIORITY_PROBE,
                pods=pods,
            )
        ]
    return []


def probe_signal_label(message: str) -> str:
    if "readiness probe" in message:
        return "ReadinessProbeFailed"
    if "liveness probe" in message:
        return "LivenessProbeFailed"
    if "startup probe" in message:
        return "StartupProbeFailed"
    return SIGNAL_PROBE_FAILED


def probe_failure_symptom(message: str) -> str:
    if "readiness probe" in message:
        return SYMPTOM_READINESS_PROBE_FAILURE
    if "liveness probe" in message:
        return SYMPTOM_LIVENESS_PROBE_FAILURE
    if "startup probe" in message:
        return SYMPTOM_STARTUP_PROBE_FAILURE
    return SYMPTOM_PROBE_FAILURE


def event_signal(
    event: JsonObject,
    signal: str,
    symptom: str,
    priority: int,
    *,
    pods: list[JsonObject] | None = None,
) -> SymptomSignal:
    resource_kind = str(event.get("involved_kind") or "Unknown")
    resource_name = str(event.get("involved_name") or "unknown")
    namespace = optional_text(event.get("namespace"))
    if resource_kind == "Pod":
        current_pod = next(
            (
                pod
                for pod in pods or []
                if str(pod.get("name") or "") == resource_name
                and optional_text(pod.get("namespace")) == namespace
            ),
            None,
        )
        if current_pod is not None:
            resource_kind, resource_name = pod_resource_hint(current_pod)
    return SymptomSignal(
        signal=signal,
        symptom=symptom,
        priority=priority,
        resource_kind=resource_kind,
        resource_name=resource_name,
        namespace=namespace,
        source_rank=SOURCE_RANK_EVENT,
        weight=int(event.get("count") or 0),
    )


def service_endpoint_signals(kubernetes: JsonObject) -> list[SymptomSignal]:
    """selector 가 어떤 파드도 못 잡아 EndpointSlice 가 빈 Service 를 찾는다.

    보수적 판정 — 오탐(RBAC 로 endpoints 미수집 등)을 막기 위해 세 조건을 모두 요구한다:
    ① Service 에 selector 가 있고, ② 그 Service 의 EndpointSlice 가 실제로 수집됐는데
    endpoint 합이 0 이며, ③ snapshot 의 어떤 파드도 selector 라벨을 만족하지 않는다.
    """
    pods = snapshot_items(kubernetes, "pods")
    endpoints = snapshot_items(kubernetes, "endpoints")
    signals: list[SymptomSignal] = []
    for service in snapshot_items(kubernetes, "services"):
        selector = service.get("selector")
        if not isinstance(selector, dict) or not selector:
            continue
        slices = [item for item in endpoints if slice_belongs_to_service(item, service)]
        if not slices:
            continue
        if sum(int(item.get("endpoint_count") or 0) for item in slices) > 0:
            continue
        if any(selector_matches_pod(selector, pod) for pod in pods):
            continue
        signals.append(
            SymptomSignal(
                signal=SIGNAL_SERVICE_ENDPOINTS_EMPTY,
                symptom=SYMPTOM_SERVICE_ENDPOINTS_EMPTY,
                priority=PRIORITY_SERVICE_ENDPOINT,
                resource_kind="Service",
                resource_name=str(service.get("name") or "unknown"),
                namespace=optional_text(service.get("namespace")),
                source_rank=SOURCE_RANK_SERVICE,
            )
        )
    return signals


def slice_belongs_to_service(endpoint_slice: JsonObject, service: JsonObject) -> bool:
    if optional_text(endpoint_slice.get("namespace")) != optional_text(service.get("namespace")):
        return False
    slice_name = str(endpoint_slice.get("name") or "")
    service_name = str(service.get("name") or "")
    if not slice_name or not service_name:
        return False
    return slice_name == service_name or slice_name.startswith(f"{service_name}-")


def selector_matches_pod(selector: dict, pod: JsonObject) -> bool:
    labels = pod.get("labels")
    if not isinstance(labels, dict):
        return False
    return all(str(labels.get(str(key))) == str(value) for key, value in selector.items())


def text_items(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [str(item) for item in value if item]


def optional_text(value: object) -> str | None:
    return str(value) if value else None
