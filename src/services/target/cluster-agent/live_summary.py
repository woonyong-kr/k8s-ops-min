"""live summary 생산자 — cluster-agent → management realtime-gateway outbound WS.

경계 원칙:
- agent 는 browser fan-out 을 모름. 클러스터당 outbound 연결 1개만 유지하고,
  사용자 수 확장은 전적으로 realtime-gateway 의 책임임.
- payload 는 계약(LiveSummary)이 강제하는 bounded 요약만 — raw metric/전체 pod 목록 금지.
- 실패 시 backoff 재접속. 기존 evidence/command 경로에는 영향 없음(끄면 no-op).
"""

from __future__ import annotations

import asyncio
import json
import time
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager
from datetime import UTC, datetime
from typing import Any, Protocol

import httpx
from kubernetes_api import (
    kubernetes_api_base_url,
    kubernetes_client,
    kubernetes_headers,
    service_account_token,
)
from live_resource_metrics import (
    BoundedNodeTargetStore,
    NodeClusterResourceMetricsCollector,
    PodResourceMetricsCollector,
    collection_interval_for_pods,
)

import config as agent_config
from packages.config.logs import CONTEXT_KEY, get_logger
from packages.config.realtime import derive_realtime_gateway_url
from packages.config.settings import env
from packages.contracts.gateway.fields import Gateway
from packages.contracts.port_forward import AgentPortForwardEvent
from packages.contracts.realtime import (
    AGENT_LIVE_PATH,
    MAX_HOT_PODS,
    HotPod,
    LiveClusterResourceObservation,
    LiveMetricsMetadata,
    LiveSummary,
    LiveSummaryMessage,
    ResourceDelta,
)
from packages.contracts.terminal import (
    TerminalConnected,
    TerminalEnd,
    TerminalError,
    TerminalOutput,
)

LOGGER = get_logger(__name__)

CRASH_LOOP_REASON = "CrashLoopBackOff"

SummaryCollector = Callable[[], Awaitable[LiveSummary | None]]
ResourceMetricsCollector = Callable[[], Awaitable[LiveClusterResourceObservation]]


class LiveStreamConnection(Protocol):
    async def send(self, message: str | bytes) -> None: ...

    async def recv(self) -> str | bytes: ...


class TerminalController(Protocol):
    async def handle(self, payload: object, emit: Callable[..., Awaitable[None]]) -> bool: ...

    async def close_all(self) -> None: ...


class PortForwardStreamController(Protocol):
    async def handle_control(
        self,
        payload: object,
        emit_event: Callable[[AgentPortForwardEvent], Awaitable[None]],
        emit_data: Callable[[bytes], Awaitable[None]],
    ) -> bool: ...

    async def handle_data(self, raw: bytes) -> bool: ...

    async def close_all(self) -> None: ...


# (url, headers) → async context manager yielding LiveStreamConnection
LiveStreamConnector = Callable[[str, dict[str, str]], AbstractAsyncContextManager[Any]]


def _websockets_connector(url: str, headers: dict[str, str]) -> AbstractAsyncContextManager[Any]:
    import websockets  # 지연 import — 비활성/테스트 경로에서 불필요한 의존 로드 금지

    return websockets.connect(url, additional_headers=headers)


def derive_gateway_url(management_base_url: str) -> str:
    """REALTIME_GATEWAY_URL 미설정 시 관리 API 주소에서 안전한 WS 주소를 유도한다."""
    return derive_realtime_gateway_url(management_base_url)


def _clamp_interval(raw: str) -> float:
    try:
        value = float(raw)
    except ValueError:
        value = float(agent_config.DEFAULT_LIVE_SUMMARY_INTERVAL_SECONDS)
    return min(
        max(value, agent_config.MIN_LIVE_SUMMARY_INTERVAL_SECONDS),
        agent_config.MAX_LIVE_SUMMARY_INTERVAL_SECONDS,
    )


def next_collection_delay(target_interval: float, collection_elapsed: float) -> float:
    """Keep collection start-to-start cadence at the adaptive target interval."""
    return max(0.0, target_interval - max(0.0, collection_elapsed))


def cluster_resource_metrics_key(cluster_id: str) -> str:
    """Stable retained-state key for the single bounded cluster metrics cut."""
    return f"{cluster_id}/cluster/metrics/live"


