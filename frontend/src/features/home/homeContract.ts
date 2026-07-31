export type HomeHealthTone =
  | "healthy"
  | "warning"
  | "critical"
  | "stale"
  | "unknown";

export type HomeConnectionState = "online" | "stale" | "pending" | "offline" | "unknown";
export type HomeRegistrationState = "active" | "pending" | "expired" | "unknown";
export type HomeClusterProvider = "eks" | "gke" | "aks" | "onprem" | "kind" | "unknown";
export type HomeConnectionStage =
  | "token_issued"
  | "awaiting_install"
  | "agent_connected"
  | "snapshot_received"
  | "ready"
  | "expired"
  | "error";
export type HomeCollectionCompleteness = "unknown";
export type HomeIdentityStability = "ephemeral";

export interface HomeClusterChoice {
  id: string;
  workspaceId: string;
  name: string;
  environment: string;
  provider: HomeClusterProvider;
  connectionStage: HomeConnectionStage | null;
  registrationState: HomeRegistrationState;
  connectionState: HomeConnectionState;
  lastObservedAt: string | null;
  nodeCount: number | null;
  podCount: number | null;
  incidentCount: number | null;
  serverCount?: number | null;
  appCount?: number | null;
  openIncidentCount?: number | null;
}

export interface HomeClusterChoices {
  completeness: HomeCollectionCompleteness;
  clusters: HomeClusterChoice[];
}

export interface HomeUsageSnapshot {
  observedAt: string | null;
  podsRunning: number;
  podsTotal: number;
  nodesReady: number;
  nodesTotal: number;
  restartCount: number;
  cpuPercent: number | null;
  memoryPercent: number | null;
}

export interface HomeWorkloadSummary {
  id: string;
  identityStability: HomeIdentityStability;
  name: string;
  kind: string;
  namespace: string | null;
  health: HomeHealthTone;
  ready: string;
  restartCount: number;
}

export interface HomeWarningSummary {
  id: string;
  identityStability: HomeIdentityStability;
  name: string;
  namespace: string | null;
  reason: string | null;
  message: string | null;
  involvedKind: string | null;
  involvedName: string | null;
  occurrenceCount: number;
  lastSeenAt: string | null;
}

export interface HomeIncidentSummary {
  id: string;
  /** Server incident identifier used only when a detail link is available. */
  incidentId: string | null;
  correlationId: string;
  symptom: string | null;
  rootCause: string | null;
  namespace: string | null;
  resourceKind: string | null;
  resourceName: string | null;
  status: string;
  createdAt: string | null;
}

export type HomeDataQualityWarningCode =
  | "usage-unavailable"
  | "workload-readiness-unavailable"
  | "incident-link-unavailable";

export interface HomeDataQualityWarning {
  code: HomeDataQualityWarningCode;
  section: "usage" | "workloads" | "incidents";
  /** Canonical row identity when the warning belongs to one row. */
  entityId: string | null;
}

export interface HomeClusterOverview {
  clusterId: string;
  name: string;
  health: HomeHealthTone;
  usage: HomeUsageSnapshot | null;
  workloads: HomeWorkloadSummary[];
  warnings: HomeWarningSummary[];
  incidents: HomeIncidentSummary[];
  dataQualityWarnings: HomeDataQualityWarning[];
}

export interface HomeNodeSummary {
  id: string;
  identityStability: HomeIdentityStability;
  name: string;
  ready: boolean;
  health: HomeHealthTone;
  podsRunning: number;
  podsCapacity: number;
  cpuPercent: number | null;
  memoryPercent: number | null;
  restartCount: number;
  conditions: string[];
}

export interface HomeNodeCollection {
  clusterId: string;
  completeness: HomeCollectionCompleteness;
  nodes: HomeNodeSummary[];
}

export interface HomePodReadiness {
  ready: number;
  total: number;
}

export interface HomePodOwner {
  kind: string;
  name: string;
}

export interface HomePodSummary {
  id: string;
  identityStability: HomeIdentityStability;
  name: string;
  namespace: string;
  phase: string;
  health: HomeHealthTone;
  readiness: HomePodReadiness;
  restartCount: number;
  owner: HomePodOwner | null;
  cpuMillicores: number | null;
  memoryMebibytes: number | null;
  incidentCorrelationId: string | null;
}

export interface HomePodCollection {
  clusterId: string;
  nodeName: string;
  completeness: HomeCollectionCompleteness;
  pods: HomePodSummary[];
}

export type HomeFailureCode =
  | "unauthorized"
  | "forbidden"
  | "offline"
  | "not-found"
  | "rate-limited"
  | "invalid-response"
  | "error";

export class HomePortFailure extends Error {
  readonly code: HomeFailureCode;
  readonly retryAfterSeconds: number | null;

  constructor(code: HomeFailureCode, retryAfterSeconds: number | null = null) {
    super(`Home port failed: ${code}`);
    this.name = "HomePortFailure";
    this.code = code;
    this.retryAfterSeconds = retryAfterSeconds;
  }
}

export interface HomePort {
  listClusterChoices(signal?: AbortSignal): Promise<HomeClusterChoices>;
  loadClusterOverview(clusterId: string, signal?: AbortSignal): Promise<HomeClusterOverview>;
  loadNodes(clusterId: string, signal?: AbortSignal): Promise<HomeNodeCollection>;
  loadNodePods(
    clusterId: string,
    nodeName: string,
    signal?: AbortSignal,
  ): Promise<HomePodCollection>;
}
