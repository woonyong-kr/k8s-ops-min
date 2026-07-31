import { useEffect, useMemo, useState } from "react";

import { getPhysicalTopology } from "../api/physical-topology";
import type { PhysicalTopologyEndpoint } from "../api/physical-topology-schemas";
import { useDevpreviewContracts } from "./contracts";
import { useLiveStreamView, type LiveStreamViewState } from "./liveStreamFeed";

// UI-PHASE2-001: 물리 토폴로지(노드·파드) 전용 라이브 어댑터.
// 정본 `GET /api/topology?view=physical&clusters=<exact-one>`만 읽는다.
// 노드 측정값과 파드 귀속은 응답의 server/server_id 증거만 사용하고 추정하지 않는다.

export type TopologyStatus = "loading" | "ready" | "unavailable";
export type TopologyCompleteness = "exact" | "partial" | "unavailable";

export interface InvNode {
  name: string;
  status: string;
  health: string;
  cluster: string;
  key: string;
  cpuPercent: number | null;
  memoryPercent: number | null;
  matchedPodCount: number | null;
  totalPodCount: number | null;
  restartsRecent?: number;
  conditions?: string[];
}

export interface InvPod {
  name: string;
  namespace: string | null;
  status: string;
  health: string;
  cluster: string;
  key: string;
  serverId: string | null;
  cpuMillicores: number | null;
  cpuRequestMillicores: number | null;
  cpuLimitMillicores: number | null;
  memoryMebibytes: number | null;
  memoryRequestMebibytes: number | null;
  memoryLimitMebibytes: number | null;
  restartCount: number;
}

export interface ClusterTopologyView {
  status: TopologyStatus;
  nodes: InvNode[];
  pods: InvPod[];
  /** 서버 배열에 실제로 반환된 Ready 노드 수. */
  nodesReady: number | null;
  /** 서버 배열에 실제로 반환된 노드 수. partial이면 화면에 일부 관측을 함께 표기한다. */
  nodesTotal: number | null;
  /** 정본 토폴로지가 실제로 반환해 노드/파드 화면에 표시할 수 있는 파드 수. */
  podsTotal: number | null;
  nodeCompleteness: TopologyCompleteness;
  podCompleteness: TopologyCompleteness;
  returnedPodCount: number;
  truncatedPodCount: number;
  partial: boolean;
  stale: boolean;
  partialReasonCodes: string[];
}

type TopologyListener = (view: ClusterTopologyView) => void;
interface TopologyChannel {
  controller: AbortController | null;
  listeners: Set<TopologyListener>;
  pendingInvalidation: boolean;
  refreshTimer: number | null;
  disposeTimer: number | null;
  visibilityCleanup: (() => void) | null;
}

// Physical topology is a database-heavy snapshot projection. Its observed
// p95 can exceed 25 s on the live management cluster, so a shorter poll period
// overlaps queries and recreates lock pressure. Live websocket deltas animate
// independently; this snapshot is the one-minute reconciliation safety net.
export const PHYSICAL_TOPOLOGY_RECONCILE_MS = 60_000;
const CACHE_TTL_MS = PHYSICAL_TOPOLOGY_RECONCILE_MS;
const PARTIAL_CACHE_TTL_MS = 60_000;
const ERROR_CACHE_TTL_MS = 30_000;
const STRICT_MODE_GRACE_MS = 50;
const EMPTY_READY: ClusterTopologyView = {
  status: "ready",
  nodes: [],
  pods: [],
  nodesReady: 0,
  nodesTotal: 0,
  podsTotal: 0,
  nodeCompleteness: "exact",
  podCompleteness: "exact",
  returnedPodCount: 0,
  truncatedPodCount: 0,
  partial: false,
  stale: false,
  partialReasonCodes: [],
};

const LOADING: ClusterTopologyView = {
  ...EMPTY_READY,
  status: "loading",
  nodesReady: null,
  nodesTotal: null,
  podsTotal: null,
  nodeCompleteness: "unavailable",
  podCompleteness: "unavailable",
};