class KubernetesPodSummaryCollector:
    """k8s pod 목록(상한 있음)에서 bounded 요약을 계산함. API 미접근 환경이면 None."""

    def __init__(
        self,
        cluster_id: str,
        window_ms: int,
        transport: httpx.AsyncBaseTransport | None = None,
        metrics_collector: PodResourceMetricsCollector | None = None,
        node_target_store: BoundedNodeTargetStore | None = None,
    ) -> None:
        self.cluster_id = cluster_id
        self.window_ms = window_ms
        self.transport = transport
        self._last_restart_total: int | None = None
        self._last_resources: dict[str, dict[str, Any]] = {}
        self._pending_deltas: list[ResourceDelta] = []
        self._next_interval_seconds = max(window_ms / 1000, 0.001)
        self._last_collection_started: float | None = None
        self.metrics_collector = metrics_collector or PodResourceMetricsCollector(
            agent_config.LIVE_RESOURCE_NODE_CONCURRENCY
        )
        self.node_target_store = node_target_store

    async def __call__(self) -> LiveSummary | None:
        base_url = kubernetes_api_base_url()
        token = service_account_token()
        if not base_url or not token:
            return None
        pods: list[dict[str, Any]] = []
        headers = kubernetes_headers(token)
        collection_started = time.monotonic()
        actual_interval_seconds = (
            collection_started - self._last_collection_started
            if self._last_collection_started is not None
            else self._next_interval_seconds
        )
        self._last_collection_started = collection_started
        async with kubernetes_client(self.transport) as client:
            await self._refresh_node_targets(client, base_url, headers)
            continuation = ""
            while len(pods) < agent_config.LIVE_SUMMARY_POD_TOTAL_LIMIT:
                params: dict[str, str | int] = {"limit": agent_config.LIVE_SUMMARY_POD_LIST_LIMIT}
                if continuation:
                    params["continue"] = continuation
                response = await client.get(
                    f"{base_url}/api/v1/pods",
                    params=params,
                    headers=headers,
                )
                response.raise_for_status()
                payload = response.json()
                remaining = agent_config.LIVE_SUMMARY_POD_TOTAL_LIMIT - len(pods)
                pods.extend(payload.get("items", [])[:remaining])
                continuation = str(payload.get("metadata", {}).get("continue") or "")
                if not continuation:
                    break
            measured = await self.metrics_collector.collect(
                client,
                base_url=base_url,
                headers=headers,
                pods=pods,
                actual_interval_seconds=max(actual_interval_seconds, 0.0),
            )
        return self.summarize(pods, measured, observed_at=datetime.now(UTC))

    async def _refresh_node_targets(
        self,
        client: Any,
        base_url: str,
        headers: dict[str, str],
    ) -> None:
        store = self.node_target_store
        if store is None:
            return
        try:
            response = await client.get(
                f"{base_url.rstrip('/')}/api/v1/nodes",
                params={"limit": store.max_nodes + 1},
                headers=headers,
            )
        except httpx.HTTPError:
            store.mark_unavailable("kubernetes_nodes_request_failed")
            return
        if response.is_error:
            store.mark_unavailable(f"kubernetes_nodes_http_{response.status_code}")
            return
        try:
            store.update(response.json())
        except ValueError:
            store.mark_unavailable("kubernetes_nodes_invalid_payload")

    def summarize(
        self,
        pods: list[dict[str, Any]],
        pod_metrics: dict[str, dict[str, Any]] | None = None,
        *,
        observed_at: datetime | None = None,
    ) -> LiveSummary:
        self._next_interval_seconds = collection_interval_for_pods(len(pods))
        window_ms = int(self._next_interval_seconds * 1000)
        ready_count = 0
        restart_total = 0
        crash_looping = False
        hot_pods: list[HotPod] = []
        next_resources: dict[str, dict[str, Any]] = {}
        for pod in pods:
            namespace = pod.get("metadata", {}).get("namespace", "")
            name = pod.get("metadata", {}).get("name", "")
            statuses = pod.get("status", {}).get("containerStatuses", [])
            ready = bool(statuses) and all(status.get("ready", False) for status in statuses)
            restarts = sum(int(status.get("restartCount", 0)) for status in statuses)
            phase = str(pod.get("status", {}).get("phase") or "Unknown")
            node_name = str(pod.get("spec", {}).get("nodeName") or "")
            owner_kind, owner_name = pod_owner(pod)
            crash = any(
                status.get("state", {}).get("waiting", {}).get("reason", "") == CRASH_LOOP_REASON
                for status in statuses
            )
            ready_count += int(ready)
            restart_total += restarts
            crash_looping = crash_looping or crash
            if (not ready or restarts > 0) and len(hot_pods) < MAX_HOT_PODS and namespace and name:
                hot_pods.append(
                    HotPod(namespace=namespace, pod=name, restart_count=restarts, ready=ready)
                )
            if namespace and name:
                key = f"{self.cluster_id}/{namespace}/pod/{name}"
                metrics = (pod_metrics or {}).get(f"{namespace}/{name}", {})
                next_resources[key] = {
                    "resource_type": "pod",
                    "kind": "Pod",
                    "name": name,
                    "namespace": namespace,
                    "phase": phase,
                    "ready": "1/1" if ready else "0/1",
                    "restarts": restarts,
                    "node": node_name,
                    "owner_kind": owner_kind,
                    "owner_name": owner_name,
                    "health": pod_health(phase, ready, restarts),
                    **metrics,
                }
        self._pending_deltas = resource_deltas(
            self._last_resources,
            next_resources,
            observed_at=observed_at or datetime.now(UTC),
        )
        self._last_resources = next_resources
        restart_delta = (
            max(0, restart_total - self._last_restart_total)
            if self._last_restart_total is not None
            else 0
        )
        self._last_restart_total = restart_total
        if crash_looping:
            phase = "degraded"
        elif ready_count < len(pods):
            phase = "progressing"
        else:
            phase = "idle"
        metrics_metadata = aggregate_metrics_metadata(pod_metrics)
        return LiveSummary(
            cluster_id=self.cluster_id,
            window_ms=window_ms,
            pods_ready=ready_count,
            pods_total=len(pods),
            restart_delta=restart_delta,
            rollout_phase=phase,
            hot_pods=hot_pods,
            metrics_metadata=metrics_metadata,
        )

    def next_interval_seconds(self) -> float:
        """다음 publisher sleep이 사용할 현재 적응 주기."""
        return self._next_interval_seconds

    def drain_deltas(self) -> list[ResourceDelta]:
        deltas = self._pending_deltas
        self._pending_deltas = []
        return deltas


