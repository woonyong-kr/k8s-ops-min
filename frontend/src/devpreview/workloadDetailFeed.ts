import { useCallback, useEffect, useState } from "react";

import { getWorkloadDetail } from "../api/workload-detail";

// UI-PHASE2-METRICS-001 · M13/M16 · typed live adapter for the resource detail
// drawer. Reads `GET /api/workloads/{kind}/{namespace}/{name}` which — contrary
// to the drawer's previous "계약 없음" placeholders — exposes real replicas,
// health, labels, managed pods, recent events, and the observation coverage.
// Fields the observation does not carry are left null and render as honest
// "관측 안 됨"; nothing is fabricated. Only supported workload kinds issue a
// request; other kinds stay idle so the drawer keeps its honest generic view.

export type WorkloadDetailStatus =
  | "idle"
  | "loading"
  | "ready"
  | "unavailable"
  | "error";

export interface WorkloadReplicaView {
  desired: number | null;
  ready: number | null;
  available: number | null;
  updated: number | null;
  unavailable: number | null;
}

export interface WorkloadLabelView {
  key: string;
  value: string;
}

export interface WorkloadPodView {
  name: string;
  namespace: string | null;
  health: string;
}

export interface WorkloadEventView {
  type: string | null;
  reason: string | null;
  count: number | null;
  lastAt: string | null;
}

export interface WorkloadDetailView {
  status: WorkloadDetailStatus;
  /** 관측 커버리지 — availability가 partial이면 일부 범위만 수집된 것이다. */
  coverageAvailability: string | null;
  coverageReasonCodes: string[];
  observedAt: string | null;
  health: string | null;
  replicas: WorkloadReplicaView | null;
  labels: WorkloadLabelView[];
  podsAvailability: string | null;
  podsExcludedCount: number;
  pods: WorkloadPodView[];
  eventsAvailability: string | null;
  events: WorkloadEventView[];
  /** capability 계약이 advertise한 action id들(예: restart/scale). advertise된 것만. */
  actions: string[];
  /** 일시적 오류(error status)에서 사용자가 재조회할 수 있게 하는 재시도 트리거. */
  retry: () => void;
}

const NOOP = () => {};

const IDLE: WorkloadDetailView = {
  status: "idle",
  coverageAvailability: null,
  coverageReasonCodes: [],
  observedAt: null,
  health: null,
  replicas: null,
  labels: [],
  podsAvailability: null,
  podsExcludedCount: 0,
  pods: [],
  eventsAvailability: null,
  events: [],
  actions: [],
  retry: NOOP,
};

// 이 detail 계약이 실제로 배선된 워크로드 kind → {apiGroup, apiVersion}.
// 계약이 다루지 않는 kind는 요청하지 않고 idle로 남겨 honest 일반 뷰를 유지한다.
const WORKLOAD_API: Record<string, { apiGroup: string; apiVersion: string }> = {
  Deployment: { apiGroup: "apps", apiVersion: "v1" },
  StatefulSet: { apiGroup: "apps", apiVersion: "v1" },
  DaemonSet: { apiGroup: "apps", apiVersion: "v1" },
  ReplicaSet: { apiGroup: "apps", apiVersion: "v1" },
};

export function workloadDetailSupported(kindId: string): boolean {
  return kindId in WORKLOAD_API;
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error
    && (error as { name?: unknown }).name === "AbortError";
}

// key 조합/분해용 구분자. k8s 식별자·apiGroup·version에는 포함될 수 없는 문자다.
const SEP = "\u0000";

/**
 * Loads the live workload detail for a resource drawer. `clusterId`/`name`이
 * 비었거나 kind가 미지원이면 요청하지 않고 idle을 반환한다. scope가 바뀌면 이전
 * 요청을 abort 하므로 stale 응답이 새 선택을 덮지 못한다.
 */
export function useWorkloadDetail(
  clusterId: string | null,
  kindId: string,
  namespace: string | null,
  name: string,
): WorkloadDetailView {
  // 내부 상태는 retry를 담지 않는다(retry는 훅이 반환 시점에 부착). 그래서 setView는
  // 순수 관측 결과만 담고, 공개 반환 타입에서 retry가 항상 존재하도록 보장한다.
  const [view, setView] = useState<Omit<WorkloadDetailView, "retry"> & { key: string }>({ ...IDLE, key: "" });
  const [nonce, setNonce] = useState(0);
  const api = WORKLOAD_API[kindId];
  const cid = clusterId?.trim() ?? "";
  const nm = name.trim();
  const canFetch = api !== undefined && cid !== "" && nm !== "";
  // namespace가 null이면 빈 문자열로 인코딩(실 namespace는 최소 길이 1이므로 충돌 없음).
  const key = canFetch
    ? [cid, api.apiGroup, api.apiVersion, kindId, namespace ?? "", nm].join(SEP)
    : "";
  useEffect(() => {
    if (key === "") return;
    const [reqCid, reqGroup, reqVersion, reqKind, reqNs, reqName] = key.split(SEP);
    const controller = new AbortController();
    getWorkloadDetail(
      {
        clusterId: reqCid,
        apiGroup: reqGroup,
        apiVersion: reqVersion,
        kind: reqKind,
        namespace: reqNs === "" ? null : reqNs,
        name: reqName,
      },
      controller.signal,
    )
      .then((response) => {
        if (controller.signal.aborted) return;
        const d = response.detail;
        setView({
          status: "ready",
          coverageAvailability: d.coverage.availability,
          coverageReasonCodes: [...d.coverage.reason_codes],
          observedAt: d.observation.observed_at,
          health: d.observation.health,
          replicas: {
            desired: d.observation.replicas.desired,
            ready: d.observation.replicas.ready,
            available: d.observation.replicas.available,
            updated: d.observation.replicas.updated,
            unavailable: d.observation.replicas.unavailable,
          },
          labels: d.observation.labels.map((l) => ({ key: l.key, value: l.value })),
          podsAvailability: d.pods.availability,
          podsExcludedCount: d.pods.excluded_count,
          pods: d.pods.items.map((p) => ({
            name: p.resource.name,
            namespace: p.resource.namespace,
            health: p.health,
          })),
          eventsAvailability: d.events.availability,
          events: d.events.items.map((e) => ({
            type: e.event_type,
            reason: e.reason,
            count: e.occurrence_count,
            lastAt: e.last_occurred_at,
          })),
          actions: [...d.capabilities.actions],
          key,
        });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || isAbortError(cause)) return;
        // 요청 자체가 실패한 것(HTTP/네트워크 오류)은 "관측 안 됨(unavailable)"이 아니라
        // 재시도 가능한 error로 구분한다 — 데이터 부재와 일시적 오류를 섞지 않는다.
        setView({ ...IDLE, status: "error", key });
      });
    return () => controller.abort();
    // nonce 변경 시 동일 key라도 재조회한다(사용자 재시도).
  }, [key, nonce]);
  const retry = useCallback(() => {
    setView((prev) => ({ ...IDLE, status: "loading", key: prev.key }));
    setNonce((n) => n + 1);
  }, []);
  if (!canFetch) return { ...IDLE, retry };
  return { ...(view.key === key ? view : { ...IDLE, status: "loading" }), retry };
}