const UNAVAILABLE: ClusterTopologyView = {
  ...LOADING,
  status: "unavailable",
};

const cache = new Map<string, { view: ClusterTopologyView; expiresAt: number }>();
const channels = new Map<string, TopologyChannel>();

export function podsForNode(pods: readonly InvPod[], nodeId: string): InvPod[] {
  return pods.filter((pod) => pod.serverId === nodeId);
}

export function toClusterTopologyView(topology: PhysicalTopologyEndpoint): ClusterTopologyView {
  const truncatedPodCount = Object.values(topology.truncated)
    .reduce((total, count) => total + count, topology.unassigned_truncated_count);
  const reasonCodes = Array.from(new Set([
    ...topology.partial_reason_codes,
    ...topology.snapshot.partial_reason_codes,
  ])).sort();
  const stale = topology.snapshot.stale;
  const podCompleteness = topology.projection_completeness;
  const partial = topology.projection_completeness !== "exact"
    || truncatedPodCount > 0
    || stale
    || reasonCodes.length > 0;

  return {
    status: "ready",
    nodes: topology.servers.map((server) => ({
      name: server.name,
      status: server.status,
      health: server.status,
      cluster: topology.cluster.cluster_id,
      key: server.id,
      cpuPercent: server.cpu_pct,
      memoryPercent: server.mem_pct,
      matchedPodCount: server.matched_pod_count,
      totalPodCount: server.total_pod_count,
      restartsRecent: 0,
      conditions: [],
    })),
    pods: topology.pods.map((pod) => ({
      name: pod.name,
      namespace: pod.namespace,
      status: pod.phase,
      health: pod.health,
      cluster: topology.cluster.cluster_id,
      key: pod.id,
      serverId: pod.server_id,
      cpuMillicores: pod.cpu_mcores,
      cpuRequestMillicores: pod.cpu_request_mcores,
      cpuLimitMillicores: pod.cpu_limit_mcores,
      memoryMebibytes: pod.mem_mib,
      memoryRequestMebibytes: pod.mem_request_mib,
      memoryLimitMebibytes: pod.mem_limit_mib,
      restartCount: pod.restarts,
    })),
    nodesReady: topology.servers.filter((server) => server.status.toLowerCase() === "ready").length,
    nodesTotal: topology.servers.length,
    // counts.*는 현재 리소스 필터 전체 개수이며 Pod 전용 개수가 아니다. 카드와
    // 실제 드릴이 모두 같은 pods 배열 길이를 표시하고, 생략분은 별도 표기한다.
    podsTotal: topology.pods.length,
    nodeCompleteness: topology.projection_completeness,
    podCompleteness,
    returnedPodCount: topology.pods.length,
    truncatedPodCount,
    partial,
    stale,
    partialReasonCodes: reasonCodes,
  };
}

function currentCached(clusterId: string): ClusterTopologyView | undefined {
  const entry = cache.get(clusterId);
  if (entry === undefined) return undefined;
  if (entry.expiresAt > Date.now()) return entry.view;
  cache.delete(clusterId);
  return undefined;
}

