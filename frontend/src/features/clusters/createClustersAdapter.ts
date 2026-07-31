import {
  ClustersPortFailure,
  type ClusterConnectProvider,
  type ClusterConnectReceipt,
  type ClusterConnectStage,
  type ClusterDisconnectProgress,
  type ClusterDisconnectReceipt,
  type ClusterDisconnectPort,
  type ClusterConnectionSnapshot,
  type ClustersFailureCode,
  type ClustersPort,
} from "./clustersContract";

interface ClusterConnectWire {
  cluster_id: string;
  install_command: string;
  powershell_install_command: string;
  expires_at: string;
}

interface ClusterConnectionWire {
  cluster_id: string;
  connection_status: string;
  connection_stage?: ClusterConnectStage;
  last_agent_id: string | null;
  last_seen_at: string | null;
  agents: Array<{ details: Record<string, unknown> }>;
  connect_timeout_seconds: number | null;
  connect_expires_at: string | null;
}

interface ClusterUnregisterWire {
  cluster_id: string;
  status: "uninstalling" | "cleanup_required" | "disconnected" | "purged";
  stage: "agent_cleanup_queued" | "agent_cleanup_pending" | "registration_revoked" | "purged";
  command_id: string | null;
  command_status_path: string | null;
  uninstall_command: string | null;
  cleanup_verified: boolean;
  resources: string[];
  residual_resources: string[];
  failure_reason: string | null;
}

interface CommandStatusWire {
  status: "queued" | "leased" | "running" | "completed" | "failed";
  result: Record<string, unknown>;
}

export interface ClustersEndpointDependencies {
  connectCluster(
    input: { name: string; provider?: ClusterConnectProvider },
    signal?: AbortSignal,
  ): Promise<ClusterConnectWire>;
  getClusterConnectionStatus(
    clusterId: string,
    signal?: AbortSignal,
  ): Promise<ClusterConnectionWire>;
  reissueClusterConnectCommand(
    clusterId: string,
    signal?: AbortSignal,
  ): Promise<ClusterConnectWire>;
  unregisterCluster(
    clusterId: string,
    options?: { manualCleanupAttested?: boolean },
    signal?: AbortSignal,
  ): Promise<ClusterUnregisterWire>;
  getCommandStatus(commandId: string, signal?: AbortSignal): Promise<CommandStatusWire>;
}

export function createClustersAdapter(
  endpoints: ClustersEndpointDependencies,
): ClustersPort & ClusterDisconnectPort {
  return {
    async connect(input, signal) {
      return withFailure(async () => connectReceipt(await endpoints.connectCluster(input, signal)));
    },
    async loadConnection(clusterId, signal) {
      return withFailure(async () => {
        const response = await endpoints.getClusterConnectionStatus(clusterId, signal);
        canonicalNullableTimestamp(response.last_seen_at);
        const stage = response.connection_stage ?? "awaiting_install";
        const agentVersion = response.agents
          .map((agent) => agent.details.version)
          .find((version): version is string => typeof version === "string") ?? null;
        return {
          status: stage === "ready"
            ? "connected"
            : stage === "expired" || response.connection_status === "install_expired"
              ? "expired"
              : "waiting",
          stage,
          agentVersion,
          lastSeenAt: response.last_seen_at,
        } satisfies ClusterConnectionSnapshot;
      });
    },
    async reissue(clusterId, signal) {
      return withFailure(async () => connectReceipt(
        await endpoints.reissueClusterConnectCommand(clusterId, signal),
      ));
    },
    async disconnect(clusterId, signal) {
      return withFailure(async () => disconnectReceipt(
        await endpoints.unregisterCluster(clusterId, {}, signal),
      ));
    },
    async loadDisconnect(commandId, signal) {
      return withFailure(async () => disconnectProgress(
        await endpoints.getCommandStatus(commandId, signal),
      ));
    },
  };
}

function connectReceipt(response: ClusterConnectWire): ClusterConnectReceipt {
  const command = response.install_command.trim();
  const powershellCommand = response.powershell_install_command.trim();
  if (
    !response.cluster_id.trim()
    || !command
    || command.includes("\n")
    || !powershellCommand
    || powershellCommand.includes("\n")
  ) invalidResponse();
  canonicalTimestamp(response.expires_at);
  return {
    clusterId: response.cluster_id,
    installCommand: command,
    powershellInstallCommand: powershellCommand,
    expiresAt: response.expires_at,
  };
}

function disconnectReceipt(response: ClusterUnregisterWire): ClusterDisconnectReceipt {
  return {
    status: response.status === "cleanup_required"
      ? "cleanup-required"
      : response.status === "uninstalling"
        ? "uninstalling"
        : "disconnected",
    stage: response.stage,
    commandId: response.command_id,
    uninstallCommand: response.uninstall_command,
    cleanupVerified: response.cleanup_verified,
    cleanupResources: response.resources,
    residualResources: response.residual_resources,
    failureReason: response.failure_reason,
  };
}

function disconnectProgress(response: CommandStatusWire): ClusterDisconnectProgress {
  const result = response.result;
  const failureReason = typeof result.failure_reason === "string"
    ? result.failure_reason
    : typeof result.message === "string"
      ? result.message
      : null;
  return {
    status: response.status,
    cleanupCompleted: result.cleanup_completed === true,
    cleanupResources: stringArray(result.cleanup_resources),
    residualResources: stringArray(result.residual_resources),
    failureReason,
  };
}

function stringArray(value: unknown): string[] {
  return Array.isArray(value) && value.every((item) => typeof item === "string") ? value : [];
}

async function withFailure<T>(operation: () => Promise<T>): Promise<T> {
  try {
    return await operation();
  } catch (error) {
    if (isAbortError(error) || error instanceof ClustersPortFailure) throw error;
    throw new ClustersPortFailure(failureCode(error));
  }
}

function failureCode(error: unknown): ClustersFailureCode {
  const kind = typeof error === "object" && error !== null && "kind" in error &&
      typeof error.kind === "string"
    ? error.kind
    : "";
  const codes: Record<string, ClustersFailureCode> = {
    unauthorized: "unauthorized",
    forbidden: "forbidden",
    network: "offline",
    "invalid-payload": "invalid-response",
    conflict: "conflict",
  };
  return codes[kind] ?? "error";
}

function canonicalTimestamp(value: string): void {
  if (!value.trim() || Number.isNaN(Date.parse(value))) invalidResponse();
}

function canonicalNullableTimestamp(value: string | null): void {
  if (value !== null) canonicalTimestamp(value);
}

function invalidResponse(): never {
  throw new ClustersPortFailure("invalid-response");
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error &&
    error.name === "AbortError";
}
