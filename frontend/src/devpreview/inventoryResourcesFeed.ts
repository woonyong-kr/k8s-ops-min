import { useEffect, useState } from "react";

import { listInventoryResourcesByType } from "../api/inventory-query";
import type { InventoryResource } from "../api/inventory-schemas";
import { projectInventoryResourceRow } from "./inventoryResourceTableProjection";
import { getSharedInventorySummary } from "./inventorySummaryFeed";

// UI-PHASE2-001: typed live adapter for the 통합 리소스 37종 테이블.
// Reads only `GET /api/clusters/{id}/inventory/resources?resource_type=` and
// `GET /api/clusters/{id}/inventory/summary`. Kind-specific values are projected
// from each row's bounded `summary`; absent evidence remains an honest blank.

export type ResourcesFeedStatus = "loading" | "ready" | "unavailable";

export type Row = Record<string, unknown>;

export interface InventoryResourcesView {
  status: ResourcesFeedStatus;
  rows: Row[];
}

export interface InventoryKindCountsView {
  status: ResourcesFeedStatus;
  // meta[clusterId][resource_type] = observed count. resource_type keys are
  // lowercased so lookups by kindToResourceType (also lowercased) always align.
  meta: Record<string, Record<string, number>>;
}

// Gateway가 허용하는 최대 단일 응답. 전체 클러스터는 클러스터별로 각각 이 한도를
// 사용하므로 기존 500 고정 때문에 요약 카운트와 목록이 과도하게 어긋나던 문제를 줄인다.
const RESOURCE_QUERY_LIMIT = 1000;

// kindId → backend resource_type 추정값. 백엔드 resource_type의 정확한 표기가
// 불확실하므로 대부분은 kindId를 소문자화한 값(= 쿠버네티스 kind 소문자)을 쓰고,
// 약어로 표기된 종류(HPA/PVC)만 정식 kind 이름으로 매핑한다.
const KIND_TO_RESOURCE_TYPE: Record<string, string> = {
  HPA: "hpa",
  HorizontalPodAutoscaler: "hpa",
  PVC: "pvc",
  PersistentVolumeClaim: "pvc",
  EndpointSlice: "endpoint",
};

export function kindToResourceType(kindId: string): string {
  return KIND_TO_RESOURCE_TYPE[kindId] ?? kindId.toLowerCase();
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error
    && (error as { name?: unknown }).name === "AbortError";
}

function matchesResourceType(resource: InventoryResource, resourceType: string): boolean {
  return resource.resource_type.toLowerCase() === resourceType
    || kindToResourceType(resource.kind) === resourceType;
}

export function useInventoryResources(
  clusterId: string | null,
  resourceType: string | null,
): InventoryResourcesView {
  const [view, setView] = useState<InventoryResourcesView>({ status: "loading", rows: [] });
  const rt = resourceType?.trim() ?? "";
  // 빈 문자열 clusterId(cold-start 파생)를 차단해 `GET /api/clusters//inventory/resources`
  // double-slash 404를 막는다. all-cluster 스코프는 useInventoryResourcesAcrossClusters(fan-out)로 조회.
  const canFetch = clusterId !== null && clusterId.trim() !== "" && rt !== "";
  // useEffect dep = 조인된 단일 문자열 key. 클러스터 id·resource_type 모두 공백이
  // 없으므로 이펙트 내부에서 key만으로 되살려 참조(exhaustive-deps: key 하나).
  const key = canFetch ? [clusterId, rt].join(" ") : "";
  useEffect(() => {
    // 빈 스코프(클러스터 미선택) 또는 resource_type 미지정 시 요청하지 않는다.
    // 동기 setState 금지 규칙에 따라 여기서는 상태를 만지지 않고,
    // 아래 파생 반환(canFetch === false → 빈 ready)으로 빈 결과를 낸다.
    if (key === "") return;
    const [cid, type] = key.split(" ");
    const controller = new AbortController();
    listInventoryResourcesByType(
      cid,
      { resourceType: type, limit: RESOURCE_QUERY_LIMIT },
      controller.signal,
    )
      .then((response) => {
        if (controller.signal.aborted) return;
        // 이중 안전장치: 서버가 resource_type 쿼리 표기를 다르게 받거나 무시해도,
        // 정식 kind 소문자 === 추정 resource_type 로 한 번 더 걸러 표기 불일치로
        // 빈 표/오염 행이 나오는 것을 막는다.
        const rows = response.resources
          .filter((resource) => matchesResourceType(resource, type))
          .map((resource) => projectInventoryResourceRow(resource));
        setView({ status: "ready", rows });
      })
      .catch((cause: unknown) => {
        if (isAbortError(cause)) return;
        setView({ status: "unavailable", rows: [] });
      });
    return () => controller.abort();
  }, [key]);
  return canFetch ? view : { status: "ready", rows: [] };
}