class KubernetesClusterResourceCollector:
    """Bounded 1 Hz node/cluster metrics collector, independent of pod topology."""

    def __init__(
        self,
        cluster_id: str,
        transport: httpx.AsyncBaseTransport | None = None,
        metrics_collector: NodeClusterResourceMetricsCollector | None = None,
        node_target_store: BoundedNodeTargetStore | None = None,
    ) -> None:
        self.cluster_id = cluster_id
        self.transport = transport
        self.metrics_collector = metrics_collector or NodeClusterResourceMetricsCollector(
            cluster_id,
            agent_config.LIVE_RESOURCE_NODE_CONCURRENCY,
        )
        self.node_target_store = node_target_store or BoundedNodeTargetStore()
        self._last_collection_started: float | None = None
        self._actual_interval_seconds = agent_config.LIVE_RESOURCE_METRICS_INTERVAL_SECONDS

    async def __call__(self) -> LiveClusterResourceObservation:
        collection_started = time.monotonic()
        self._actual_interval_seconds = (
            collection_started - self._last_collection_started
            if self._last_collection_started is not None
            else agent_config.LIVE_RESOURCE_METRICS_INTERVAL_SECONDS
        )
        self._last_collection_started = collection_started
        base_url = kubernetes_api_base_url()
        token = service_account_token()
        if not base_url or not token:
            return self.unavailable("kubernetes_api_not_configured")
        targets = self.node_target_store.snapshot()
        if not targets.names and not targets.complete:
            return self.unavailable(targets.degraded_reason or "node_targets_not_observed")
        async with kubernetes_client(self.transport) as client:
            return await self.metrics_collector.collect(
                client,
                base_url=base_url,
                headers=kubernetes_headers(token),
                node_targets=targets.names,
                targets_complete=targets.complete,
                targets_degraded_reason=targets.degraded_reason,
                actual_interval_seconds=max(self._actual_interval_seconds, 0.0),
            )

    def unavailable(self, reason: str) -> LiveClusterResourceObservation:
        return self.metrics_collector.unavailable(
            reason,
            actual_interval_seconds=max(self._actual_interval_seconds, 0.0),
        )


