import { useEffect, useState } from "react";

import {
  connectCluster as connectClusterApi,
  getClusterConnectStatus,
  reissueClusterConnectCommand as reissueClusterConnectCommandApi,
} from "../api/cluster-connect";
import type {
  ClusterConnectProvider,
  ClusterConnectResponse,
  ClusterConnectStatusResponse,
} from "../api/cluster-connect-schemas";
import { getInventorySummary } from "../api/inventory-summary";
import { getClusterUsage } from "../api/metrics";
import {
  connectApplication as connectApplicationApi,
  previewApplicationConnection as previewApplicationConnectionApi,
  type ApplicationConnectInput,
  type ApplicationConnectPreviewInput,
} from "../api/applications";
import type {
  ConnectionPreview,
  ConnectionPreviewResource,
} from "../api/applications-schemas";

export type ConnectionPreviewView = ConnectionPreview;
export type ConnectionPreviewResourceView = ConnectionPreviewResource;
import { listClusters as listClustersApi, type ListClustersOptions } from "../api/clusters";
import type { ClusterSummary } from "../api/cluster-schemas";
import {
  listRepositoryBranches as listRepositoryBranchesApi,
  listRepositoryManifestCandidates as listRepositoryManifestCandidatesApi,
  probeRepository as probeRepositoryApi,
  validateRepositoryManifest as validateRepositoryManifestApi,
} from "../api/repository-discovery";
import type {
  RepositoryBranch,
  RepositoryBranchList,
  RepositoryManifestCandidate,
  RepositoryManifestCandidateList,
  RepositoryManifestValidation,
  RepositoryProbe,
} from "../api/repository-discovery-schemas";
import {
  getProviderCatalog,
  getProviderClusterDiscovery,
  preflightTargetRegistration,
  registerTarget,
  type TargetPreflightInput,
  type TargetRegisterInput,
} from "../api/cluster-registration";
import type {
  ProviderCatalog,
  ProviderClusterDiscovery,
  ProviderConfigField,
  TargetInstallResponse,
  TargetPreflightResponse,
} from "../api/cluster-registration-schemas";

// UI-PHASE2-001 · CON-01..CON-09 / §2 "Connect cluster" / §5 side-effects.
//
// Typed live adapters for the environment connect wizard. Rules mirror
// checksFeed.ts / sessionFeed.ts: a typed `status` union, no synchronous
// setState at the top of a useEffect (eslint `react-hooks/set-state-in-effect`),
// a scope change aborts the previous request, and NO backfill — an absent
// server capability renders honest `unavailable`, never a fabricated success.
//
// SAFETY (plan §5): this runs against a shared live backend. Only GET reads
// auto-run on mount here (catalog / discovery / connection status). Target
// registration is a real mutation that creates external state; the preflight
// and register helpers below are plain imperative functions and are NEVER
// invoked from an effect or a timer — the wizard calls them only from an
// explicit user button click. Secret agent tokens returned by registration are
// never persisted or logged by this module.

export type ConnectFeedStatus = "loading" | "ready" | "unavailable" | "error";

// ── Repository/application discovery (explicit-click reads/mutation) ────────
// Keep the view component behind the same authenticated domain adapter as the
// cluster wizard. Repository registration remains an explicit user mutation.

export type ClusterSummaryView = ClusterSummary;
export type RepositoryBranchView = RepositoryBranch;
export type RepositoryManifestCandidateView = RepositoryManifestCandidate;

/**
 * Explicit-click cluster registration boundary used by the demo shell.
 * Keeping the mutation here preserves the authenticated API composition rule:
 * view components never import the low-level product API directly.
 */
export type ClusterConnectResponseView = ClusterConnectResponse;

export function connectCluster(
  input: { name: string; provider?: ClusterConnectProvider },
  signal?: AbortSignal,
): Promise<ClusterConnectResponse> {
  return connectClusterApi(input, signal);
}

/** 설치 실패/만료 후 같은 등록에 새 토큰으로 설치 명령을 재발급한다(명령어 하나로 재설치). */
export function reissueClusterConnectCommand(
  clusterId: string,
  signal?: AbortSignal,
): Promise<ClusterConnectResponse> {
  return reissueClusterConnectCommandApi(clusterId, signal);
}

export function connectApplication(
  input: ApplicationConnectInput,
  signal?: AbortSignal,
) {
  return connectApplicationApi(input, signal);
}

