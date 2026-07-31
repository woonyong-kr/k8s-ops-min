import { useMemo, useState } from "react";

import { getClusterNodesSummary } from "../api/cluster-summary";
import type { ClusterNodesSummary } from "../api/cluster-summary-schemas";
import {
  liveClusterResourceObservationSchema,
  type LiveClusterResourceObservation,
} from "../api/live-schemas";
import { useDevpreviewContracts } from "./contracts";
import { useLiveStreamView, type LiveStreamViewState } from "./liveStreamFeed";
import { useBoundedPoll } from "./useBoundedPoll";

// WebSocket이 실측 cluster_metrics를 보내는 동안 REST는 정합성 확인용이다. 소켓이
// 없거나 재연결 중일 때만 5초 fallback을 사용하고, 정상 관측 중에는 60초 reconcile로
// 낮춘다. 화면 숨김·in-flight dedupe·abort는 useBoundedPoll이 보장한다.
export const CLUSTER_SUMMARY_FALLBACK_MS = 5_000;
export const CLUSTER_SUMMARY_RECONCILE_MS = 60_000;

// UI-PHASE2-001 §5.2: a typed live adapter for the Home/cluster cards. Node
// readiness and usage come from each cluster's canonical node-summary contract.
// Missing CPU/MEM (`cpu_pct`/`mem_pct` null) stays null — an honest "not
// observed" — and is never backfilled with a generated value.

export type ClusterSummaryStatus = "loading" | "ready" | "unavailable";

export interface ClusterNodeSummaryView {
  name: string;
  ready: boolean;
  health: string;
  cpuPct: number | null;
  memPct: number | null;
  podsRunning: number;
  podsCapacity: number;
  restartsRecent: number;
  conditions: string[];
}

export interface ClusterSummaryView {
  status: ClusterSummaryStatus;
  health: string | null;
  cpuPct: number | null;
  memPct: number | null;
  podsRunning: number | null;
  podsTotal: number | null;
  nodesReady: number | null;
  nodesTotal: number | null;
  openIncidents: number | null;
  nodes: ClusterNodeSummaryView[];
  /** Live transport freshness; last-known-good values remain visible when true. */
  stale?: boolean;
  /** Real restart change reported by the latest live summary window. */
  restartDelta?: number | null;
}

const UNAVAILABLE: ClusterSummaryView = {
  status: "unavailable",
  health: null,
  cpuPct: null,
  memPct: null,
  podsRunning: null,
  podsTotal: null,
  nodesReady: null,
  nodesTotal: null,
  openIncidents: null,
  nodes: [],
};

export function toClusterSummaryView(detail: ClusterNodesSummary): ClusterSummaryView {
  return {
    status: "ready",
    health: null,
    cpuPct: observedMean(detail.nodes.map((node) => node.cpu_pct)),
    memPct: observedMean(detail.nodes.map((node) => node.mem_pct)),
    podsRunning: detail.nodes.reduce((total, node) => total + node.pods_running, 0),
    // Node summary exposes running pods and capacity, not a cluster-wide current
    // pod total. Keep the absent fact null instead of substituting capacity.
    podsTotal: null,
    nodesReady: detail.nodes.filter((node) => node.ready).length,
    nodesTotal: detail.nodes.length,
    openIncidents: null,
    nodes: detail.nodes.map((node) => ({
      name: node.name,
      ready: node.ready,
      health: node.health,
      cpuPct: node.cpu_pct,
      memPct: node.mem_pct,
      podsRunning: node.pods_running,
      podsCapacity: node.pods_capacity,
      restartsRecent: node.restarts_recent,
      conditions: node.conditions,
    })),
  };
}

function observedMean(values: readonly (number | null)[]): number | null {
  const observed = values.filter((value): value is number => value !== null);
  if (observed.length === 0) return null;
  const mean = observed.reduce((total, value) => total + value, 0) / observed.length;
  return Math.round(mean * 10) / 10;
}

/**
 * Reads the canonical node-summary contract for every visible card. Independent
 * requests start together (no waterfall), share one abort scope, and keep their
 * previous values while bounded polling refreshes them.
 */
