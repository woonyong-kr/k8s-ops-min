"""Realtime 계약(realtime.v1) — cluster-agent → realtime-gateway → browser fan-out.

경계 원칙:
- node-collector 는 이 경로에 참여하지 않음(/metrics 는 Prometheus scrape 가 정식 경로).
- agent 가 보내는 live summary 는 raw metric 이 아니라 bounded 요약이어야 함
  (raw Prometheus 응답/전체 로그/무제한 pod 목록 금지 — 상한을 계약으로 강제).
- 메시지는 StrictModel(extra 금지) — 계약 밖 필드는 즉시 검증 실패(fail-fast).
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Annotated, Any, Literal, Self

from pydantic import Field, TypeAdapter, field_validator, model_validator

from packages.contracts.gateway.base import StrictModel

REALTIME_PROTOCOL = "realtime.v1"

# WebSocket 경로 — producer(cluster-agent)와 gateway 가 같은 계약을 import 함(중복 리터럴 금지).
AGENT_LIVE_PATH = "/live/agent"
BROWSER_LIVE_PATH = "/live/browser"

# snapshot.state 구조 키 — browser 소비자가 최초 렌더링에 쓰는 read model 의 계약.
STATE_CLUSTERS_KEY = "clusters"
STATE_RESOURCES_KEY = "resources"

# live summary 상한 — 사용자 수와 무관하게 agent payload 가 bounded 이도록 계약으로 강제.
MAX_HOT_PODS = 20
MAX_WINDOW_MS = 60_000
MAX_LIVE_NODE_OBSERVATIONS = 18

# browser fan-out 상한 — 느린 client 는 밀린 메시지를 버리고 최신 snapshot 으로 복구함.
BROWSER_QUEUE_MAX = 32
BROWSER_STREAM_POLICY_REVISION = 1
BROWSER_STREAM_MAX_FRAMES_PER_SECOND = 60

# resource.delta key 형식: "<cluster>/<namespace>/<kind>/<name>"
DELTA_KEY_SEGMENTS = 4
DELTA_KEY_SEGMENT_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]*$")


@dataclass(frozen=True)
class RealtimeIngressLimits:
    """Explicit ingress/cache budgets shared by contract validation, hub, and gateway."""

    delta_key_max_length: int = 1_024
    delta_value_max_bytes: int = 16 * 1_024
    delta_value_max_fields: int = 256
    delta_value_max_depth: int = 8
    agent_message_max_bytes: int = 32 * 1_024
    agent_ingress_window_seconds: float = 60.0
    agent_messages_per_window: int = 6_000
    agent_bytes_per_window: int = 32 * 1_024 * 1_024
    cluster_retained_resources: int = 5_000
    snapshot_max_resources: int = 5_000
    snapshot_max_bytes: int = 16 * 1_024 * 1_024


class RealtimeLimitError(ValueError):
    """A producer or retained state exceeded a declared realtime budget."""

    def __init__(self, code: str) -> None:
        super().__init__(code)
        self.code = code


RolloutPhase = Literal["idle", "progressing", "degraded"]
DeltaOp = Literal["replace", "remove"]
MetricSource = Literal[
    "kubelet_stats_summary",
    "metrics_server_fallback",
    "mixed",
    "unavailable",
]
LiveNodeStatus = Literal["ready", "not_ready", "unknown"]
LiveClusterStatus = Literal["ready", "degraded", "unknown"]


class HotPod(StrictModel):
    """주의가 필요한 pod 1개 — 목록 크기는 MAX_HOT_PODS 로 제한됨."""

    namespace: str = Field(min_length=1)
    pod: str = Field(min_length=1)
    cpu_ratio: float | None = Field(default=None, ge=0.0)
    restart_count: int = Field(default=0, ge=0)
    ready: bool = True


class LiveMetricsMetadata(StrictModel):
    """실시간 측정 출처와 실제 관측 간격 — 추정값과 실측값의 혼동을 막는다."""

    source: MetricSource
    actual_interval_seconds: float | None = Field(default=None, ge=0.0)
    degraded_reason: str | None = None


class LiveNodeResourceObservation(StrictModel):
    """One bounded, real node observation carried inside a cluster metrics delta."""

    name: str = Field(min_length=1)
    status: LiveNodeStatus
    cpu_mcores: float | None = Field(default=None, ge=0.0)
    mem_mib: float | None = Field(default=None, ge=0.0)
    cpu_capacity_mcores: float | None = Field(default=None, gt=0.0)
    mem_capacity_mib: float | None = Field(default=None, gt=0.0)
    cpu_pct: float | None = Field(default=None, ge=0.0)
    mem_pct: float | None = Field(default=None, ge=0.0)
    observed_at: datetime | None = None
    source: MetricSource
    stale: bool
    degraded_reason: str | None = None
    status_observed_at: datetime | None = None
    status_source: Literal["kubernetes_api"] = "kubernetes_api"
    status_stale: bool

    @model_validator(mode="after")
    def _validate_observation_evidence(self) -> Self:
        if not self.stale and (
            self.cpu_mcores is None or self.mem_mib is None or self.observed_at is None
        ):
            raise ValueError("fresh node metrics require measured cpu, memory, and observed_at")
        if not self.status_stale and self.status_observed_at is None:
            raise ValueError("fresh node status requires status_observed_at")
        if self.cpu_pct is not None and (
            self.cpu_mcores is None or self.cpu_capacity_mcores is None
        ):
            raise ValueError("node cpu percentage requires measured usage and capacity")
        if self.mem_pct is not None and (
            self.mem_mib is None or self.mem_capacity_mib is None
        ):
            raise ValueError("node memory percentage requires measured usage and capacity")
        return self


class LiveClusterResourceObservation(StrictModel):
    """Bounded node/cluster CPU, memory, and status cut for one realtime tick.

    The observation travels as one ``resource.delta`` value.  Keeping all nodes in
    one bounded value prevents a node-sized cluster from turning a 1 Hz metric tick
    into an unbounded frame burst.
    """

    resource_type: Literal["cluster_metrics"] = "cluster_metrics"
    kind: Literal["ClusterMetrics"] = "ClusterMetrics"
    cluster_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    actual_interval_seconds: float | None = Field(default=None, ge=0.0)
    collection_complete: bool
    status: LiveClusterStatus
    cpu_mcores: float | None = Field(default=None, ge=0.0)
    mem_mib: float | None = Field(default=None, ge=0.0)
    cpu_capacity_mcores: float | None = Field(default=None, gt=0.0)
    mem_capacity_mib: float | None = Field(default=None, gt=0.0)
    cpu_pct: float | None = Field(default=None, ge=0.0)
    mem_pct: float | None = Field(default=None, ge=0.0)
    observed_at: datetime | None = None
    source: MetricSource
    stale: bool
    degraded_reason: str | None = None
    status_observed_at: datetime | None = None
    status_source: Literal["kubernetes_api"] = "kubernetes_api"
    status_stale: bool
    nodes_ready: int | None = Field(default=None, ge=0)
    nodes_total: int | None = Field(default=None, ge=0)
    nodes: list[LiveNodeResourceObservation] = Field(
        default_factory=list,
        max_length=MAX_LIVE_NODE_OBSERVATIONS,
    )

    @model_validator(mode="after")
    def _validate_observation_evidence(self) -> Self:
        if not self.stale and (
            self.cpu_mcores is None or self.mem_mib is None or self.observed_at is None
        ):
            raise ValueError("fresh cluster metrics require measured cpu, memory, and observed_at")
        if not self.status_stale and self.status_observed_at is None:
            raise ValueError("fresh cluster status requires status_observed_at")
        if self.collection_complete:
            if self.nodes_total != len(self.nodes):
                raise ValueError("complete cluster metrics require exact nodes_total")
            ready = sum(node.status == "ready" for node in self.nodes)
            if self.nodes_ready != ready:
                raise ValueError("complete cluster metrics require exact nodes_ready")
        elif self.nodes_total is not None or self.nodes_ready is not None:
            raise ValueError("partial cluster metrics cannot claim node totals")
        if self.cpu_pct is not None and (
            self.cpu_mcores is None or self.cpu_capacity_mcores is None
        ):
            raise ValueError("cluster cpu percentage requires measured usage and capacity")
        if self.mem_pct is not None and (
            self.mem_mib is None or self.mem_capacity_mib is None
        ):
            raise ValueError("cluster memory percentage requires measured usage and capacity")
        return self


class LiveSummary(StrictModel):
    """cluster-agent 가 주기 송신하는 클러스터 요약 — raw metric 금지."""

    cluster_id: str = Field(min_length=1)
    window_ms: int = Field(default=1000, ge=0, le=MAX_WINDOW_MS)
    pods_ready: int = Field(default=0, ge=0)
    pods_total: int = Field(default=0, ge=0)
    restart_delta: int = Field(default=0, ge=0)
    rollout_phase: RolloutPhase = "idle"
    hot_pods: list[HotPod] = Field(default_factory=list, max_length=MAX_HOT_PODS)
    metrics_metadata: LiveMetricsMetadata | None = None


class Subscription(StrictModel):
    """browser 구독 필터 — 빈 문자열은 '전체 허용'."""

    workspace_id: str = Field(min_length=1)
    cluster_id: str = ""
    namespace: str = ""
    app: str = ""


class BrowserStreamPolicy(StrictModel):
    """Browser delivery budget negotiated by the gateway before a snapshot."""

    revision: int = Field(default=BROWSER_STREAM_POLICY_REVISION, ge=1)
    max_frames_per_second: int = Field(
        default=BROWSER_STREAM_MAX_FRAMES_PER_SECOND,
        ge=1,
        le=BROWSER_STREAM_MAX_FRAMES_PER_SECOND,
    )
    hidden_tab: Literal["coalesce"] = "coalesce"
    max_pending_messages: int = Field(default=BROWSER_QUEUE_MAX, ge=1, le=BROWSER_QUEUE_MAX)


class HelloMessage(StrictModel):
    type: Literal["hello"] = "hello"
    protocol: str = REALTIME_PROTOCOL
    stream_policy: BrowserStreamPolicy = Field(default_factory=BrowserStreamPolicy)


class SnapshotMessage(StrictModel):
    """접속(또는 overflow 복구) 시 1회 전송되는 최신 상태 전체."""

    type: Literal["snapshot"] = "snapshot"
    seq: int = Field(default=0, ge=0)
    state: dict[str, Any] = Field(default_factory=dict)


class ResyncRequiredMessage(StrictModel):
    """The gateway deliberately refused an oversized complete state cut."""

    type: Literal["resync.required"] = "resync.required"
    code: Literal["snapshot_limit_exceeded"] = "snapshot_limit_exceeded"
    retryable: bool = True


class LiveSummaryMessage(StrictModel):
    """agent → gateway ingest, gateway → browser fan-out 공용. seq 는 gateway 가 부여."""

    type: Literal["live.summary"] = "live.summary"
    seq: int = Field(default=0, ge=0)
    cluster_id: str = Field(min_length=1)
    summary: LiveSummary


class ResourceDelta(StrictModel):
    """개별 리소스 변경 1건 — key 는 "<cluster>/<namespace>/<kind>/<name>"."""

    type: Literal["resource.delta"] = "resource.delta"
    seq: int = Field(default=0, ge=0)
    op: DeltaOp = "replace"
    key: str = Field(min_length=1, max_length=1_024)
    value: dict[str, Any] | None = None
    observed_at: datetime | None = None

    @field_validator("key")
    @classmethod
    def _validate_key_format(cls, key: str) -> str:
        validate_delta_key(key)
        return key


class PingMessage(StrictModel):
    type: Literal["ping"] = "ping"
    ts: float


RealtimeMessage = Annotated[
    HelloMessage
    | SnapshotMessage
    | ResyncRequiredMessage
    | LiveSummaryMessage
    | ResourceDelta
    | PingMessage,
    Field(discriminator="type"),
]

# 단일 진입점 — 수신 payload 는 전부 이 어댑터로 검증함(수동 dict 검사 금지).
RealtimeEnvelope: TypeAdapter[RealtimeMessage] = TypeAdapter(RealtimeMessage)


def parse_realtime_message(
    payload: Any, *, limits: RealtimeIngressLimits | None = None
) -> RealtimeMessage:
    message = RealtimeEnvelope.validate_python(payload)
    if isinstance(message, ResourceDelta):
        validate_resource_delta(message, limits or RealtimeIngressLimits())
    return message


def validate_delta_key(key: str, *, max_length: int = 1_024) -> None:
    if len(key) > max_length:
        raise RealtimeLimitError("delta_key_too_large")
    parts = key.split("/")
    if len(parts) != DELTA_KEY_SEGMENTS or any(
        not part or DELTA_KEY_SEGMENT_PATTERN.fullmatch(part) is None for part in parts
    ):
        raise ValueError("resource delta key must have four safe non-empty segments")


def validate_resource_delta(delta: ResourceDelta, limits: RealtimeIngressLimits) -> None:
    validate_delta_key(delta.key, max_length=limits.delta_key_max_length)
    if delta.op == "replace" and delta.value is None:
        raise ValueError("resource delta replace requires a value")
    if delta.value is None:
        return
    try:
        encoded = json.dumps(delta.value, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    except (TypeError, ValueError) as exc:
        raise ValueError("resource delta value must be JSON-compatible") from exc
    if len(encoded) > limits.delta_value_max_bytes:
        raise RealtimeLimitError("delta_value_too_large")
    _validate_json_shape(
        delta.value,
        max_fields=limits.delta_value_max_fields,
        max_depth=limits.delta_value_max_depth,
    )


def serialized_json_bytes(value: object) -> int:
    try:
        return len(json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode("utf-8"))
    except (TypeError, ValueError) as exc:
        raise ValueError("realtime payload must be JSON-compatible") from exc


def _validate_json_shape(value: object, *, max_fields: int, max_depth: int) -> None:
    fields = 0

    def visit(item: object, depth: int) -> None:
        nonlocal fields
        if depth > max_depth:
            raise RealtimeLimitError("delta_value_too_deep")
        if item is None or isinstance(item, str | int | float | bool):
            return
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError("resource delta value keys must be strings")
                fields += 1
                if fields > max_fields:
                    raise RealtimeLimitError("delta_value_too_many_fields")
                visit(child, depth + 1)
            return
        if isinstance(item, list):
            for child in item:
                fields += 1
                if fields > max_fields:
                    raise RealtimeLimitError("delta_value_too_many_fields")
                visit(child, depth + 1)
            return
        raise ValueError("resource delta value must contain JSON values")

    visit(value, 1)


def delta_key_parts(key: str) -> tuple[str, str, str, str]:
    """key → (cluster, namespace, kind, name). 부족한 조각은 빈 문자열."""
    parts = key.split("/", DELTA_KEY_SEGMENTS - 1)
    while len(parts) < DELTA_KEY_SEGMENTS:
        parts.append("")
    return parts[0], parts[1], parts[2], parts[3]