export function previewApplicationConnection(
  input: ApplicationConnectPreviewInput,
  signal?: AbortSignal,
): Promise<ConnectionPreview> {
  return previewApplicationConnectionApi(input, signal);
}

export function listClusters(
  options: ListClustersOptions = {},
  signal?: AbortSignal,
) {
  return listClustersApi(options, signal);
}

export function probeRepository(
  repoRef: string,
  token?: string,
  signal?: AbortSignal,
  installationId?: string,
): Promise<RepositoryProbe> {
  return probeRepositoryApi(repoRef, token, signal, installationId);
}

export function listRepositoryBranches(
  repoRef: string,
  signal?: AbortSignal,
  installationId?: string,
): Promise<RepositoryBranchList> {
  return listRepositoryBranchesApi(repoRef, signal, installationId);
}

export function listRepositoryManifestCandidates(
  repoRef: string,
  branch: string,
  signal?: AbortSignal,
  installationId?: string,
): Promise<RepositoryManifestCandidateList> {
  return listRepositoryManifestCandidatesApi(repoRef, branch, signal, installationId);
}

export function validateRepositoryManifest(
  repoRef: string,
  branch: string,
  manifestPath: string,
  sourceType = "",
  signal?: AbortSignal,
  installationId?: string,
): Promise<RepositoryManifestValidation> {
  return validateRepositoryManifestApi(repoRef, branch, manifestPath, sourceType, signal, installationId);
}

// ── Provider availability (catalog + cluster discovery) ─────────────────────

export interface ProviderAvailability {
  key: string;
  label: string;
  available: boolean;
  unavailableReason: string | null;
}

export interface ClusterProvidersView {
  status: ConnectFeedStatus;
  /** Registration flows keyed by cloud provider (e.g. `eks`, `gke`, `aks`). */
  cloudProviders: Map<string, ProviderAvailability>;
  /** Source SCM providers (e.g. `github`) for the repository sub-wizard. */
  sourceProviders: ProviderAvailability[];
  defaultCloudProvider: string | null;
  defaultDeployProvider: string | null;
  /** Server-advertised default deploy provider for a given cloud provider. */
  deployProviderFor: (cloudProvider: string) => string | null;
  /** Server-advertised provider-specific registration fields. */
  providerConfigFieldsFor: (cloudProvider: string) => ProviderConfigField[];
}

const EMPTY_PROVIDERS: ClusterProvidersView = {
  status: "loading",
  cloudProviders: new Map(),
  sourceProviders: [],
  defaultCloudProvider: null,
  defaultDeployProvider: null,
  deployProviderFor: () => null,
  providerConfigFieldsFor: () => [],
};

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error
    && (error as { name?: unknown }).name === "AbortError";
}

function toClusterProvidersView(
  catalog: ProviderCatalog,
  discovery: ProviderClusterDiscovery,
): ClusterProvidersView {
  const cloudProviders = new Map<string, ProviderAvailability>();
  const deployByCloud = new Map<string, string>();
  const configFieldsByCloud = new Map<string, ProviderConfigField[]>();
  for (const provider of catalog.providers.cloud ?? []) {
    configFieldsByCloud.set(provider.key, provider.config_fields);
  }
  for (const flow of discovery.flows) {
    cloudProviders.set(flow.cloud_provider, {
      key: flow.cloud_provider,
      label: flow.label,
      available: flow.status === "available",
      unavailableReason: flow.unavailable_reason,
    });
    deployByCloud.set(flow.cloud_provider, flow.default_deploy_provider);
  }

  const sourceProviders: ProviderAvailability[] = (catalog.providers.source ?? []).map(
    (provider) => ({
      key: provider.key,
      label: provider.label,
      available: provider.status === "available",
      unavailableReason: provider.unavailable_reason,
    }),
  );

  return {
    status: cloudProviders.size > 0 ? "ready" : "unavailable",
    cloudProviders,
    sourceProviders,
    defaultCloudProvider: discovery.default_cloud_provider,
    defaultDeployProvider: discovery.default_deploy_provider,
    deployProviderFor: (cloudProvider: string) => deployByCloud.get(cloudProvider) ?? null,
    providerConfigFieldsFor: (cloudProvider: string) => (
      configFieldsByCloud.get(cloudProvider) ?? []
    ),
  };
}