/**
 * 여러 클러스터의 동일 resource type을 실제 클러스터별 API에서 병렬 조회해 합친다.
 * gateway 계약이 단일 클러스터 경로만 제공하므로 `전체 클러스터`는 서버 값을
 * 이름으로 추정하지 않고 이 fan-out 결과의 합집합으로만 표현한다.
 */
export function useInventoryResourcesAcrossClusters(
  clusterIds: readonly string[],
  resourceType: string | null,
): InventoryResourcesView {
  const [view, setView] = useState<InventoryResourcesView & { key: string }>({
    status: "loading",
    rows: [],
    key: "",
  });
  const type = resourceType?.trim() ?? "";
  const ids = [...new Set(clusterIds.filter(Boolean))];
  const key = type && ids.length > 0 ? `${type}\u0000${ids.join("\u0000")}` : "";
  useEffect(() => {
    if (!key) return;
    const [requestedType, ...requestedIds] = key.split("\u0000");
    const controller = new AbortController();
    void Promise.allSettled(requestedIds.map((clusterId) =>
      listInventoryResourcesByType(
        clusterId,
        { resourceType: requestedType, limit: RESOURCE_QUERY_LIMIT },
        controller.signal,
      ),
    )).then((results) => {
      if (controller.signal.aborted) return;
      const fulfilled = results.filter((result): result is PromiseFulfilledResult<Awaited<ReturnType<typeof listInventoryResourcesByType>>> => result.status === "fulfilled");
      const rows = fulfilled
        .flatMap((result) => result.value.resources)
        .filter((resource) => matchesResourceType(resource, requestedType))
        .map((resource) => projectInventoryResourceRow(resource))
        .sort((a, b) => `${String(a.cluster)}\u0000${String(a.ns ?? "")}\u0000${String(a.name)}`.localeCompare(`${String(b.cluster)}\u0000${String(b.ns ?? "")}\u0000${String(b.name)}`));
      setView({ status: fulfilled.length > 0 ? "ready" : "unavailable", rows, key });
    });
    return () => controller.abort();
  }, [key]);
  if (!key || ids.length === 0) return { status: "ready", rows: [] };
  return view.key === key ? view : { status: "loading", rows: [] };
}

export function useInventoryKindCounts(
  clusterIds: readonly string[],
): InventoryKindCountsView {
  const [view, setView] = useState<InventoryKindCountsView>({ status: "loading", meta: {} });
  const key = clusterIds.join(" ");
  useEffect(() => {
    const ids = key ? key.split(" ") : [];
    if (ids.length === 0) return;
    const controller = new AbortController();
    const meta: Record<string, Record<string, number>> = {};
    let remaining = ids.length;
    let anyReady = false;
    for (const id of ids) {
      void getSharedInventorySummary(id)
        .then((summary) => {
          if (controller.signal.aborted) return;
          anyReady = true;
          const byType: Record<string, number> = {};
          for (const ns of summary.namespaces) {
            for (const count of ns.counts) {
              const type = count.resource_type.toLowerCase();
              byType[type] = (byType[type] ?? 0) + count.count;
            }
          }
          meta[id] = byType;
        })
        .catch((cause: unknown) => {
          if (isAbortError(cause)) return;
          // 한 클러스터의 인벤토리 실패는 그 클러스터만 비운다(전체 실패 아님).
        })
        .finally(() => {
          if (controller.signal.aborted) return;
          remaining -= 1;
          if (remaining === 0) {
            setView({ status: anyReady ? "ready" : "unavailable", meta });
          }
        });
    }
    return () => controller.abort();
  }, [key]);
  return view;
}