export function useClusterSummaries(
  clusterIds: readonly string[],
): Record<string, ClusterSummaryView> {
  const [summaries, setSummaries] = useState<Record<string, ClusterSummaryView>>({});
  const { workspaceId } = useDevpreviewContracts();
  const key = Array.from(new Set(clusterIds)).sort().join("\u0000");
  const ids = useMemo(() => key ? key.split("\u0000") : [], [key]);
  const liveSubscription = useMemo(() => {
    // The authenticated browser contract requires one exact cluster and the
    // performance budget permits one active socket. Multi-cluster overview
    // cards therefore retain bounded REST polling; the selected dashboard/drill
    // receives the 1 Hz direct model.
    if (workspaceId === null || ids.length !== 1) return null;
    return { workspaceId, clusterId: ids[0] };
  }, [ids, workspaceId]);
  const live = useLiveStreamView(liveSubscription);
  const refreshMs = liveSubscription !== null
    && live.status === "connected"
    && live.observed
    && !live.stale
    ? CLUSTER_SUMMARY_RECONCILE_MS
    : CLUSTER_SUMMARY_FALLBACK_MS;
  // 공통 bounded-poll: 화면이 보일 때만 visible cluster의 노드 요약을 병렬 조회한다.
  // in-flight dedupe·backpressure로 중복 요청 0, 스코프 변경 시 abort로 stale overwrite 0.
  // 재조회 중 직전 요약 값은 유지하고 한 번의 setSummaries로 전체를 commit한다.
  useBoundedPoll({
    scopeKey: key,
    intervalMs: refreshMs,
    load: (signal) => Promise.all(ids.map(async (id) => {
      try {
        const detail = await getClusterNodesSummary(id, signal);
        return [id, toClusterSummaryView(detail)] as const;
      } catch (cause: unknown) {
        if (signal.aborted || isAbortError(cause)) throw cause;
        return [id, UNAVAILABLE] as const;
      }
    })),
    onResult: (entries) => setSummaries(Object.fromEntries(entries)),
    // 최초 실패만 unavailable로 표시하고, 이후 실패는 직전 정상 값을 유지한다.
    onError: () => setSummaries((previous) => Object.fromEntries(
      ids.map((id) => [id, previous[id] ?? UNAVAILABLE]),
    )),
  });
  return useMemo(
    () => applyLiveClusterSummaries(summaries, ids, live),
    [ids, live, summaries],
  );
}

/**
 * Applies only facts present in the retained live protocol model. In
 * particular, pod request ratios are never relabelled as node CPU/MEM, and
 * pods_ready is never relabelled as pods_running.
 */
export function applyLiveClusterSummaries(
  current: Readonly<Record<string, ClusterSummaryView>>,
  clusterIds: readonly string[],
  live: LiveStreamViewState,
): Record<string, ClusterSummaryView> {
  if (!live.observed) {
    if (!live.stale) return current as Record<string, ClusterSummaryView>;
    return Object.fromEntries(Object.entries(current).map(([clusterId, summary]) => [
      clusterId,
      clusterIds.includes(clusterId) ? { ...summary, stale: true } : summary,
    ]));
  }
  const podFacts = livePodFacts(live.resources, clusterIds);
  const metricFacts = liveClusterResourceFacts(live.resources, clusterIds);
  let changed = false;
  const next = { ...current };

  for (const clusterId of clusterIds) {
    const liveSummary = live.summaries[clusterId];
    const metrics = metricFacts.get(clusterId);
    const currentSummary = current[clusterId];
    if (currentSummary === undefined && liveSummary === undefined && metrics === undefined) continue;
    const summary = currentSummary?.status === "ready"
      ? currentSummary
      : emptyObservedSummary(metrics);
    // A newly connected or partially projected stream can emit a zero summary
    // before any pod identities arrive. That is not authoritative evidence that
    // the cluster is empty: replacing the REST node summary here made real
    // 22/58 and 30/58 occupancy render as 0/58. Only resource.delta pod
    // identities are precise enough to redistribute occupancy per node.
    const candidateFacts = podFacts.get(clusterId);
    // resource.delta is a retained delta set, not necessarily a complete pod
    // inventory. It is authoritative only when its identity count matches the
    // summary total from the same retained model.
    const facts = candidateFacts !== undefined
      && liveSummary !== undefined
      && candidateFacts.observed === liveSummary.pods_total
      ? candidateFacts
      : undefined;
    const nodes = mergeLiveNodes(summary.nodes, metrics, facts);
    const metricsFresh = metrics !== undefined && !metrics.stale;
    const statusFresh = metrics !== undefined && !metrics.status_stale;
    const projected: ClusterSummaryView = {
      ...summary,
      status: summary.status === "unavailable" && metrics === undefined && liveSummary === undefined
        ? "unavailable"
        : "ready",
      health: statusFresh ? metrics.status : summary.health,
      cpuPct: metricsFresh && metrics.cpu_pct !== null ? metrics.cpu_pct : summary.cpuPct,
      memPct: metricsFresh && metrics.mem_pct !== null ? metrics.mem_pct : summary.memPct,
      podsRunning: facts?.running ?? summary.podsRunning,
      podsTotal: shouldRetainRestPodTotal(summary, liveSummary?.pods_total, facts)
        ? summary.podsTotal
        : liveSummary?.pods_total ?? summary.podsTotal,
      nodesReady: statusFresh && metrics.collection_complete
        ? metrics.nodes_ready
        : summary.nodesReady,
      nodesTotal: statusFresh && metrics.collection_complete
        ? metrics.nodes_total
        : summary.nodesTotal,
      nodes,
      stale: live.stale || metrics?.stale === true || metrics?.status_stale === true,
      restartDelta: liveSummary?.restart_delta ?? summary.restartDelta ?? null,
    };
    next[clusterId] = projected;
    changed = true;
  }

  return changed ? next : current as Record<string, ClusterSummaryView>;
}

