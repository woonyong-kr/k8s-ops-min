import { useEffect, useState } from "react";

import { listApplicationRuns, listApplications } from "../api/applications";
import type { Application, WorkflowRun } from "../api/applications-schemas";
import { listHelmReleases } from "../api/helm-releases";
import { useVisibleRefreshClock } from "../shared/data/useVisibleRefreshClock";

// UI-PHASE2-001 §2 "Deploy": typed live adapters for the /deploy surface.
//
// `GET /api/applications` returns an opaque `jsonMap` per Application, so every
// field is read defensively — a missing field renders as an honest gap, never a
// fabricated value. `GET /api/helm/releases` currently reports coverage
// `unavailable` with reason codes; that honest state is surfaced rather than a
// backfilled release table. Both hooks are strictly read-only.
//
// 갱신 정책: 목록 훅은 탭이 보이는 동안 주기 폴링한다(useVisibleRefreshClock —
// hidden 탭에서는 요청하지 않는다). 재조회 중에는 마지막 관측 값을 유지해
// 화면이 스켈레톤으로 되돌아가지 않는다. Helm은 서버가 지시한
// refresh_after_seconds를 따른다 — 임의 주기를 지어내지 않는다.

/** 애플리케이션·워크플로우 목록 폴링 주기. */
export const DEPLOY_LIST_POLL_MS = 15_000;
/** 배포가 진행 중(관측된 활성 상태)일 때의 가속 폴링 주기. */
export const DEPLOY_LIST_ACTIVE_POLL_MS = 5_000;
/** 폴링을 가속할 일시적 진행 상태 — pending·waiting_for_approval 은 몇 시간씩
 * 지속될 수 있는 대기 상태라 제외한다(useApplications 는 셸 전역에서도 쓰여
 * 가속이 앱 전체 요청량으로 번진다). */
const ACTIVE_DELIVERY_STATUSES = new Set([
  "progressing", "running", "starting", "in_progress",
]);
const HELM_LIST_MIN_POLL_MS = 5_000;
const HELM_LIST_MAX_POLL_MS = 60_000;
const HELM_LIST_FALLBACK_POLL_MS = 15_000;

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error
    && (error as { name?: unknown }).name === "AbortError";
}

// ── defensive jsonMap readers ────────────────────────────────────────────────

function readString(record: Record<string, unknown>, key: string): string | null {
  const value = record[key];
  return typeof value === "string" && value.trim() !== "" ? value : null;
}

function readStringArray(record: Record<string, unknown>, key: string): string[] {
  const value = record[key];
  if (!Array.isArray(value)) return [];
  return value.filter((item): item is string => typeof item === "string" && item.trim() !== "");
}

function readObject(record: Record<string, unknown>, key: string): Record<string, unknown> | null {
  const value = record[key];
  return typeof value === "object" && value !== null && !Array.isArray(value)
    ? (value as Record<string, unknown>)
    : null;
}

function readObjects(record: Record<string, unknown>, key: string): Record<string, unknown>[] {
  const value = record[key];
  return Array.isArray(value)
    ? value.filter((item): item is Record<string, unknown> => typeof item === "object" && item !== null && !Array.isArray(item))
    : [];
}

// ── applications ─────────────────────────────────────────────────────────────

export type DeployFeedStatus = "loading" | "ready" | "unavailable";

export interface ApplicationView {
  id: string;
  name: string;
  environments: string[];
  lifecycleStatus: string | null;
  repositoryRef: string | null;
  defaultBranch: string | null;
  manifestPath: string | null;
  /** Runtime health status, e.g. "unknown" — never coerced to "healthy". */
  healthStatus: string | null;
  /** Delivery/GitOps rollout status, e.g. "pending". */
  deliveryStatus: string | null;
  deliveryAvailability: string | null;
  workflowRunId: string | null;
  deliveryObservedAt: string | null;
}

export interface ApplicationsFeed {
  status: DeployFeedStatus;
  items: ApplicationView[];
  /** The last authorized snapshot is retained after a refresh failure. */
  stale: boolean;
}