/**
 * Loads the live provider catalog and cluster-discovery flows.
 * Both are read-only GETs, safe to run on mount.
 */
export function useClusterProviders(): ClusterProvidersView {
  const [view, setView] = useState<ClusterProvidersView>(EMPTY_PROVIDERS);
  useEffect(() => {
    const controller = new AbortController();
    void Promise.all([
      getProviderCatalog(controller.signal),
      getProviderClusterDiscovery(controller.signal),
    ])
      .then(([catalog, discovery]) => {
        if (controller.signal.aborted) return;
        setView(toClusterProvidersView(catalog, discovery));
      })
      .catch((cause: unknown) => {
        if (controller.signal.aborted || isAbortError(cause)) return;
        setView((prev) => ({ ...prev, status: "error" }));
      });
    return () => controller.abort();
  }, []);
  return view;
}

// ── Connection status polling ───────────────────────────────────────────────

export type ConnectionPollStatus = "idle" | "loading" | "ready" | "error";

export interface ConnectionStatusView {
  status: ConnectionPollStatus;
  /** `waiting` | `connected` | `expired` once a status has been observed. */
  connection: ClusterConnectStatusResponse["status"] | null;
  agentVersion: string | null;
  connectedAt: string | null;
  failureReason: string | null;
}

type ConnectionStatusState = ConnectionStatusView & {
  clusterId: string | null;
};

const IDLE_CONNECTION: ConnectionStatusView = {
  status: "idle",
  connection: null,
  agentVersion: null,
  connectedAt: null,
  failureReason: null,
};

const IDLE_CONNECTION_STATE: ConnectionStatusState = {
  ...IDLE_CONNECTION,
  clusterId: null,
};

export const CONNECTION_STATUS_POLL_MS = 3_000;

/**
 * Polls the real connection status for a registered cluster.
 *
 * When `clusterId` is `null` (no registration yet) this stays idle and issues
 * no request — completion is never faked by a timer. Polling stops once the
 * server reports a terminal `connected` / `expired` status.
 */
export function useClusterConnectionStatus(clusterId: string | null): ConnectionStatusView {
  const [view, setView] = useState<ConnectionStatusState>(IDLE_CONNECTION_STATE);

  useEffect(() => {
    if (!clusterId) return;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let cancelled = false;
    // 마지막 관측이 아직 waiting이라 추가 폴링이 필요한지 여부. 백그라운드 탭에서는
    // 폴링을 멈추고(visibility-gate), 화면이 다시 보이면 즉시 1회 재조회해 이어간다.
    let waiting = true;
    let requestInFlight = false;
    let requestController: AbortController | null = null;
    let requestRevision = 0;

    const schedule = () => {
      if (timer !== null || document.hidden) return;
      timer = setTimeout(() => { timer = null; void poll(); }, CONNECTION_STATUS_POLL_MS);
    };
    const poll = async () => {
      if (cancelled || document.hidden || requestInFlight) return;
      requestInFlight = true;
      const revision = ++requestRevision;
      const controller = new AbortController();
      requestController = controller;
      await getClusterConnectStatus(clusterId, controller.signal)
        .then((response) => {
          if (cancelled || controller.signal.aborted || revision !== requestRevision) return;
          setView({
            clusterId,
            status: "ready",
            connection: response.status,
            agentVersion: response.agent_version,
            connectedAt: response.connected_at,
            failureReason: response.failure_reason ?? null,
          });
          waiting = response.status === "waiting";
          if (waiting) schedule();
        })
        .catch((cause: unknown) => {
          if (cancelled || controller.signal.aborted || revision !== requestRevision || isAbortError(cause)) return;
          setView({ ...IDLE_CONNECTION, clusterId, status: "error" });
          // 일시 네트워크 오류 한 번에 폴링이 영구 정지하지 않도록, 아직 대기 중이면
          // 다음 주기를 다시 예약한다. visibility 토글에만 의존하지 않는다.
          if (waiting) schedule();
        });
      if (revision === requestRevision) {
        requestInFlight = false;
        requestController = null;
      }
    };
    const onVisibility = () => {
      if (document.hidden) {
        if (timer !== null) { clearTimeout(timer); timer = null; }
        requestRevision += 1;
        requestController?.abort();
        requestController = null;
        requestInFlight = false;
        return;
      }
      if (waiting && timer === null) void poll();
    };
    document.addEventListener("visibilitychange", onVisibility);
    void poll();

    return () => {
      cancelled = true;
      if (timer) clearTimeout(timer);
      document.removeEventListener("visibilitychange", onVisibility);
      requestRevision += 1;
      requestController?.abort();
    };
  }, [clusterId]);

  return view.clusterId === clusterId ? view : IDLE_CONNECTION;
}

