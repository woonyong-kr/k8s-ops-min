export type ClusterConnectProvider = "aws" | "gcp" | "azure" | "onprem";
export type ClusterConnectState = "waiting" | "connected" | "expired";
export type ClusterConnectStage =
  | "token_issued"
  | "awaiting_install"
  | "agent_connected"
  | "snapshot_received"
  | "ready"
  | "expired"
  | "error";

export interface ClusterConnectReceipt {
  clusterId: string;
  installCommand: string;
  powershellInstallCommand: string;
  expiresAt: string;
}

export interface ClusterConnectionSnapshot {
  status: ClusterConnectState;
  stage: ClusterConnectStage;
  agentVersion: string | null;
  lastSeenAt: string | null;
}

export type ClustersFailureCode =
  | "unauthorized"
  | "forbidden"
  | "offline"
  | "invalid-response"
  | "conflict"
  | "error";

export class ClustersPortFailure extends Error {
  readonly code: ClustersFailureCode;

  constructor(code: ClustersFailureCode) {
    super(`Clusters port failed: ${code}`);
    this.name = "ClustersPortFailure";
    this.code = code;
  }
}

export interface ClustersPort {
  connect(
    input: { name: string; provider?: ClusterConnectProvider },
    signal?: AbortSignal,
  ): Promise<ClusterConnectReceipt>;
  loadConnection(
    clusterId: string,
    signal?: AbortSignal,
  ): Promise<ClusterConnectionSnapshot>;
  reissue(clusterId: string, signal?: AbortSignal): Promise<ClusterConnectReceipt>;
}

export interface ClusterDisconnectPort {
  disconnect(clusterId: string, signal?: AbortSignal): Promise<ClusterDisconnectReceipt>;
  loadDisconnect(commandId: string, signal?: AbortSignal): Promise<ClusterDisconnectProgress>;
}

export interface ClusterDisconnectReceipt {
  status: "uninstalling" | "cleanup-required" | "disconnected";
  stage: "agent_cleanup_queued" | "agent_cleanup_pending" | "registration_revoked" | "purged";
  commandId: string | null;
  uninstallCommand: string | null;
  cleanupVerified: boolean;
  cleanupResources: string[];
  residualResources: string[];
  failureReason: string | null;
}

export interface ClusterDisconnectProgress {
  status: "queued" | "leased" | "running" | "completed" | "failed";
  cleanupCompleted: boolean;
  cleanupResources: string[];
  residualResources: string[];
  failureReason: string | null;
}