function toApplicationView(record: Application, index: number): ApplicationView {
  const health = readObject(record, "health");
  const delivery = readObject(record, "delivery");
  const id = readString(record, "id") ?? `app-${index}`;
  return {
    id,
    name: readString(record, "name") ?? id,
    environments: readStringArray(record, "environments"),
    lifecycleStatus: readString(record, "lifecycle_status"),
    repositoryRef: readString(record, "repository_ref"),
    defaultBranch: readString(record, "default_branch"),
    manifestPath: readString(record, "manifest_path"),
    healthStatus: health ? readString(health, "status") : null,
    deliveryStatus: delivery ? readString(delivery, "status") : null,
    deliveryAvailability: delivery ? readString(delivery, "availability") : null,
    workflowRunId: delivery ? readString(delivery, "workflow_run_id") : null,
    deliveryObservedAt: delivery ? readString(delivery, "observed_at") : null,
  };
}

/**
 * Reads the live Application list. An empty list is an honest "관측된
 * 애플리케이션 없음"; a load failure is an honest `unavailable`.
 */
export function useApplications(
  refreshKey: unknown = null,
  enabled = true,
): ApplicationsFeed {
  const [feed, setFeed] = useState<ApplicationsFeed>({
    status: "loading",
    items: [],
    stale: false,
  });
  // 진행 중 배포가 관측되면 폴링을 가속한다 — 상태는 항상 서버 관측값에서만 파생.
  const active = feed.items.some((item) =>
    (item.deliveryStatus !== null && ACTIVE_DELIVERY_STATUSES.has(item.deliveryStatus))
    || (item.lifecycleStatus !== null && ACTIVE_DELIVERY_STATUSES.has(item.lifecycleStatus)));
  const { revision } = useVisibleRefreshClock(
    enabled,
    active ? DEPLOY_LIST_ACTIVE_POLL_MS : DEPLOY_LIST_POLL_MS,
  );
  useEffect(() => {
    if (!enabled) return undefined;
    const controller = new AbortController();
    void listApplications({ signal: controller.signal })
      .then((response) => {
        if (controller.signal.aborted) return;
        setFeed({
          status: "ready",
          items: response.applications.map(toApplicationView),
          stale: false,
        });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || isAbortError(cause)) return;
        setFeed((previous) => previous.status === "ready"
          ? { ...previous, stale: true }
          : { status: "unavailable", items: [], stale: false });
      });
    return () => controller.abort();
  }, [enabled, refreshKey, revision]);
  return feed;
}

// ── actual GitOps workflow evidence ─────────────────────────────────────────

export interface WorkflowStepView {
  name: string;
  status: string | null;
  message: string | null;
  updatedAt: string | null;
  details: Record<string, unknown>;
}

export interface ApplicationRunView {
  applicationId: string;
  applicationName: string;
  repositoryRef: string | null;
  workflowRunId: string;
  status: string | null;
  currentStep: string | null;
  commitSha: string | null;
  clusterId: string | null;
  commandId: string | null;
  createdAt: string | null;
  updatedAt: string | null;
  steps: WorkflowStepView[];
  promotionGate: Record<string, unknown> | null;
}

export interface ApplicationRunsFeed {
  status: DeployFeedStatus;
  items: ApplicationRunView[];
}

function toRunView(run: WorkflowRun, application: ApplicationView): ApplicationRunView | null {
  const workflowRunId = readString(run, "workflow_run_id");
  if (workflowRunId === null) return null;
  return {
    applicationId: application.id,
    applicationName: application.name,
    repositoryRef: application.repositoryRef,
    workflowRunId,
    status: readString(run, "status"),
    currentStep: readString(run, "current_step"),
    commitSha: readString(run, "commit_sha"),
    clusterId: readString(run, "cluster_id"),
    commandId: readString(run, "command_id"),
    createdAt: readString(run, "created_at"),
    updatedAt: readString(run, "updated_at"),
    steps: readObjects(run, "steps").map((step) => ({
      name: readString(step, "name") ?? "",
      status: readString(step, "status"),
      message: readString(step, "message"),
      updatedAt: readString(step, "updated_at"),
      details: readObject(step, "details") ?? {},
    })),
    promotionGate: readObject(run, "promotion_gate"),
  };
}

/**
 * Reads server-recorded workflow history for the currently visible
 * Applications. This is deliberately read-only: the demo gate never advances
 * from a timer or a local optimistic state.
 */