export type ActivationEvidenceStatus = "waiting" | "ready" | "error";
export type ActivationReadinessStatus = "idle" | "waiting" | "ready" | "error";

export interface ClusterActivationReadinessView {
  status: ActivationReadinessStatus;
  heartbeat: ActivationEvidenceStatus;
  inventory: ActivationEvidenceStatus;
  metrics: ActivationEvidenceStatus;
}

const IDLE_ACTIVATION: ClusterActivationReadinessView = {
  status: "idle",
  heartbeat: "waiting",
  inventory: "waiting",
  metrics: "waiting",
};

interface ObservedActivation extends ClusterActivationReadinessView {
  clusterId: string;
}

/**
 * Waits for three independent, server-observed readiness signals after target
 * registration. Heartbeat plus a real inventory snapshot is sufficient to
 * finish registration. Telemetry warms up asynchronously after navigation and
 * must never trap the connection wizard indefinitely.
 */
export function useClusterActivationReadiness(
  clusterId: string | null,
  connection: ClusterConnectStatusResponse["status"] | null,
): ClusterActivationReadinessView {
  const [observed, setObserved] = useState<ObservedActivation | null>(null);

  useEffect(() => {
    if (!clusterId || connection !== "connected") return;
    let cancelled = false;
    let timer: ReturnType<typeof setTimeout> | null = null;
    let controller: AbortController | null = null;

    const schedule = () => {
      if (cancelled || timer !== null || document.hidden) return;
      timer = setTimeout(() => {
        timer = null;
        void poll();
      }, CONNECTION_STATUS_POLL_MS);
    };
    const poll = async () => {
      if (cancelled || document.hidden || controller !== null) return;
      controller = new AbortController();
      const signal = controller.signal;
      const [inventoryResult, metricsResult] = await Promise.allSettled([
        getInventorySummary(clusterId, signal),
        getClusterUsage(clusterId, { limit: 1 }, signal),
      ]);
      controller = null;
      if (cancelled || signal.aborted) return;

      const inventory = inventoryResult.status === "rejected"
        ? "error"
        : inventoryResult.value.latest_snapshot !== null
          && inventoryResult.value.counts_evidence.completeness !== "unavailable"
          ? "ready"
          : "waiting";
      const metrics = metricsResult.status === "rejected"
        ? "error"
        : metricsResult.value.samples.some((sample) => (
          sample.sampled_at !== null
          && (sample.usage.cpu_pct !== null && sample.usage.cpu_pct !== undefined
            || sample.usage.mem_pct !== null && sample.usage.mem_pct !== undefined)
        ))
          ? "ready"
          : "waiting";
      const status = inventory === "ready"
        ? "ready"
        : inventory === "error"
          ? "error"
          : "waiting";
      setObserved({
        clusterId,
        status,
        heartbeat: "ready",
        inventory,
        metrics,
      });
      if (status !== "ready") schedule();
    };
    const onVisibility = () => {
      if (document.hidden) {
        if (timer !== null) clearTimeout(timer);
        timer = null;
        controller?.abort();
        controller = null;
        return;
      }
      void poll();
    };

    document.addEventListener("visibilitychange", onVisibility);
    void poll();
    return () => {
      cancelled = true;
      if (timer !== null) clearTimeout(timer);
      controller?.abort();
      document.removeEventListener("visibilitychange", onVisibility);
    };
  }, [clusterId, connection]);

  if (!clusterId) return IDLE_ACTIVATION;
  if (connection !== "connected") {
    // expired 와 failed 는 모두 종결 실패 상태다. 활성화 준비도를 error 로 두어
    // 어떤 소비자도 실패한 연결을 "대기 중"으로 오인하지 않게 한다.
    const terminalError = connection === "expired" || connection === "failed";
    return {
      status: terminalError ? "error" : "waiting",
      heartbeat: terminalError ? "error" : "waiting",
      inventory: "waiting",
      metrics: "waiting",
    };
  }
  if (observed?.clusterId !== clusterId) {
    return { ...IDLE_ACTIVATION, status: "waiting", heartbeat: "ready" };
  }
  return {
    status: observed.status,
    heartbeat: observed.heartbeat,
    inventory: observed.inventory,
    metrics: observed.metrics,
  };
}

