import {
  createContext,
  useCallback,
  useContext,
  useEffect,
  useMemo,
  useState,
  type ReactNode,
} from "react";

import { listClusters } from "../api/clusters";

type DevpreviewClusterList = Awaited<ReturnType<typeof listClusters>>;
type DevpreviewClusterSummary = DevpreviewClusterList["clusters"][number];

// UI-PHASE2-001: fixture 모드가 완전히 제거되어 소스는 항상 실제 계약("live")이다.
export type DevpreviewDataSource = "live";
export type DevpreviewDataStatus = "loading" | "ready" | "error";

export interface DevpreviewCluster {
  id: string;
  workspaceId: string;
  name: string;
  displayName: string;
  environment: string;
  provider: NonNullable<DevpreviewClusterSummary["provider"]>;
  connectionStatus: string;
  connectionStage: DevpreviewClusterSummary["connection_stage"] | null;
  observationMode: NonNullable<DevpreviewClusterSummary["observation_mode"]>;
  lastObservedAt: string | null;
  kubernetesVersion: string | null;
  nodeCount: number | null;
  podCount: number | null;
  namespaceCount: number | null;
  incidentCount: number | null;
  role: "management" | "target";
  readOnly: boolean;
}

export interface DevpreviewContractState {
  source: DevpreviewDataSource;
  status: DevpreviewDataStatus;
  workspaceId: string | null;
  clusters: DevpreviewCluster[];
  error: string | null;
  refresh(): void;
}

const EMPTY_STATE: DevpreviewContractState = {
  source: "live",
  status: "loading",
  workspaceId: null,
  clusters: [],
  error: null,
  refresh: () => undefined,
};

const DevpreviewContractContext = createContext<DevpreviewContractState>(EMPTY_STATE);

export function DevpreviewContractProvider({ children }: { children: ReactNode }) {
  const [revision, setRevision] = useState(0);
  const [status, setStatus] = useState<DevpreviewDataStatus>("loading");
  const [clusters, setClusters] = useState<DevpreviewCluster[]>([]);
  const [error, setError] = useState<string | null>(null);
  const refresh = useCallback(() => {
    setStatus("loading");
    setError(null);
    setRevision((value) => value + 1);
  }, []);

  useEffect(() => {
    const controller = new AbortController();

    // UI-PHASE2-001 §6: fixture-fallback 제거 — 항상 실제 계약(/api/clusters)만 조회.
    const request = listClusters({}, controller.signal);

    void request.then((response) => {
      if (controller.signal.aborted) return;
      setClusters(response.clusters.map(projectCluster));
      setStatus("ready");
    }).catch((cause: unknown) => {
      if (controller.signal.aborted) return;
      setClusters([]);
      setError(contractErrorMessage(cause));
      setStatus("error");
    });

    return () => controller.abort();
  }, [revision]);

  const workspaceId = clusters[0]?.workspaceId ?? null;
  const value = useMemo<DevpreviewContractState>(() => ({
    source: "live",
    status,
    workspaceId,
    clusters,
    error,
    refresh,
  }), [clusters, error, refresh, status, workspaceId]);

  return (
    <DevpreviewContractContext.Provider value={value}>
      {children}
    </DevpreviewContractContext.Provider>
  );
}

export function useDevpreviewContracts(): DevpreviewContractState {
  return useContext(DevpreviewContractContext);
}

export function projectCluster(cluster: DevpreviewClusterSummary): DevpreviewCluster {
  const configuredName = cluster.settings.name;
  const providerConfig = cluster.settings.provider_config;
  const eksClusterName = isRecord(providerConfig)
    ? providerConfig.eks_cluster_name
    : null;
  const configuredRole = cluster.settings.cluster_role;
  const role = configuredRole === "management" || cluster.environment === "management"
    ? "management"
    : "target";

  return {
    id: cluster.cluster_id,
    workspaceId: cluster.workspace_id,
    name: cluster.name,
    // EKS 화면의 식별자는 AWS의 실제 cluster name과 반드시 같아야 한다.
    // 등록 시 붙인 별칭/한글 이름을 우선하면 같은 클러스터가 다른 대상으로
    // 보이거나 중복처럼 보이므로, EKS는 provider_config의 원본 이름을 사용한다.
    displayName: cluster.provider === "eks"
      ? textOrNull(eksClusterName) ?? cluster.name
      : textOrNull(configuredName) ?? cluster.name,
    environment: cluster.environment,
    provider: cluster.provider ?? "unknown",
    connectionStatus: cluster.connection_status,
    connectionStage: cluster.connection_stage ?? null,
    observationMode: cluster.observation_mode ?? "agent",
    lastObservedAt: cluster.last_agent_seen_at ?? cluster.last_seen_at ?? null,
    kubernetesVersion: cluster.kubernetes_version ?? null,
    nodeCount: cluster.node_count,
    podCount: cluster.pod_count,
    namespaceCount: cluster.namespace_count ?? null,
    incidentCount: cluster.incident_count,
    role,
    readOnly: role === "management",
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function textOrNull(value: unknown): string | null {
  return typeof value === "string" && value.trim() ? value.trim() : null;
}

function contractErrorMessage(cause: unknown): string {
  if (cause instanceof Error && cause.message.trim()) return cause.message;
  return "계약 데이터를 불러오지 못했습니다.";
}
