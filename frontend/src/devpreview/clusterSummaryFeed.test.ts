import { describe, expect, it } from "vitest";

import type { LiveStreamViewState } from "./liveStreamFeed";
import {
  applyLiveClusterSummaries,
  CLUSTER_SUMMARY_FALLBACK_MS,
  CLUSTER_SUMMARY_RECONCILE_MS,
  liveClusterResourceFacts,
  type ClusterSummaryView,
} from "./clusterSummaryFeed";

const observedAt = "2026-07-24T02:33:18.481759+00:00";

function clusterMetrics(overrides: Record<string, unknown> = {}): Record<string, unknown> {
  return {
    resource_type: "cluster_metrics",
    kind: "ClusterMetrics",
    cluster_id: "cluster-a",
    name: "cluster-a",
    actual_interval_seconds: 1,
    collection_complete: true,
    status: "ready",
    cpu_mcores: 1_000,
    mem_mib: 2_048,
    cpu_capacity_mcores: 4_000,
    mem_capacity_mib: 8_192,
    cpu_pct: 25,
    mem_pct: 25,
    observed_at: observedAt,
    source: "kubelet_stats_summary",
    stale: false,
    degraded_reason: null,
    status_observed_at: observedAt,
    status_source: "kubernetes_api",
    status_stale: false,
    nodes_ready: 1,
    nodes_total: 1,
    nodes: [
      {
        name: "node-a",
        status: "ready",
        cpu_mcores: 1_000,
        mem_mib: 2_048,
        cpu_capacity_mcores: 4_000,
        mem_capacity_mib: 8_192,
        cpu_pct: 25,
        mem_pct: 25,
        observed_at: observedAt,
        source: "kubelet_stats_summary",
        stale: false,
        degraded_reason: null,
        status_observed_at: observedAt,
        status_source: "kubernetes_api",
        status_stale: false,
      },
    ],
    ...overrides,
  };
}

function live(value: unknown, overrides: Partial<LiveStreamViewState> = {}): LiveStreamViewState {
  return {
    status: "connected",
    observed: true,
    stale: false,
    resources: { "cluster-a/cluster/metrics/live": value },
    summaries: {},
    updatedAt: Date.now(),
    ...overrides,
  };
}

const restSummary: ClusterSummaryView = {
  status: "ready",
  health: null,
  cpuPct: 10,
  memPct: 11,
  podsRunning: 3,
  podsTotal: null,
  nodesReady: 1,
  nodesTotal: 2,
  openIncidents: null,
  nodes: [
    {
      name: "node-a",
      ready: false,
      health: "degraded",
      cpuPct: 10,
      memPct: 11,
      podsRunning: 3,
      podsCapacity: 58,
      restartsRecent: 2,
      conditions: ["MemoryPressure"],
    },
  ],
};

describe("cluster summary realtime projection", () => {
  it("projects measured cluster and node facts without dropping REST-only facts", () => {
    const result = applyLiveClusterSummaries(
      { "cluster-a": restSummary },
      ["cluster-a"],
      live(clusterMetrics()),
    )["cluster-a"];

    expect(result).toMatchObject({
      status: "ready",
      health: "ready",
      cpuPct: 25,
      memPct: 25,
      nodesReady: 1,
      nodesTotal: 1,
      stale: false,
    });
    expect(result?.nodes[0]).toEqual({
      name: "node-a",
      ready: true,
      health: "ready",
      cpuPct: 25,
      memPct: 25,
      podsRunning: 3,
      podsCapacity: 58,
      restartsRecent: 2,
      conditions: ["MemoryPressure"],
    });
  });

  it("retains last-known-good measurements when the next live observation is stale", () => {
    const stale = clusterMetrics({
      stale: true,
      status_stale: true,
      cpu_pct: null,
      mem_pct: null,
      observed_at: null,
      nodes: [
        {
          ...(clusterMetrics().nodes as Record<string, unknown>[])[0],
          stale: true,
          status_stale: true,
          cpu_pct: null,
          mem_pct: null,
          observed_at: null,
        },
      ],
    });
    const result = applyLiveClusterSummaries(
      { "cluster-a": restSummary },
      ["cluster-a"],
      live(stale),
    )["cluster-a"];

    expect(result).toMatchObject({ cpuPct: 10, memPct: 11, stale: true });
    expect(result?.nodes[0]).toMatchObject({
      ready: false,
      cpuPct: 10,
      memPct: 11,
    });
  });

  it("can render an honest cluster-level push result before REST succeeds", () => {
    const result = applyLiveClusterSummaries(
      {},
      ["cluster-a"],
      live(clusterMetrics()),
    )["cluster-a"];

    expect(result).toMatchObject({
      status: "ready",
      cpuPct: 25,
      memPct: 25,
      nodesReady: 1,
      nodesTotal: 1,
      podsRunning: null,
      podsTotal: null,
    });
    expect(result?.nodes).toHaveLength(1);
    expect(result?.nodes[0]).toMatchObject({
      name: "node-a",
      podsCapacity: 0,
      restartsRecent: 0,
      conditions: [],
    });
  });

  it("rejects malformed open resource values at the typed boundary", () => {
    expect(liveClusterResourceFacts(
      {
        "cluster-a/cluster/metrics/live": {
          ...clusterMetrics(),
          cpu_pct: 25,
          cpu_capacity_mcores: null,
        },
      },
      ["cluster-a"],
    ).size).toBe(0);
  });

  it("keeps fallback and reconcile cadences explicit and bounded", () => {
    expect(CLUSTER_SUMMARY_FALLBACK_MS).toBe(5_000);
    expect(CLUSTER_SUMMARY_RECONCILE_MS).toBe(60_000);
  });
});