// ── Target registration (explicit-click mutations only) ─────────────────────

export interface ClusterTargetFields {
  /** Wizard platform id mapped to a live discovery cloud provider. */
  cloudProvider: string;
  deployProvider: string;
  name: string;
  environment: string;
  /** Provider-specific values advertised by the provider catalog. */
  providerConfig: Record<string, unknown>;
}

function slugId(name: string): string {
  return name.trim().toLowerCase().replace(/[^a-z0-9-]+/g, "-").replace(/^-+|-+$/g, "");
}

function normalizedProviderConfig(fields: ClusterTargetFields): Record<string, unknown> {
  return Object.fromEntries(
    Object.entries(fields.providerConfig).flatMap(([key, value]) => {
      if (typeof value !== "string") return [[key, value]];
      const normalized = value.trim();
      return normalized === "" ? [] : [[key, normalized]];
    }),
  );
}

function baseSelection(fields: ClusterTargetFields) {
  return {
    clusterRole: "target" as const,
    // The agent reports back to this origin; a preview build cannot know the
    // management cluster's internal URL, so the server validates/defaults it.
    managementBaseUrl: window.location.origin,
    // Agent image is a management-cluster deployment value the browser does not
    // hold; left blank so the server reports its own default or a real error.
    image: "",
    // Non-destructive: register the target record and issue a bootstrap
    // command WITHOUT applying anything to a live cluster.
    apply: false,
    kubeContext: null,
    cloudProvider: fields.cloudProvider,
    deployProvider: fields.deployProvider,
    providerConfig: normalizedProviderConfig(fields),
  };
}

/**
 * Runs a real, non-mutating preflight validation. Safe to call on an explicit
 * click; issues no agent credential and creates no target.
 */
export function preflightClusterTarget(
  fields: ClusterTargetFields,
  signal?: AbortSignal,
): Promise<TargetPreflightResponse> {
  return preflightTargetRegistration(buildClusterTargetPreflightInput(fields), signal);
}

/** Builds the complete immutable request used by both the UI and adapter tests. */
export function buildClusterTargetPreflightInput(
  fields: ClusterTargetFields,
): TargetPreflightInput {
  return {
    ...baseSelection(fields),
    clusterId: slugId(fields.name),
    name: fields.name,
    environment: fields.environment,
  };
}

/**
 * Registers a target. THIS IS A REAL MUTATION — the wizard must call it only
 * from an explicit user confirmation, never from an effect or timer. The
 * returned receipt carries a one-time `agent_token`; keep it in ephemeral
 * component state only and never persist or log it.
 */
export function registerClusterTarget(
  fields: ClusterTargetFields,
  signal?: AbortSignal,
): Promise<TargetInstallResponse> {
  return registerTarget(buildClusterTargetRegisterInput(fields), signal);
}

/** Keeps registration identity and provider defaults identical to preflight. */
export function buildClusterTargetRegisterInput(
  fields: ClusterTargetFields,
): TargetRegisterInput {
  return {
    ...baseSelection(fields),
    // Keep the mutation bound to the exact identity that passed preflight.
    // Letting the server generate a suffixed id here would validate one target
    // and register another, defeating duplicate and policy checks.
    clusterId: slugId(fields.name),
    name: fields.name,
    environment: fields.environment,
    // 위저드 등록도 관리 클러스터 에이전트(deploy/management/target-agent.yaml)와
    // 동일한 5초 수집 주기를 쓴다. 생략하면 서버 기본 30초가 매니페스트에 박혀
    // freshness 창(45초) 안에서도 갱신이 30초에 한 번으로 보인다.
    evidenceIntervalSeconds: 5,
  };
}

/** Maps a wizard platform id to its live cluster-discovery cloud provider key. */
export const PLATFORM_CLOUD_PROVIDER: Record<string, string> = {
  aws: "eks",
  gcp: "gke",
  azure: "aks",
  docker: "existing-k8s",
};

// 클러스터 등록 도메인의 api 유틸/타입도 이 어댑터 경계를 통해서만 노출한다.
export { isApiError } from "../api/client";
export type {
  ProviderConfigField,
  TargetInstallResponse,
  TargetPreflightResponse,
} from "../api/cluster-registration-schemas";
