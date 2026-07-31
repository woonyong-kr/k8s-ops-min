import { useEffect, useState } from "react";

import { getApplicationDrift, getApplicationOverview } from "../api/application-catalog";
import type {
  ApplicationDetailEndpointItem,
  ApplicationDriftEndpoint,
} from "../api/application-catalog-schemas";
import { listApplicationDeployments } from "../api/applications";
import { getChangeTimeline } from "../api/change-timeline";
import type { ChangeTimelineEndpoint } from "../api/change-timeline-schemas";
import { getGitOpsApplicationDetail } from "../api/gitops-application-detail";
import type { GitOpsApplicationDetailEndpoint } from "../api/gitops-application-detail-schemas";
import { getHelmRelease } from "../api/helm-releases";
import type { HelmReleaseDetailEndpoint } from "../api/helm-releases-schemas";
import { useVisibleRefreshClock } from "../shared/data/useVisibleRefreshClock";
import { operationalMessageLabel } from "./statusLabel";
import type { DeployFeedStatus } from "./deployFeed";

// 배포 상세 패널 전용 라이브 어댑터 — UI-PHASE2-001 원칙 유지.
// 모든 값은 실제 계약 응답에서만 파생하고, 실패한 섹션은 정직한 unavailable로
// 렌더한다(다른 섹션의 성공을 막지 않는다). 어떤 값도 합성하지 않는다.
// 갱신: 패널이 열려 있고 탭이 보이는 동안만 폴링한다(useVisibleRefreshClock).

/** 패널이 열려 있는 동안의 상세 폴링 주기 — 목록(15s)보다 촘촘한 관측. */
export const APPLICATION_DETAIL_POLL_MS = 10_000;
/** Helm 상세는 서버가 refresh_after_seconds로 주기를 지시한다(클램프 범위). */
export const HELM_DETAIL_MIN_POLL_MS = 5_000;
export const HELM_DETAIL_MAX_POLL_MS = 60_000;
export const HELM_DETAIL_FALLBACK_POLL_MS = 15_000;

export interface DetailSection<T> {
  status: DeployFeedStatus;
  data: T | null;
}

export interface ApplicationDetailFeed {
  /** GET /api/applications/{id} — 검증된 애플리케이션 개요·범위·활동·토폴로지. */
  record: DetailSection<ApplicationDetailEndpointItem>;
  /** GET /api/applications/{id}/deployments — 환경 바인딩 목록. */
  deployments: DetailSection<Record<string, unknown>[]>;
  /** GET /api/gitops/applications/{id} — GitOps 스코프·소스·capability. */
  gitops: DetailSection<GitOpsApplicationDetailEndpoint["application"]>;
  /** GET /api/applications/{id}/drift — 선언/라이브 드리프트 증거. */
  drift: DetailSection<ApplicationDriftEndpoint>;
}

const LOADING_SECTION: DetailSection<never> = { status: "loading", data: null };
const EMPTY_APPLICATION_DETAIL: ApplicationDetailFeed = {
  record: LOADING_SECTION,
  deployments: LOADING_SECTION,
  gitops: LOADING_SECTION,
  drift: LOADING_SECTION,
};

function settledSection<R, T>(
  result: PromiseSettledResult<R>,
  project: (value: R) => T,
): DetailSection<T> {
  if (result.status === "fulfilled") return { status: "ready", data: project(result.value) };
  return { status: "unavailable", data: null };
}

/**
 * 한 애플리케이션의 상세 증거 4종을 병렬로 읽는다. 섹션별 독립 status라서
 * 아직 연동되지 않은 계약(예: drift)이 실패해도 나머지는 정상 표시된다.
 */
export function useApplicationDetail(applicationId: string | null): ApplicationDetailFeed {
  const { revision } = useVisibleRefreshClock(applicationId !== null, APPLICATION_DETAIL_POLL_MS);
  const [state, setState] = useState<{ key: string; feed: ApplicationDetailFeed }>({
    key: "",
    feed: EMPTY_APPLICATION_DETAIL,
  });

  useEffect(() => {
    if (applicationId === null) return;
    const controller = new AbortController();
    void Promise.allSettled([
      getApplicationOverview(applicationId, controller.signal),
      listApplicationDeployments(applicationId, { signal: controller.signal }),
      getGitOpsApplicationDetail(applicationId, controller.signal),
      getApplicationDrift(applicationId, controller.signal),
    ] as const).then(([record, deployments, gitops, drift]) => {
      if (controller.signal.aborted) return;
      setState({
        key: applicationId,
        feed: {
          record: settledSection(record, (response) => response.application),
          deployments: settledSection(deployments, (response) => response.deployments),
          gitops: settledSection(gitops, (response) => response.application),
          drift: settledSection(drift, (response) => response),
        },
      });
    });
    return () => controller.abort();
  }, [applicationId, revision]);

  if (applicationId === null || state.key !== applicationId) return EMPTY_APPLICATION_DETAIL;
  return state.feed;
}

// ── Helm 릴리스 상세 ─────────────────────────────────────────────────────────

