import { useState } from "react";

import { getChecksOverview } from "../api/checks";
import { useBoundedPoll } from "./useBoundedPoll";

// 점검 개요 실시간 갱신 주기(bounded, 60Hz 아님). 갱신 빈도 낮아 30초.
export const CHECKS_REFRESH_MS = 30_000;

// UI-PHASE2-001 §5.2: typed live adapter for the Checks surface. Reads
// `GET /api/checks/overview`. The current dev contract reports check
// results/catalog/visibility as `unavailable` with reason codes while still
// exposing real per-cluster scope coverage. Unsupported checks render as
// unavailable — never as "passing".

export type ChecksFeedStatus = "loading" | "ready" | "error";

export interface ChecksScopeView {
  clusterId: string;
  namespaces: string[];
  freshness: string;
}

export interface ChecksOverviewView {
  status: ChecksFeedStatus;
  scopeAvailability: string | null;
  resultAvailability: string | null;
  catalogAvailability: string | null;
  reasonCodes: string[];
  scopes: ChecksScopeView[];
}


export function useChecksOverview(): ChecksOverviewView {
  const [view, setView] = useState<ChecksOverviewView>({
    status: "loading",
    scopeAvailability: null,
    resultAvailability: null,
    catalogAvailability: null,
    reasonCodes: [],
    scopes: [],
  });
  // 점검 개요는 stale 허용 read라 화면이 보일 때만 30초 bounded 폴링으로 실시간화한다.
  // 점검 결과 갱신 빈도가 낮고 응답 비용이 중간이라 20s보다 완만한 30s를 택한다.
  useBoundedPoll({
    scopeKey: "checks",
    intervalMs: CHECKS_REFRESH_MS,
    load: (signal) => getChecksOverview({}, signal),
    onResult: (response) => {
      setView({
        status: "ready",
        scopeAvailability: response.scope_coverage.availability,
        resultAvailability: response.result_set.availability,
        catalogAvailability: response.catalog.availability,
        reasonCodes: [...response.result_set.reason_codes],
        scopes: response.scope_coverage.scopes.map((scope) => ({
          clusterId: scope.cluster_id,
          namespaces: [...scope.namespaces],
          freshness: scope.freshness,
        })),
      });
    },
    // stale-while-refresh: 재조회 실패 시 직전 ready 값을 유지하고, 최초 로드 실패만 error.
    onError: () => setView((prev) => (prev.status === "ready" ? prev : { ...prev, status: "error" })),
  });
  return view;
}
