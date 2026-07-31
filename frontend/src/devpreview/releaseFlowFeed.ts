import { useCallback, useMemo, useState } from "react";
import { useEffect } from "react";

import { createReleaseFlowClient, type ReleaseRunAction } from "../api/release-flow";
import type {
  ReleasePlanApi,
  ReleaseReadinessApi,
  ReleaseRunApi,
} from "../api/release-flow-schemas";
import { useVisibleRefreshClock } from "../shared/data/useVisibleRefreshClock";
import type { DeployFeedStatus } from "./deployFeed";
import { DEPLOY_LIST_ACTIVE_POLL_MS, DEPLOY_LIST_POLL_MS } from "./deployFeed";

// 릴리스 플랜/런 라이브 어댑터 — UI-PHASE2-001 원칙 유지.
// GET /api/release-plans · /api/release-runs 만 주기 조회하고, 모든 쓰기는
// 사용자 확인 후 executeRunAction/startPlan 을 통해서만 발생한다. 응답 외의
// 어떤 낙관적 상태도 만들지 않는다 — 성공 후 즉시 서버를 재조회한다.

/** 목록 상단 고정 등 "진행 중" 분류용 런 상태. */
const ACTIVE_RUN_STATUSES = new Set([
  "running", "in_progress", "pending", "starting", "progressing", "waiting_for_approval",
]);
/** 폴링 가속용 일시적 실행 상태 — 장기 대기(pending·approval)는 15초 유지. */
const TRANSIENT_RUN_STATUSES = new Set([
  "running", "in_progress", "starting", "progressing",
]);

export function isActiveRunStatus(status: string | null | undefined): boolean {
  return typeof status === "string" && ACTIVE_RUN_STATUSES.has(status.trim().toLowerCase());
}

export function runEffectiveStatus(run: ReleaseRunApi): string {
  const derived = typeof run.derived_status === "string" ? run.derived_status.trim() : "";
  return derived !== "" ? derived : run.status;
}

export interface ReleaseFlowFeed {
  status: DeployFeedStatus;
  plans: ReleasePlanApi[];
  runs: ReleaseRunApi[];
  /** 진행형 상태의 런 — 목록 상단 고정용. */
  activeRuns: ReleaseRunApi[];
  refresh: () => void;
}

/**
 * 릴리스 플랜·런을 함께 읽는다. enabled=false(탭 비활성)면 요청하지 않는다.
 * 진행 중 런이 관측되면 5초, 아니면 15초 주기(hidden 탭 자동 중단).
 */
export function useReleaseFlow(enabled: boolean): ReleaseFlowFeed {
  const client = useMemo(() => createReleaseFlowClient(), []);
  const [state, setState] = useState<{
    status: DeployFeedStatus;
    plans: ReleasePlanApi[];
    runs: ReleaseRunApi[];
  }>({ status: "loading", plans: [], runs: [] });
  const [manualRevision, setManualRevision] = useState(0);
  const active = state.runs.some((run) =>
    TRANSIENT_RUN_STATUSES.has(runEffectiveStatus(run).trim().toLowerCase()));
  const { revision } = useVisibleRefreshClock(
    enabled,
    active ? DEPLOY_LIST_ACTIVE_POLL_MS : DEPLOY_LIST_POLL_MS,
  );

  useEffect(() => {
    if (!enabled) return;
    const controller = new AbortController();
    void Promise.allSettled([
      client.listPlans(controller.signal),
      client.listRuns(undefined, controller.signal),
    ] as const).then(([plans, runs]) => {
      if (controller.signal.aborted) return;
      if (plans.status === "rejected" && runs.status === "rejected") {
        setState({ status: "unavailable", plans: [], runs: [] });
        return;
      }
      setState({
        status: "ready",
        plans: plans.status === "fulfilled" ? plans.value.plans : [],
        runs: runs.status === "fulfilled"
          ? [...runs.value.runs].sort((left, right) =>
              (right.updated_at ?? right.created_at ?? "").localeCompare(left.updated_at ?? left.created_at ?? ""))
          : [],
      });
    });
    return () => controller.abort();
  }, [client, enabled, revision, manualRevision]);

  const refresh = useCallback(() => setManualRevision((value) => value + 1), []);
  const activeRuns = state.runs.filter((run) => isActiveRunStatus(runEffectiveStatus(run)));
  if (!enabled) return { status: "loading", plans: [], runs: [], activeRuns: [], refresh };
  return { ...state, activeRuns, refresh };
}

// ── 쓰기 액션 — 확인 후 실행, 결과는 서버 응답만 신뢰 ───────────────────────

export interface ReleaseActionState {
  /** 실행 중인 액션 키(런ID:액션) — 버튼 스피너/비활성 용도. */
  pendingKey: string | null;
  /** 마지막 실행 결과 — 패널 안 정직한 결과 표시(토스트 대체). */
  lastResult: { key: string; ok: boolean; message: string } | null;
}

export interface ReleaseActions {
  state: ReleaseActionState;
  executeRunAction: (runId: string, action: ReleaseRunAction, reason?: string) => Promise<boolean>;
  startPlan: (plan: ReleasePlanApi) => Promise<boolean>;
  checkReadiness: (plan: ReleasePlanApi, signal?: AbortSignal) => Promise<ReleaseReadinessApi>;
}

function errorMessage(cause: unknown): string {
  if (cause instanceof Error && cause.message.trim() !== "") return cause.message;
  return "요청이 거부되었습니다";
}

/** 릴리스 쓰기 액션 실행기 — 성공/실패 모두 onSettled(재조회)를 호출한다. */
export function useReleaseActions(onSettled: () => void): ReleaseActions {
  const client = useMemo(() => createReleaseFlowClient(), []);
  const [state, setState] = useState<ReleaseActionState>({ pendingKey: null, lastResult: null });

  const executeRunAction = useCallback(async (
    runId: string,
    action: ReleaseRunAction,
    reason?: string,
  ): Promise<boolean> => {
    const key = `${runId}:${action}`;
    setState({ pendingKey: key, lastResult: null });
    try {
      await client.runAction(runId, action, reason);
      setState({ pendingKey: null, lastResult: { key, ok: true, message: "서버가 요청을 수락했습니다" } });
      onSettled();
      return true;
    } catch (cause) {
      setState({ pendingKey: null, lastResult: { key, ok: false, message: errorMessage(cause) } });
      onSettled();
      return false;
    }
  }, [client, onSettled]);

  const startPlan = useCallback(async (plan: ReleasePlanApi): Promise<boolean> => {
    const key = `${plan.plan_id ?? plan.name}:start`;
    setState({ pendingKey: key, lastResult: null });
    try {
      const run = await client.startPlan(plan);
      setState({ pendingKey: null, lastResult: { key, ok: true, message: `런 시작됨 · ${run.run_id}` } });
      onSettled();
      return true;
    } catch (cause) {
      setState({ pendingKey: null, lastResult: { key, ok: false, message: errorMessage(cause) } });
      onSettled();
      return false;
    }
  }, [client, onSettled]);

  const checkReadiness = useCallback(
    (plan: ReleasePlanApi, signal?: AbortSignal) => client.checkReadiness(plan, signal),
    [client],
  );

  return { state, executeRunAction, startPlan, checkReadiness };
}