function subscribeClusterTopology(clusterId: string, listener: TopologyListener): () => void {
  let channel = channels.get(clusterId);
  if (channel === undefined) {
    channel = {
      controller: null,
      listeners: new Set(),
      pendingInvalidation: false,
      refreshTimer: null,
      disposeTimer: null,
      visibilityCleanup: null,
    };
    channels.set(clusterId, channel);
    const active = channel;
    const onVisibilityChange = () => {
      if (document.visibilityState === "hidden") {
        if (active.refreshTimer !== null) {
          window.clearTimeout(active.refreshTimer);
          active.refreshTimer = null;
        }
        active.controller?.abort();
        return;
      }
      if (active.listeners.size === 0 || channels.get(clusterId) !== active) return;
      const cached = cache.get(clusterId);
      if (
        cached !== undefined
        && cached.expiresAt > Date.now()
        && !active.pendingInvalidation
      ) {
        scheduleClusterRefresh(clusterId, active, cached.expiresAt - Date.now() + 25);
      } else {
        cache.delete(clusterId);
        active.pendingInvalidation = false;
        loadClusterTopology(clusterId, active);
      }
    };
    document.addEventListener("visibilitychange", onVisibilityChange);
    channel.visibilityCleanup = () => document.removeEventListener("visibilitychange", onVisibilityChange);
  }
  if (channel.disposeTimer !== null) {
    window.clearTimeout(channel.disposeTimer);
    channel.disposeTimer = null;
  }
  channel.listeners.add(listener);
  const entry = cache.get(clusterId);
  if (entry !== undefined && entry.expiresAt > Date.now()) {
    scheduleClusterRefresh(clusterId, channel, entry.expiresAt - Date.now() + 25);
  } else {
    cache.delete(clusterId);
    loadClusterTopology(clusterId, channel);
  }

  const active = channel;
  return () => {
    active.listeners.delete(listener);
    if (active.listeners.size > 0 || channels.get(clusterId) !== active) return;
    if (active.refreshTimer !== null) {
      window.clearTimeout(active.refreshTimer);
      active.refreshTimer = null;
    }
    // StrictMode disposes and re-subscribes once during development. Keep the
    // in-flight request briefly so the second mount cannot duplicate it.
    active.disposeTimer = window.setTimeout(() => {
      active.disposeTimer = null;
      if (active.listeners.size > 0 || channels.get(clusterId) !== active) return;
      active.controller?.abort();
      active.visibilityCleanup?.();
      channels.delete(clusterId);
    }, STRICT_MODE_GRACE_MS);
  };
}

function loadClusterTopology(clusterId: string, channel: TopologyChannel): void {
  if (
    channel.controller !== null
    || channel.listeners.size === 0
    || document.visibilityState === "hidden"
  ) return;
  const controller = new AbortController();
  channel.controller = controller;
  channel.pendingInvalidation = false;
  void getPhysicalTopology({ clusters: [clusterId] }, controller.signal)
    .then((topology) => {
      if (
        controller.signal.aborted
        || channels.get(clusterId) !== channel
      ) return;
      const view = toClusterTopologyView(topology);
      const ttl = view.partial ? PARTIAL_CACHE_TTL_MS : CACHE_TTL_MS;
      cache.set(clusterId, { view, expiresAt: Date.now() + ttl });
      channel.listeners.forEach((notify) => notify(view));
      // A live delta may arrive while this database-heavy snapshot is in
      // flight. The completed response is still observed evidence and is
      // useful immediately; publish it, then let `finally` perform one
      // trailing refresh instead of keeping the UI blank indefinitely.
      if (!channel.pendingInvalidation) scheduleClusterRefresh(clusterId, channel, ttl);
    })
    .catch((cause: unknown) => {
      if (
        isAbortError(cause)
        || controller.signal.aborted
        || channels.get(clusterId) !== channel
      ) return;
      cache.set(clusterId, { view: UNAVAILABLE, expiresAt: Date.now() + ERROR_CACHE_TTL_MS });
      channel.listeners.forEach((notify) => notify(UNAVAILABLE));
      scheduleClusterRefresh(clusterId, channel, ERROR_CACHE_TTL_MS);
    })
    .finally(() => {
      if (channel.controller === controller) channel.controller = null;
      if (channel.pendingInvalidation) {
        if (
          channel.listeners.size > 0
          && channels.get(clusterId) === channel
          && document.visibilityState !== "hidden"
        ) {
          channel.pendingInvalidation = false;
          loadClusterTopology(clusterId, channel);
        }
      }
    });
}

function scheduleClusterRefresh(
  clusterId: string,
  channel: TopologyChannel,
  delayMs: number,
): void {
  if (
    channel.listeners.size === 0
    || channels.get(clusterId) !== channel
    || document.visibilityState === "hidden"
  ) return;
  if (channel.refreshTimer !== null) window.clearTimeout(channel.refreshTimer);
  channel.refreshTimer = window.setTimeout(() => {
    channel.refreshTimer = null;
    if (channel.listeners.size === 0 || channels.get(clusterId) !== channel) return;
    cache.delete(clusterId);
    loadClusterTopology(clusterId, channel);
  }, Math.max(250, delayMs));
}