class LiveSummaryPublisher:
    """주기적으로 요약을 수집해 realtime-gateway 로 push. 끄면(run 즉시 반환) no-op."""

    def __init__(
        self,
        *,
        cluster_id: str,
        gateway_url: str,
        token: str,
        interval_seconds: float,
        collector: SummaryCollector,
        resource_metrics_collector: ResourceMetricsCollector | None = None,
        connect: LiveStreamConnector | None = None,
        terminal_controller: TerminalController | None = None,
        port_forward_controller: PortForwardStreamController | None = None,
        retry_delay_seconds: float = agent_config.LIVE_SUMMARY_RETRY_DELAY_SECONDS,
        enabled: bool = True,
    ) -> None:
        self.cluster_id = cluster_id
        self.gateway_url = gateway_url.rstrip("/")
        self.token = token
        self.interval_seconds = interval_seconds
        self.collector = collector
        self.resource_metrics_collector = resource_metrics_collector
        self.connect = connect or _websockets_connector
        self.terminal_controller = terminal_controller
        self.port_forward_controller = port_forward_controller
        self.retry_delay_seconds = retry_delay_seconds
        self.enabled = enabled

    @classmethod
    def from_env(
        cls,
        cluster_id: str,
        management_base_url: str,
        kubernetes_transport: httpx.AsyncBaseTransport | None = None,
        terminal_controller: TerminalController | None = None,
        port_forward_controller: PortForwardStreamController | None = None,
    ) -> LiveSummaryPublisher:
        enabled = (
            env(
                agent_config.LIVE_SUMMARY_ENABLED_ENV, agent_config.DEFAULT_LIVE_SUMMARY_ENABLED
            ).lower()
            == "true"
        )
        interval = _clamp_interval(
            env(
                agent_config.LIVE_SUMMARY_INTERVAL_SECONDS_ENV,
                agent_config.DEFAULT_LIVE_SUMMARY_INTERVAL_SECONDS,
            )
        )
        gateway_url = env(agent_config.REALTIME_GATEWAY_URL_ENV, "") or derive_gateway_url(
            management_base_url
        )
        node_target_store = BoundedNodeTargetStore()
        return cls(
            cluster_id=cluster_id,
            gateway_url=gateway_url,
            token=env(agent_config.AGENT_TOKEN_ENV, ""),
            interval_seconds=interval,
            collector=KubernetesPodSummaryCollector(
                cluster_id,
                int(interval * 1000),
                kubernetes_transport,
                node_target_store=node_target_store,
            ),
            resource_metrics_collector=KubernetesClusterResourceCollector(
                cluster_id,
                kubernetes_transport,
                node_target_store=node_target_store,
            ),
            terminal_controller=terminal_controller,
            port_forward_controller=port_forward_controller,
            enabled=enabled,
        )

    @property
    def endpoint(self) -> str:
        return f"{self.gateway_url}{AGENT_LIVE_PATH}?{Gateway.CLUSTER_ID}={self.cluster_id}"

    async def run(self) -> None:
        if not self.enabled or not self.gateway_url:
            LOGGER.info(
                "live_summary_disabled",
                extra={CONTEXT_KEY: {Gateway.CLUSTER_ID: self.cluster_id, "enabled": self.enabled}},
            )
            return
        headers = {agent_config.AGENT_TOKEN_HEADER: self.token}
        while True:
            try:
                async with self.connect(self.endpoint, headers) as connection:
                    await self._stream(connection)
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                LOGGER.warning(
                    "live_summary_stream_retry",
                    extra={
                        CONTEXT_KEY: {
                            Gateway.CLUSTER_ID: self.cluster_id,
                            "exception_type": type(exc).__name__,
                        }
                    },
                )
                await asyncio.sleep(self.retry_delay_seconds)

    async def _stream(self, connection: LiveStreamConnection) -> None:
        send_lock = asyncio.Lock()

        async def send_model(
            message: TerminalConnected | TerminalOutput | TerminalEnd | TerminalError,
        ) -> None:
            async with send_lock:
                await connection.send(message.model_dump_json())

        async def send_port_forward_event(message: AgentPortForwardEvent) -> None:
            async with send_lock:
                await connection.send(message.model_dump_json())

        async def send_port_forward_data(frame: bytes) -> None:
            async with send_lock:
                await connection.send(frame)

        producers = [asyncio.create_task(self._publish_loop(connection, send_lock))]
        if self.resource_metrics_collector is not None:
            producers.append(
                asyncio.create_task(self._resource_metrics_loop(connection, send_lock))
            )
        if self.terminal_controller is None and self.port_forward_controller is None:
            try:
                await asyncio.gather(*producers)
            finally:
                for producer in producers:
                    producer.cancel()
                await asyncio.gather(*producers, return_exceptions=True)
            return
        try:
            while True:
                raw = await connection.recv()
                if isinstance(raw, bytes):
                    if (
                        self.port_forward_controller is not None
                        and await self.port_forward_controller.handle_data(raw)
                    ):
                        continue
                    raise ValueError("invalid binary frame on the agent realtime stream")
                try:
                    payload = json.loads(raw)
                except (TypeError, UnicodeDecodeError, json.JSONDecodeError):
                    continue
                handled = False
                if self.terminal_controller is not None:
                    handled = await self.terminal_controller.handle(payload, send_model)
                if not handled and self.port_forward_controller is not None:
                    await self.port_forward_controller.handle_control(
                        payload,
                        send_port_forward_event,
                        send_port_forward_data,
                    )
        finally:
            for producer in producers:
                producer.cancel()
            await asyncio.gather(*producers, return_exceptions=True)
            if self.terminal_controller is not None:
                await self.terminal_controller.close_all()
            if self.port_forward_controller is not None:
                await self.port_forward_controller.close_all()

    async def _publish_loop(
        self,
        connection: LiveStreamConnection,
        send_lock: asyncio.Lock,
    ) -> None:
        while True:
            collection_started = time.monotonic()
            summary = await self.collector()
            if summary is not None:
                drain = getattr(self.collector, "drain_deltas", None)
                if callable(drain):
                    for delta in drain():
                        async with send_lock:
                            await connection.send(delta.model_dump_json())
                # gateway는 summary 시점의 최신 delta cut을 replay/alert 저장소에 남긴다.
                # summary를 먼저 보내면 저장/평가가 항상 한 수집 주기 뒤처진다.
                message = LiveSummaryMessage(cluster_id=self.cluster_id, summary=summary)
                async with send_lock:
                    await connection.send(message.model_dump_json())
            next_interval = getattr(self.collector, "next_interval_seconds", None)
            target_interval = next_interval() if callable(next_interval) else self.interval_seconds
            delay = next_collection_delay(
                target_interval,
                time.monotonic() - collection_started,
            )
            await asyncio.sleep(delay)

    async def _resource_metrics_loop(
        self,
        connection: LiveStreamConnection,
        send_lock: asyncio.Lock,
    ) -> None:
        collector = self.resource_metrics_collector
        if collector is None:
            return
        while True:
            collection_started = time.monotonic()
            try:
                observation = await asyncio.wait_for(
                    collector(),
                    timeout=agent_config.LIVE_RESOURCE_METRICS_TIMEOUT_SECONDS,
                )
            except TimeoutError:
                unavailable = getattr(collector, "unavailable", None)
                if not callable(unavailable):
                    raise
                observation = unavailable("node_metrics_collection_timeout")
            delta = ResourceDelta(
                key=cluster_resource_metrics_key(self.cluster_id),
                value=observation.model_dump(mode="json"),
                observed_at=observation.observed_at or observation.status_observed_at,
            )
            async with send_lock:
                await connection.send(delta.model_dump_json())
            await asyncio.sleep(
                next_collection_delay(
                    agent_config.LIVE_RESOURCE_METRICS_INTERVAL_SECONDS,
                    time.monotonic() - collection_started,
                )
            )


