import { useCallback, useMemo, useState } from "react";

import { getClusterNodesSummary } from "../api/cluster-summary";
import { getFleetSummary } from "../api/fleet";
import type { FleetSummary, FleetTotals } from "../api/schemas";
import { isAbortError } from "../shared/data/asyncResourceState";
import { toClusterSummaryView, type ClusterSummaryView } from "./clusterSummaryFeed";
import { useFleetSummaryStream } from "./fleetSummaryStream";
import { useBoundedPoll } from "./useBoundedPoll";

// Fleet 롤업은 전 클러스터를 단일 요청으로 반환한다(클러스터당 1요청 아님).
// 홈 카드는 이 단일 원장을 소비해 드릴다운과 숫자가 어긋나지 않게 한다.
// 정상 경로는 workspace fleet SSE 가 완전한 payload를 직접 전달한다. HTTP GET은
// 스트림 연결 전/장애 시에만 5초 bounded fallback으로 동작한다.
const FLEET_REFRESH_MS = 5_000;
// fleet 호출 자체 타임아웃. 집계가 "빨리 실패"가 아니라 "매달리는" 장애(DB 타임아웃 등)일 때
// 폴링의 시도 타임아웃(8s)이 전체를 끊기 전에 fleet 만 끊고 클러스터별 폴백을 돌리기 위함.
const FLEET_CALL_TIMEOUT_MS = 3_500;

/**
 * Reads the server-computed fleet rollup and exposes it in the existing
 * ClusterSummaryView shape so the compact home cards render without change.
 *
 * 회복 규칙(빈 화면 금지):
 *  - fleet 응답 실패 시 클러스터별 nodes/summary 계약으로 폴백해 같은 화면 값을
 *    채운다(집계 API 장애가 카드 전체를 비우지 않는다).
 *  - 폴백까지 실패하면 에러를 던져(onError) 직전 정상 값을 그대로 유지한다 —
 *    성공 응답만 화면 상태를 교체할 수 있다(last-known-good).
 *
 * fleet does not carry per-node detail (slots) or namespace counts; those stay
 * null here and the card falls back to the values it already sources elsewhere
 * (cl.namespaceCount). Node/slot detail remains the per-cluster drill's job.
 */
export type FleetSummaryFeedStatus = "loading" | "ready" | "partial" | "unavailable";
export type FleetSummaryFeedTransport =
  | "connecting"
  | "sse"
  | "http"
  | "cluster-fallback";
export type FleetTotalsObservation = "loading" | "observed" | "stale" | "unavailable";

export interface FleetSummaryFeedView {
  status: FleetSummaryFeedStatus;
  transport: FleetSummaryFeedTransport;
  clusters: Record<string, ClusterSummaryView>;
  totals: FleetTotals | null;
  totalsObservation: FleetTotalsObservation;
}

type FleetLoadResult =
  | { source: "http"; fleet: FleetSummary }
  | { source: "cluster-fallback"; clusters: Record<string, ClusterSummaryView> };

const EMPTY_FLEET_VIEW: FleetSummaryFeedView = {
  status: "loading",
  transport: "connecting",
  clusters: {},
  totals: null,
  totalsObservation: "loading",
};