/**
 * 실시간 델타가 알려 준 한 클러스터만 다시 검증한다. 기존 화면 값은 React state에
 * 남겨 두고 캐시/다음 예약만 무효화하므로 새 응답 전까지 빈 화면으로 바뀌지 않는다.
 * 진행 중 요청은 중복하거나 폐기하지 않는다. 이미 완료된 관측 응답을 먼저 게시하고,
 * 요청 중 누적된 무효화는 완료 직후 한 번의 후속 조회로 합친다. 백엔드 응답 시간이
 * 실시간 신호 간격보다 길어도 화면이 영원히 loading에 머무르지 않는다.
 */
function invalidateClusterTopology(clusterId: string): void {
  const hadPublishedView = cache.has(clusterId);
  const channel = channels.get(clusterId);
  // The initial canonical request is already reading the latest projection.
  // A WS frame arriving before that first response must not guarantee a second
  // back-to-back heavy query. The regular one-minute reconciliation covers any
  // race after the first observed response is published.
  if (channel !== undefined && channel.controller !== null && !hadPublishedView) return;
  cache.delete(clusterId);
  if (channel === undefined) return;
  channel.pendingInvalidation = true;
  if (channel.refreshTimer !== null) {
    window.clearTimeout(channel.refreshTimer);
    channel.refreshTimer = null;
  }
  if (channel.controller !== null) return;
  channel.pendingInvalidation = false;
  loadClusterTopology(clusterId, channel);
}

/** @internal 실시간 무효화 경쟁 조건 회귀 테스트용 진입점. */
export function invalidateClusterTopologyForTests(clusterId: string): void {
  invalidateClusterTopology(clusterId);
}

/**
 * 여러 클러스터의 정본 물리 토폴로지를 동시에 읽는다. 모듈 캐시와 진행 중 요청을
 * 공유하므로 홈 카드와 노드 드릴이 같은 클러스터를 구독해도 네트워크 요청은 하나다.
 * 범위가 바뀌면 더 이상 구독자가 없는 요청만 abort한다.
 */
export function useClusterTopologies(
  clusterIds: readonly string[],
): Record<string, ClusterTopologyView> {
  const { workspaceId } = useDevpreviewContracts();
  const key = Array.from(new Set(clusterIds)).sort().join("\u0000");
  const ids = useMemo(() => (key ? key.split("\u0000") : []), [key]);
  const [views, setViews] = useState<Record<string, ClusterTopologyView>>(() => initialViews(ids));
  const liveClusterId = ids.length === 1 ? ids[0] : null;
  const liveSubscription = useMemo(() => (
    workspaceId === null || liveClusterId === null
      ? null
      : { workspaceId, clusterId: liveClusterId }
  ), [liveClusterId, workspaceId]);
  const live = useLiveStreamView(liveSubscription);

  useEffect(() => {
    const unsubscribes = ids.map((id) => subscribeClusterTopology(id, (view) => {
      setViews((previous) => previous[id] === view
        ? previous
        : { ...previous, [id]: view });
    }));
    return () => unsubscribes.forEach((unsubscribe) => unsubscribe());
  }, [ids]);

  return Object.fromEntries(ids.map((id) => [
    id,
    applyLiveResourcesToTopologyView(
      currentCached(id) ?? views[id] ?? LOADING,
      id,
      liveClusterId === id ? live : null,
    ),
  ]));
}

/**
 * Overlays only directly observed pod fields onto the cached topology shell.
 * Membership/identity and absent values stay on the canonical REST snapshot;
 * no live request-ratio is converted into a node CPU or memory percentage.
 */