function emptyObservedSummary(
  metrics: LiveClusterResourceObservation | undefined,
): ClusterSummaryView {
  return {
    status: metrics === undefined ? "unavailable" : "ready",
    health: metrics !== undefined && !metrics.status_stale ? metrics.status : null,
    cpuPct: metrics !== undefined && !metrics.stale ? metrics.cpu_pct : null,
    memPct: metrics !== undefined && !metrics.stale ? metrics.mem_pct : null,
    podsRunning: null,
    podsTotal: null,
    nodesReady: metrics?.collection_complete ? metrics.nodes_ready : null,
    nodesTotal: metrics?.collection_complete ? metrics.nodes_total : null,
    openIncidents: null,
    nodes: [],
    stale: metrics?.stale === true || metrics?.status_stale === true,
  };
}

function mergeLiveNodes(
  nodes: readonly ClusterNodeSummaryView[],
  metrics: LiveClusterResourceObservation | undefined,
  podFacts: LivePodFacts | undefined,
): ClusterNodeSummaryView[] {
  const metricsByName = new Map(metrics?.nodes.map((node) => [node.name, node]) ?? []);
  const restByName = new Map(nodes.map((node) => [node.name, node]));
  const names = new Set([...restByName.keys(), ...metricsByName.keys()]);
  return Array.from(names, (name) => {
    const rest = restByName.get(name);
    const liveNode = metricsByName.get(name);
    const metricFresh = liveNode !== undefined && !liveNode.stale;
    const statusFresh = liveNode !== undefined && !liveNode.status_stale;
    return {
      name,
      ready: statusFresh ? liveNode.status === "ready" : rest?.ready ?? false,
      health: statusFresh ? liveNode.status : rest?.health ?? "unknown",
      cpuPct: metricFresh && liveNode.cpu_pct !== null ? liveNode.cpu_pct : rest?.cpuPct ?? null,
      memPct: metricFresh && liveNode.mem_pct !== null ? liveNode.mem_pct : rest?.memPct ?? null,
      podsRunning: podFacts?.runningByNode.get(name) ?? rest?.podsRunning ?? 0,
      // The live cluster metric contract does not observe pod capacity,
      // restarts, or conditions. Preserve REST facts; never synthesize them.
      podsCapacity: rest?.podsCapacity ?? 0,
      restartsRecent: rest?.restartsRecent ?? 0,
      conditions: rest?.conditions ?? [],
    };
  });
}

export function liveClusterResourceFacts(
  resources: Readonly<Record<string, unknown>>,
  clusterIds: readonly string[],
): Map<string, LiveClusterResourceObservation> {
  const wanted = new Set(clusterIds);
  const result = new Map<string, LiveClusterResourceObservation>();
  for (const value of Object.values(resources)) {
    const parsed = liveClusterResourceObservationSchema.safeParse(value);
    if (!parsed.success || !wanted.has(parsed.data.cluster_id)) continue;
    result.set(parsed.data.cluster_id, parsed.data);
  }
  return result;
}

function shouldRetainRestPodTotal(
  summary: ClusterSummaryView,
  livePodsTotal: number | undefined,
  facts: LivePodFacts | undefined,
): boolean {
  return facts === undefined
    && livePodsTotal === 0
    && (summary.podsRunning ?? 0) > 0;
}

interface LivePodFacts {
  observed: number;
  running: number;
  runningByNode: Map<string, number>;
}

function livePodFacts(
  resources: Readonly<Record<string, unknown>>,
  clusterIds: readonly string[],
): Map<string, LivePodFacts> {
  const wanted = new Set(clusterIds);
  const result = new Map<string, LivePodFacts>();
  for (const [key, value] of Object.entries(resources)) {
    const identity = livePodIdentity(key);
    if (identity === null || !wanted.has(identity.clusterId) || !isRecord(value)) continue;
    let facts = result.get(identity.clusterId);
    if (facts === undefined) {
      facts = { observed: 0, running: 0, runningByNode: new Map() };
      result.set(identity.clusterId, facts);
    }
    facts.observed += 1;
    if (typeof value.phase !== "string" || value.phase.toLowerCase() !== "running") continue;
    facts.running += 1;
    if (typeof value.node === "string" && value.node !== "") {
      facts.runningByNode.set(value.node, (facts.runningByNode.get(value.node) ?? 0) + 1);
    }
  }
  return result;
}

function livePodIdentity(key: string): { clusterId: string } | null {
  const segments = key.split("/");
  if (segments.length !== 4 || segments[2]?.toLowerCase() !== "pod") return null;
  const clusterId = segments[0];
  return clusterId ? { clusterId } : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isAbortError(cause: unknown): boolean {
  return typeof cause === "object" && cause !== null && "name" in cause
    && (cause as { name?: unknown }).name === "AbortError";
}
