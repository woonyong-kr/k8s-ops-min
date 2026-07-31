// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { FleetSummary } from "../api/schemas";

const getFleetSummary = vi.fn();
let streamLive = false;
let deliverStreamSummary: ((summary: FleetSummary) => void) | null = null;
let streamScopeKey = "";

vi.mock("../api/fleet", () => ({
  getFleetSummary: (...args: unknown[]) => getFleetSummary(...args),
}));

vi.mock("./fleetSummaryStream", () => ({
  useFleetSummaryStream: (
    scopeKey: string,
    onSummary: (summary: FleetSummary) => void,
  ) => {
    streamScopeKey = scopeKey;
    deliverStreamSummary = onSummary;
    return { live: streamLive };
  },
}));

import { useFleetSummaries, useFleetSummaryFeed } from "./fleetSummaryFeed";

const emptyFleet: FleetSummary = {
  clusters: [],
  totals: {
    clusters: 0,
    healthy: 0,
    warning: 0,
    critical: 0,
    stale: 0,
    unknown: 0,
    open_incidents: 0,
    pending_approvals: 0,
    running_workflows: 0,
    dead_letters: 0,
  },
};

const pushedFleet: FleetSummary = {
  clusters: [{
    cluster_id: "cluster-a",
    name: "Cluster A",
    health: "healthy",
    pods_running: 4,
    pods_total: 5,
    nodes_ready: 2,
    nodes_total: 2,
    open_incidents: 1,
    restarts_recent: 3,
    cpu_pct: 42,
    mem_pct: 51,
    last_seen_at: "2026-07-24T04:00:00Z",
  }],
  totals: {
    clusters: 1,
    healthy: 1,
    warning: 0,
    critical: 0,
    stale: 0,
    unknown: 0,
    open_incidents: 1,
    pending_approvals: 0,
    running_workflows: 0,
    dead_letters: 0,
  },
};

afterEach(() => {
  getFleetSummary.mockReset();
  streamLive = false;
  deliverStreamSummary = null;
  streamScopeKey = "";
});

describe("useFleetSummaries push and fallback", () => {
  it("uses the complete pushed payload without issuing an HTTP read while live", () => {
    streamLive = true;
    const { result } = renderHook(
      () => useFleetSummaries("workspace-a", ["cluster-a"]),
    );

    expect(getFleetSummary).not.toHaveBeenCalled();
    act(() => deliverStreamSummary?.(pushedFleet));

    expect(result.current["cluster-a"]).toEqual(expect.objectContaining({
      health: "healthy",
      cpuPct: 42,
      memPct: 51,
      podsRunning: 4,
      podsTotal: 5,
      nodesReady: 2,
      nodesTotal: 2,
      openIncidents: 1,
      restartDelta: 3,
    }));
  });

  it("starts the bounded HTTP fallback immediately when the stream is unavailable", async () => {
    getFleetSummary.mockResolvedValue(emptyFleet);
    renderHook(() => useFleetSummaries("workspace-a", ["cluster-a"]));

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(getFleetSummary).toHaveBeenCalledTimes(1);
  });

  it("does not let an older HTTP fallback overwrite a newer pushed payload", async () => {
    let resolveFallback: ((value: FleetSummary) => void) | null = null;
    let fallbackSignal: AbortSignal | undefined;
    getFleetSummary.mockImplementation((signal?: AbortSignal) => {
      fallbackSignal = signal;
      return new Promise<FleetSummary>((resolve) => {
        resolveFallback = resolve;
      });
    });
    const { result, rerender } = renderHook(
      () => useFleetSummaries("workspace-a", ["cluster-a"]),
    );

    await act(async () => {
      await Promise.resolve();
    });
    expect(getFleetSummary).toHaveBeenCalledTimes(1);

    act(() => deliverStreamSummary?.(pushedFleet));
    streamLive = true;
    rerender();

    expect(fallbackSignal?.aborted).toBe(true);
    await act(async () => {
      resolveFallback?.(emptyFleet);
      await Promise.resolve();
    });
    expect(result.current["cluster-a"]?.cpuPct).toBe(42);
  });

  it("reloads the HTTP fallback immediately when its cluster scope changes", async () => {
    getFleetSummary.mockResolvedValue(emptyFleet);
    const { rerender } = renderHook(
      ({ clusterIds }) => useFleetSummaries("workspace-a", clusterIds),
      { initialProps: { clusterIds: ["cluster-a"] as readonly string[] } },
    );

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(getFleetSummary).toHaveBeenCalledTimes(1);

    rerender({ clusterIds: ["cluster-b"] });
    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(getFleetSummary).toHaveBeenCalledTimes(2);
  });

  it("keeps the workspace feed active when there are no clusters yet", async () => {
    getFleetSummary.mockResolvedValue(emptyFleet);
    renderHook(() => useFleetSummaries("workspace-empty", []));

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });

    expect(streamScopeKey).toBe("fleet:workspace-empty");
    expect(getFleetSummary).toHaveBeenCalledTimes(1);
  });

  it("does not expose the previous workspace payload during an authorization scope change", () => {
    streamLive = true;
    const { result, rerender } = renderHook(
      ({ workspaceId }) => useFleetSummaryFeed(workspaceId, ["cluster-a"]),
      { initialProps: { workspaceId: "workspace-a" as string | null } },
    );
    act(() => deliverStreamSummary?.(pushedFleet));
    expect(result.current.clusters["cluster-a"]?.cpuPct).toBe(42);

    rerender({ workspaceId: "workspace-b" });

    expect(result.current.status).toBe("loading");
    expect(result.current.clusters).toEqual({});
    expect(result.current.totals).toBeNull();
  });
});