export function applyLiveResourcesToTopologyView(
  current: ClusterTopologyView,
  clusterId: string,
  live: LiveStreamViewState | null,
): ClusterTopologyView {
  if (live === null || current.status !== "ready") return current;
  if (!live.observed) return live.stale ? { ...current, stale: true } : current;
  const measurements = new Map<string, Record<string, unknown>>();
  const podCountsByNode = new Map<string, number>();
  let observedClusterPod = false;
  for (const [key, value] of Object.entries(live.resources)) {
    const identity = livePodIdentity(key, clusterId);
    if (identity === null || !isRecord(value)) continue;
    observedClusterPod = true;
    measurements.set(`${identity.namespace}\u0000${identity.name}`, value);
    if (typeof value.node === "string" && value.node !== "") {
      podCountsByNode.set(value.node, (podCountsByNode.get(value.node) ?? 0) + 1);
    }
  }
  const authoritativeEmpty = live.summaries[clusterId]?.pods_total === 0;
  const hasPodProjection = observedClusterPod || authoritativeEmpty;
  return {
    ...current,
    stale: current.stale || live.stale,
    nodes: hasPodProjection
      ? current.nodes.map((node) => ({
          ...node,
          matchedPodCount: podCountsByNode.get(node.name) ?? 0,
        }))
      : current.nodes,
    pods: current.pods.map((pod) => {
      const value = measurements.get(`${pod.namespace ?? ""}\u0000${pod.name}`);
      if (value === undefined) return pod;
      return {
        ...pod,
        cpuMillicores: optionalFiniteMetric(value, "cpu_mcores", pod.cpuMillicores),
        memoryMebibytes: optionalFiniteMetric(value, "mem_mib", pod.memoryMebibytes),
        restartCount: optionalNonNegativeInteger(value, "restarts", pod.restartCount),
        status: optionalString(value, "phase", pod.status),
        health: optionalString(value, "health", pod.health),
      };
    }),
  };
}

function livePodIdentity(
  key: string,
  clusterId: string,
): { namespace: string; name: string } | null {
  const segments = key.split("/");
  if (
    segments.length !== 4
    || segments[0] !== clusterId
    || segments[2]?.toLowerCase() !== "pod"
  ) return null;
  const namespace = segments[1];
  const name = segments[3];
  return namespace && name ? { namespace, name } : null;
}

function optionalFiniteMetric(
  value: Record<string, unknown>,
  key: string,
  fallback: number | null,
): number | null {
  if (!Object.prototype.hasOwnProperty.call(value, key)) return fallback;
  const metric = value[key];
  return metric === null || (typeof metric === "number" && Number.isFinite(metric) && metric >= 0)
    ? metric
    : fallback;
}

function optionalNonNegativeInteger(
  value: Record<string, unknown>,
  key: string,
  fallback: number,
): number {
  if (!Object.prototype.hasOwnProperty.call(value, key)) return fallback;
  const metric = value[key];
  return typeof metric === "number" && Number.isSafeInteger(metric) && metric >= 0
    ? metric
    : fallback;
}

function optionalString(
  value: Record<string, unknown>,
  key: string,
  fallback: string,
): string {
  const observed = value[key];
  return typeof observed === "string" && observed !== "" ? observed : fallback;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/** 한 클러스터 드릴용 기존 API. 다중 구독 훅과 같은 cache/request를 재사용한다. */
export function useClusterTopology(clusterId: string | null): ClusterTopologyView {
  const clusterIds = useMemo(() => (clusterId === null ? [] : [clusterId]), [clusterId]);
  const views = useClusterTopologies(clusterIds);
  if (clusterId === null) return EMPTY_READY;
  return views[clusterId] ?? LOADING;
}

function initialViews(ids: readonly string[]): Record<string, ClusterTopologyView> {
  return Object.fromEntries(ids.map((id) => [id, currentCached(id) ?? LOADING]));
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error
    && (error as { name?: unknown }).name === "AbortError";
}

/** @internal 테스트 격리를 위한 캐시 초기화. */
export function resetClusterTopologyCacheForTests(): void {
  channels.forEach((channel) => {
    channel.controller?.abort();
    if (channel.refreshTimer !== null) window.clearTimeout(channel.refreshTimer);
    if (channel.disposeTimer !== null) window.clearTimeout(channel.disposeTimer);
    channel.visibilityCleanup?.();
  });
  channels.clear();
  cache.clear();
}
