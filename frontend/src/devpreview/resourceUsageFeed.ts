import { useCallback, useEffect, useState } from "react";

import { getClusterResourceUsageSeries } from "../api/usage-series";

// UI-PHASE2-METRICS-001 · M14 · typed live adapter for the resource detail
// metric chart. Reads `GET /api/clusters/{id}/usage` and extracts one Pod's (or
// Node's) observed CPU/memory samples. Only observed usage is charted — the
// static `cpu_request_mcores` request is deliberately NOT read as usage. Samples
// where a metric was not observed stay null and render as honest gaps; nothing
// is interpolated or fabricated.

export type ResourceUsageStatus =
  | "idle"
  | "loading"
  | "ready"
  | "unavailable"
  | "error";

export interface ResourceUsagePoint {
  sampledAt: string | null;
  cpuMcores: number | null;
  memMib: number | null;
}

export interface ResourceUsageView {
  status: ResourceUsageStatus;
  points: ResourceUsagePoint[];
  /** 관측된 표본이 하나라도 있는 metric만 true — 없으면 "관측 안 됨"으로 표기. */
  hasCpu: boolean;
  hasMemory: boolean;
  /** 전체 표본 대비 각 metric의 관측 표본 수(부분 관측 표기용). */
  sampleCount: number;
  cpuObserved: number;
  memObserved: number;
  /** 일시적 오류(error status)에서 재조회 트리거. */
  retry: () => void;
}

const NOOP = () => {};

const IDLE: ResourceUsageView = {
  status: "idle",
  points: [],
  hasCpu: false,
  hasMemory: false,
  sampleCount: 0,
  cpuObserved: 0,
  memObserved: 0,
  retry: NOOP,
};

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error
    && (error as { name?: unknown }).name === "AbortError";
}

const SEP = " ";

/**
 * Loads one resource's observed usage series for the detail metric chart.
 * 빈 스코프면 요청하지 않고 idle. scope 변경 시 이전 요청을 abort 한다.
 */
export function useResourceUsageSeries(
  clusterId: string | null,
  resourceType: "pod" | "node",
  namespace: string | null,
  name: string,
): ResourceUsageView {
  // 내부 상태는 retry를 제외하고, retry는 훅 반환 시점에 부착한다.
  const [view, setView] = useState<Omit<ResourceUsageView, "retry"> & { key: string }>({ ...IDLE, key: "" });
  const [nonce, setNonce] = useState(0);
  const cid = clusterId?.trim() ?? "";
  const nm = name.trim();
  // pod은 namespace가 필요하다. node는 namespace 없이 조회한다.
  const ns = namespace?.trim() ?? "";
  const canFetch = cid !== "" && nm !== "" && (resourceType === "node" || ns !== "");
  const key = canFetch ? [cid, resourceType, ns, nm].join(SEP) : "";
  useEffect(() => {
    if (key === "") return;
    const [reqCid, reqType, reqNs, reqName] = key.split(SEP);
    const controller = new AbortController();
    const target = reqType === "pod"
      ? { resourceType: "pod" as const, namespace: reqNs, name: reqName }
      : { resourceType: "node" as const, name: reqName };
    getClusterResourceUsageSeries(reqCid, target, {}, controller.signal)
      .then((series) => {
        if (controller.signal.aborted) return;
        const points = series.points.map((p) => ({
          sampledAt: p.sampledAt,
          cpuMcores: p.cpuMcores,
          memMib: p.memMib,
        }));
        const cpuObserved = points.filter((p) => p.cpuMcores !== null).length;
        const memObserved = points.filter((p) => p.memMib !== null).length;
        setView({
          status: cpuObserved > 0 || memObserved > 0 ? "ready" : "unavailable",
          points,
          hasCpu: cpuObserved > 0,
          hasMemory: memObserved > 0,
          sampleCount: points.length,
          cpuObserved,
          memObserved,
          key,
        });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || isAbortError(cause)) return;
        // 요청 실패(HTTP/네트워크)는 재시도 가능한 error. 200이지만 관측 표본이 없는
        // 경우(위 then의 unavailable)와 구분해 "데이터 없음"과 "일시적 오류"를 섞지 않는다.
        setView({ ...IDLE, status: "error", key });
      });
    return () => controller.abort();
  }, [key, nonce]);
  const retry = useCallback(() => {
    setView((prev) => ({ ...IDLE, status: "loading", key: prev.key }));
    setNonce((n) => n + 1);
  }, []);
  if (!canFetch) return { ...IDLE, retry };
  return { ...(view.key === key ? view : { ...IDLE, status: "loading" }), retry };
}