def aggregate_metrics_metadata(
    pod_metrics: dict[str, dict[str, Any]] | None,
) -> LiveMetricsMetadata | None:
    if not pod_metrics:
        return None
    entries = [
        value.get("metrics_metadata")
        for value in pod_metrics.values()
        if isinstance(value.get("metrics_metadata"), dict)
    ]
    if not entries:
        return None
    sources = {str(entry.get("source") or "unavailable") for entry in entries}
    source = next(iter(sources)) if len(sources) == 1 else "mixed"
    intervals = [
        float(entry["actual_interval_seconds"])
        for entry in entries
        if entry.get("actual_interval_seconds") is not None
    ]
    reasons = sorted(
        {str(entry["degraded_reason"]) for entry in entries if entry.get("degraded_reason")}
    )
    return LiveMetricsMetadata(
        source=source,
        actual_interval_seconds=max(intervals) if intervals else None,
        degraded_reason=",".join(reasons) or None,
    )


def resource_deltas(
    before: dict[str, dict[str, Any]],
    after: dict[str, dict[str, Any]],
    *,
    observed_at: datetime,
) -> list[ResourceDelta]:
    deltas: list[ResourceDelta] = []
    for key, value in after.items():
        if before.get(key) != value:
            deltas.append(
                ResourceDelta(op="replace", key=key, value=value, observed_at=observed_at)
            )
    for key in before.keys() - after.keys():
        deltas.append(ResourceDelta(op="remove", key=key, value=None, observed_at=observed_at))
    return deltas


def pod_owner(pod: dict[str, Any]) -> tuple[str, str]:
    owners = pod.get("metadata", {}).get("ownerReferences", [])
    if isinstance(owners, list) and owners:
        owner = owners[0] if isinstance(owners[0], dict) else {}
        return str(owner.get("kind") or ""), str(owner.get("name") or "")
    return "", ""


def pod_health(phase: str, ready: bool, restarts: int) -> str:
    if phase != "Running" or not ready:
        return "critical"
    if restarts > 0:
        return "warning"
    return "healthy"
