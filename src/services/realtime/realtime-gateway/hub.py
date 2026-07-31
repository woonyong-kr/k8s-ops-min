"""RealtimeHub — 최신 상태 cache + browser fan-out.

핵심 불변식:
- cluster-agent 연결은 클러스터당 1개. browser 가 몇 명이든 agent 부하는 동일함
  (fan-out 은 전적으로 이 hub 의 책임).
- 느린 browser 는 bounded queue(BROWSER_QUEUE_MAX)가 넘치면 밀린 메시지를 전부 버리고
  최신 snapshot 1개로 복구함 — 메모리 무한 증가 금지.
- seq 는 hub 전역 단조 증가. client 는 seq gap 을 snapshot 복구 신호로 쓸 수 있음.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, field

from packages.contracts.realtime import (
    BROWSER_QUEUE_MAX,
    STATE_CLUSTERS_KEY,
    STATE_RESOURCES_KEY,
    LiveSummary,
    LiveSummaryMessage,
    RealtimeIngressLimits,
    RealtimeLimitError,
    ResourceDelta,
    ResyncRequiredMessage,
    SnapshotMessage,
    Subscription,
    delta_key_parts,
    serialized_json_bytes,
    validate_resource_delta,
)


def _new_queue() -> asyncio.Queue:
    return asyncio.Queue(maxsize=BROWSER_QUEUE_MAX)


@dataclass(eq=False)  # 연결 1개 = 객체 identity(set 등록용 — 값 비교 의미 없음)
class BrowserClient:
    """browser 연결 1개 — 구독 필터와 bounded 송신 queue."""

    subscription: Subscription
    queue: asyncio.Queue = field(default_factory=_new_queue)
    dropped_messages: int = 0


class RealtimeSnapshotLimitError(RealtimeLimitError):
    """The complete current cut cannot be delivered within its explicit budget."""

    def __init__(self) -> None:
        super().__init__("snapshot_limit_exceeded")


class RealtimeResourceLimitError(RealtimeLimitError):
    """A new retained resource would exceed the per-cluster authoritative cut."""

    def __init__(self) -> None:
        super().__init__("cluster_resource_limit_exceeded")


class RealtimeHub:
    """cluster별 최신 요약 + 리소스 상태를 유지하고 browser 로 즉시 fan-out 함."""

    def __init__(self, *, limits: RealtimeIngressLimits | None = None) -> None:
        self.limits = limits or RealtimeIngressLimits()
        self._seq = 0
        self._summaries: dict[str, LiveSummary] = {}
        self._resources: dict[str, dict] = {}
        self._browsers: set[BrowserClient] = set()

    # ---- 조회 ----

    @property
    def seq(self) -> int:
        return self._seq

    @property
    def browser_count(self) -> int:
        return len(self._browsers)

    # ---- browser 등록 ----

    def register_browser(self, subscription: Subscription) -> BrowserClient:
        client = BrowserClient(subscription=subscription)
        self._browsers.add(client)
        return client

    def unregister_browser(self, client: BrowserClient) -> None:
        self._browsers.discard(client)

    def snapshot_for(self, subscription: Subscription) -> SnapshotMessage:
        """구독 필터가 적용된 최신 상태 snapshot. 접속/overflow 복구 시 사용."""
        clusters = {
            cluster_id: summary.model_dump(mode="json")
            for cluster_id, summary in self._summaries.items()
            if _summary_matches(subscription, cluster_id)
        }
        resources = {
            key: value
            for key, value in self._resources.items()
            if _delta_matches(subscription, key, value)
        }
        if len(resources) > self.limits.snapshot_max_resources:
            raise RealtimeSnapshotLimitError()
        state = {STATE_CLUSTERS_KEY: clusters, STATE_RESOURCES_KEY: resources}
        if serialized_json_bytes(state) > self.limits.snapshot_max_bytes:
            raise RealtimeSnapshotLimitError()
        return SnapshotMessage(
            seq=self._seq,
            state=state,
        )

    def resources_for_cluster(self, cluster_id: str) -> dict[str, dict]:
        """Return a detached current resource cut for server-side persistence/evaluation."""
        return {
            key: dict(value)
            for key, value in self._resources.items()
            if delta_key_parts(key)[0] == cluster_id
        }

    # ---- agent ingest → fan-out ----

    def publish_summary(self, summary: LiveSummary) -> LiveSummaryMessage:
        self._seq += 1
        self._summaries[summary.cluster_id] = summary
        message = LiveSummaryMessage(seq=self._seq, cluster_id=summary.cluster_id, summary=summary)
        for client in self._browsers:
            if _summary_matches(client.subscription, summary.cluster_id):
                self._offer(client, message)
        return message

    def publish_delta(self, delta: ResourceDelta) -> ResourceDelta:
        validate_resource_delta(delta, self.limits)
        cluster_id = delta_key_parts(delta.key)[0]
        if delta.op == "replace" and delta.key not in self._resources:
            retained = sum(1 for key in self._resources if delta_key_parts(key)[0] == cluster_id)
            if retained >= self.limits.cluster_retained_resources:
                raise RealtimeResourceLimitError()
        self._seq += 1
        if delta.op == "remove":
            self._resources.pop(delta.key, None)
        else:
            self._resources[delta.key] = delta.value or {}
        message = delta.model_copy(update={"seq": self._seq})
        for client in self._browsers:
            if _delta_matches(client.subscription, delta.key, delta.value):
                self._offer(client, message)
        return message

    # ---- 내부 ----

    def _offer(self, client: BrowserClient, message: LiveSummaryMessage | ResourceDelta) -> None:
        """queue 에 넣되, 가득 찬(느린) client 는 밀린 것을 버리고 snapshot 으로 복구."""
        try:
            client.queue.put_nowait(message)
        except asyncio.QueueFull:
            client.dropped_messages += client.queue.qsize()
            while not client.queue.empty():
                client.queue.get_nowait()
            try:
                client.queue.put_nowait(self.snapshot_for(client.subscription))
            except RealtimeSnapshotLimitError:
                client.queue.put_nowait(ResyncRequiredMessage())


def _summary_matches(subscription: Subscription, cluster_id: str) -> bool:
    """summary 는 cluster 단위 정보 — cluster 필터만 적용함."""
    return subscription.cluster_id in ("", cluster_id)


def _delta_matches(subscription: Subscription, key: str, value: dict | None) -> bool:
    cluster, namespace, _kind, _name = delta_key_parts(key)
    if subscription.cluster_id and subscription.cluster_id != cluster:
        return False
    if subscription.namespace and subscription.namespace != namespace:
        return False
    if subscription.app:
        app = (value or {}).get("app", "")
        if app and app != subscription.app:
            return False
    return True