export function useApplicationRuns(
  applications: ApplicationView[],
  refreshKey: unknown = null,
): ApplicationRunsFeed {
  const [feed, setFeed] = useState<ApplicationRunsFeed>({ status: "loading", items: [] });
  // 활성 실행이 관측되면 가속 — 최신 run의 상태만 보면 충분하다(정렬 최상단).
  const active = feed.items.some((run) =>
    run.status !== null && ACTIVE_DELIVERY_STATUSES.has(run.status));
  const { revision } = useVisibleRefreshClock(
    applications.length > 0,
    active ? DEPLOY_LIST_ACTIVE_POLL_MS : DEPLOY_LIST_POLL_MS,
  );
  const applicationKey = applications.map(({ id, workflowRunId }) => `${id}:${workflowRunId ?? ""}`).join("|");
  useEffect(() => {
    if (applications.length === 0) {
      return undefined;
    }
    const controller = new AbortController();
    // 재조회·범위 변경 중에는 마지막 관측 값을 유지한다(stale-while-revalidate) —
    // 초기 상태가 이미 loading이므로 여기서 동기 setState로 되돌리지 않는다.
    void Promise.allSettled(applications.map(async (application) => {
      // A single application can accumulate many connect-validation and retry
      // runs before the GitOps recovery flow completes. Read the full bounded
      // server history so preserved failure/recovery evidence is not pushed
      // out of the local demo surface by newer validation-only runs.
      const response = await listApplicationRuns(application.id, { limit: 500, signal: controller.signal });
      return response.runs
        .map((run) => toRunView(run, application))
        .filter((run): run is ApplicationRunView => run !== null);
    })).then((results) => {
      if (controller.signal.aborted) return;
      const fulfilled = results.filter((result): result is PromiseFulfilledResult<ApplicationRunView[]> => result.status === "fulfilled");
      const items = fulfilled.flatMap(({ value }) => value).sort((left, right) =>
        (right.updatedAt ?? right.createdAt ?? "").localeCompare(left.updatedAt ?? left.createdAt ?? ""));
      setFeed({ status: fulfilled.length > 0 ? "ready" : "unavailable", items });
    });
    return () => controller.abort();
    // applicationKey is a stable serialization of the server-owned identities.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [applicationKey, refreshKey, revision]);
  return applications.length === 0 ? { status: "ready", items: [] } : feed;
}

// ── helm releases ────────────────────────────────────────────────────────────

export interface HelmReleaseView {
  name: string;
  namespace: string;
  /** Helm 스토리지 네임스페이스 — 상세 조회 경로의 정본 식별자. */
  storageNamespace: string;
  clusterId: string;
  chart: string | null;
  chartVersion: string | null;
  status: string | null;
  revision: number | null;
}

export interface HelmReleasesFeed {
  status: DeployFeedStatus;
  items: HelmReleaseView[];
  coverageAvailability: string | null;
  reasonCodes: string[];
}

/**
 * Reads the live Helm release inventory. When coverage is `unavailable` the
 * hook still reports `ready` with an empty release list and the server reason
 * codes so the surface can render an honest "관측 안 됨" rather than a fake
 * release table.
 */
export function useHelmReleases(): HelmReleasesFeed {
  const [feed, setFeed] = useState<HelmReleasesFeed>({
    status: "loading",
    items: [],
    coverageAvailability: null,
    reasonCodes: [],
  });
  const [pollMs, setPollMs] = useState(HELM_LIST_FALLBACK_POLL_MS);
  const { revision } = useVisibleRefreshClock(true, pollMs);
  useEffect(() => {
    const controller = new AbortController();
    void listHelmReleases({}, controller.signal)
      .then((response) => {
        if (controller.signal.aborted) return;
        // 서버가 지시한 재조회 주기를 그대로 따른다(안전 클램프만 적용).
        const advised = Math.round(response.refresh_after_seconds * 1000);
        setPollMs(Number.isFinite(advised) && advised > 0
          ? Math.min(HELM_LIST_MAX_POLL_MS, Math.max(HELM_LIST_MIN_POLL_MS, advised))
          : HELM_LIST_FALLBACK_POLL_MS);
        setFeed({
          status: "ready",
          items: response.releases.map((release) => ({
            name: release.name,
            namespace: release.scope.namespaces[0] ?? release.storage_namespace,
            storageNamespace: release.storage_namespace,
            clusterId: release.scope.cluster_id,
            chart: release.chart,
            chartVersion: release.chart_version,
            status: release.status,
            revision: release.revision,
          })),
          coverageAvailability: response.coverage.availability,
          reasonCodes: [...response.coverage.reason_codes],
        });
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || isAbortError(cause)) return;
        setFeed({ status: "unavailable", items: [], coverageAvailability: null, reasonCodes: [] });
      });
    return () => controller.abort();
  }, [revision]);
  return feed;
}