export interface HelmReleaseIdentity {
  clusterId: string;
  /** Helm 스토리지 네임스페이스 — 상세 경로의 정본 식별자. */
  storageNamespace: string;
  name: string;
}

export interface HelmReleaseDetailFeed {
  status: DeployFeedStatus;
  detail: HelmReleaseDetailEndpoint["detail"] | null;
}

function clampedHelmPollMs(refreshAfterSeconds: number): number {
  if (!Number.isFinite(refreshAfterSeconds) || refreshAfterSeconds <= 0) {
    return HELM_DETAIL_FALLBACK_POLL_MS;
  }
  return Math.min(
    HELM_DETAIL_MAX_POLL_MS,
    Math.max(HELM_DETAIL_MIN_POLL_MS, Math.round(refreshAfterSeconds * 1000)),
  );
}

/**
 * GET /api/helm/releases/{namespace}/{release_name} 한 번으로 릴리스·히스토리·
 * 소유 리소스·커맨드 가용성을 함께 읽는다. 폴링 주기는 서버 지시값
 * (refresh_after_seconds)을 그대로 따른다 — 임의 주기를 지어내지 않는다.
 */
export function useHelmReleaseDetail(target: HelmReleaseIdentity | null): HelmReleaseDetailFeed {
  const key = target === null
    ? ""
    : [target.clusterId, target.storageNamespace, target.name].join("\u0000");
  const [pollMs, setPollMs] = useState(HELM_DETAIL_FALLBACK_POLL_MS);
  const { revision } = useVisibleRefreshClock(key !== "", pollMs);
  const [state, setState] = useState<{ key: string; feed: HelmReleaseDetailFeed }>({
    key: "",
    feed: { status: "loading", detail: null },
  });

  useEffect(() => {
    if (key === "") return;
    const [clusterId, storageNamespace, releaseName] = key.split("\u0000");
    const controller = new AbortController();
    void getHelmRelease(
      { clusterId, namespace: storageNamespace, releaseName },
      controller.signal,
    ).then((response) => {
      if (controller.signal.aborted) return;
      setPollMs(clampedHelmPollMs(response.refresh_after_seconds));
      setState({ key, feed: { status: "ready", detail: response.detail } });
    }).catch(() => {
      if (controller.signal.aborted) return;
      setState({ key, feed: { status: "unavailable", detail: null } });
    });
    return () => controller.abort();
  }, [key, revision]);

  if (key === "" || state.key !== key) return { status: "loading", detail: null };
  return state.feed;
}

// ── 애플리케이션 스코프 변경 이벤트 ──────────────────────────────────────────

/** 이벤트 섹션 관측 창(직전 24시간)과 폴링 주기. */
export const APPLICATION_EVENTS_WINDOW_MS = 24 * 60 * 60 * 1000;
export const APPLICATION_EVENTS_BUCKET_MS = 60 * 60 * 1000;

export interface ApplicationChangeEventView {
  id: string;
  kind: ChangeTimelineEndpoint["events"][number]["kind"];
  occurredMs: number;
  /** 서버 원문 — 증거 보존용. */
  rawTitle: string;
  /** 한국어 표시 문자열. */
  title: string;
  severity: ChangeTimelineEndpoint["events"][number]["severity"];
}

export interface ApplicationChangeEventsFeed {
  status: DeployFeedStatus;
  events: ApplicationChangeEventView[];
}

/**
 * GET /api/changes 를 applications=<id> 로 스코프해 직전 24시간의 실제 변경
 * 이벤트(배포·GitOps·인시던트·인벤토리)를 읽는다. 창은 매 조회 시점 기준으로
 * 다시 계산해 패널이 열려 있는 동안 최신 이벤트가 계속 유입된다.
 */
export function useApplicationChangeEvents(applicationId: string | null): ApplicationChangeEventsFeed {
  const { revision } = useVisibleRefreshClock(applicationId !== null, APPLICATION_DETAIL_POLL_MS);
  const [state, setState] = useState<{ key: string; feed: ApplicationChangeEventsFeed }>({
    key: "",
    feed: { status: "loading", events: [] },
  });

  useEffect(() => {
    if (applicationId === null) return;
    const controller = new AbortController();
    const toMs = Date.now();
    void getChangeTimeline({
      fromMs: toMs - APPLICATION_EVENTS_WINDOW_MS,
      toMs,
      bucketMs: APPLICATION_EVENTS_BUCKET_MS,
      applications: [applicationId],
    }, controller.signal).then((response) => {
      if (controller.signal.aborted) return;
      const events = response.events
        .map((event) => ({
          id: event.id,
          kind: event.kind,
          occurredMs: event.occurredMs,
          rawTitle: event.title,
          title: operationalMessageLabel(event.title),
          severity: event.severity,
        }))
        .sort((left, right) => right.occurredMs - left.occurredMs);
      setState({ key: applicationId, feed: { status: "ready", events } });
    }).catch(() => {
      if (controller.signal.aborted) return;
      setState({ key: applicationId, feed: { status: "unavailable", events: [] } });
    });
    return () => controller.abort();
  }, [applicationId, revision]);

  if (applicationId === null || state.key !== applicationId) return { status: "loading", events: [] };
  return state.feed;
}