export function useFleetSummaryFeed(
  workspaceId: string | null,
  clusterIds: readonly string[],
): FleetSummaryFeedView {
  const [scopedView, setScopedView] = useState<{
    workspaceId: string | null;
    view: FleetSummaryFeedView;
  }>({ workspaceId, view: EMPTY_FLEET_VIEW });
  const view = scopedView.workspaceId === workspaceId
    ? scopedView.view
    : EMPTY_FLEET_VIEW;
  const setView = useCallback((
    update: FleetSummaryFeedView
      | ((current: FleetSummaryFeedView) => FleetSummaryFeedView),
  ) => {
    setScopedView((current) => {
      const currentView = current.workspaceId === workspaceId
        ? current.view
        : EMPTY_FLEET_VIEW;
      return {
        workspaceId,
        view: typeof update === "function" ? update(currentView) : update,
      };
    });
  }, [workspaceId]);
  const key = Array.from(new Set(clusterIds)).sort().join(" ");
  // load 는 useBoundedPoll 이 매 렌더 ref 로 고정하므로 최신 ids 클로저를 안전하게 쓴다.
  const ids = useMemo(() => (key ? key.split(" ") : []), [key]);
  const streamScopeKey = workspaceId ? `fleet:${workspaceId}` : "";
  const { live: streamLive } = useFleetSummaryStream(
    streamScopeKey,
    (fleet) => setView({
      status: "ready",
      transport: "sse",
      clusters: toFleetSummaryViews(fleet),
      totals: fleet.totals,
      totalsObservation: "observed",
    }),
  );
  const fallbackScopeKey = workspaceId && !streamLive
    ? `fleet-fallback:${workspaceId}:${key || "empty"}`
    : "";

  useBoundedPoll({
    scopeKey: fallbackScopeKey,
    intervalMs: FLEET_REFRESH_MS,
    load: async (signal) => {
      // fleet 요청 전용 abort — 상위 스코프 abort 는 전파하되, 자체 타임아웃은 fleet 만
      // 끊어서 폴백(클러스터별 요약)이 실행될 기회를 보장한다.
      const fleetController = new AbortController();
      const onParentAbort = () => fleetController.abort();
      signal.addEventListener("abort", onParentAbort);
      const fleetTimer = setTimeout(() => fleetController.abort(), FLEET_CALL_TIMEOUT_MS);
      try {
        const fleet = await getFleetSummary(fleetController.signal);
        return { source: "http", fleet } satisfies FleetLoadResult;
      } catch (cause: unknown) {
        // 상위 스코프 취소만 그대로 전파. fleet 자체 타임아웃(AbortError지만 상위는
        // 살아있음)은 장애로 간주하고 폴백으로 진행한다.
        if (signal.aborted) throw cause;
        // fleet 집계 장애 → 클러스터별 canonical nodes/summary 로 동일 값을 채운다.
        const entries = await Promise.all(ids.map(async (id) => {
          try {
            const detail = await getClusterNodesSummary(id, signal);
            return [id, toClusterSummaryView(detail)] as const;
          } catch (fallbackCause: unknown) {
            if (signal.aborted || isAbortError(fallbackCause)) throw fallbackCause;
            return null;
          }
        }));
        const recovered = entries.filter(
          (entry): entry is readonly [string, ClusterSummaryView] => entry !== null,
        );
        if (recovered.length === 0) throw cause;
        return {
          source: "cluster-fallback",
          clusters: Object.fromEntries(recovered),
        } satisfies FleetLoadResult;
      } finally {
        clearTimeout(fleetTimer);
        signal.removeEventListener("abort", onParentAbort);
      }
    },
    onResult: (next) => setView((previous) => {
      if (next.source === "http") {
        return {
          status: "ready",
          transport: "http",
          clusters: toFleetSummaryViews(next.fleet),
          totals: next.fleet.totals,
          totalsObservation: "observed",
        };
      }
      return {
        status: "partial",
        transport: "cluster-fallback",
        clusters: next.clusters,
        totals: previous.totals,
        totalsObservation: previous.totals ? "stale" : "unavailable",
      };
    }),
    // 실패(폴백 포함 전부 실패)는 직전 정상 값을 유지한다 — 화면을 비우지 않는다.
    onError: () => setView((previous) => ({
      ...previous,
      status: previous.status === "loading" ? "unavailable" : previous.status,
      totalsObservation: previous.totals
        ? "stale"
        : previous.totalsObservation === "loading"
          ? "unavailable"
          : previous.totalsObservation,
    })),
  });

  return useMemo(() => view, [view]);
}

/** Compatibility projection for compact consumers that only need cluster rows. */
export function useFleetSummaries(
  workspaceId: string | null,
  clusterIds: readonly string[],
): Record<string, ClusterSummaryView> {
  return useFleetSummaryFeed(workspaceId, clusterIds).clusters;
}

function toFleetSummaryViews(fleet: FleetSummary): Record<string, ClusterSummaryView> {
  const next: Record<string, ClusterSummaryView> = {};
  for (const cluster of fleet.clusters) {
    next[cluster.cluster_id] = {
      status: "ready",
      health: cluster.health,
      cpuPct: cluster.cpu_pct,
      memPct: cluster.mem_pct,
      podsRunning: cluster.pods_running,
      podsTotal: cluster.pods_total,
      nodesReady: cluster.nodes_ready,
      nodesTotal: cluster.nodes_total,
      openIncidents: cluster.open_incidents,
      nodes: [],
      restartDelta: cluster.restarts_recent,
    };
  }
  return next;
}
