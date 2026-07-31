import { apiRequest, type ApiPath } from "./client";
import {
  providerCatalogSchema,
  providerClusterDiscoverySchema,
  targetInstallResponseSchema,
  targetPreflightResponseSchema,
  type ProviderCatalog,
  type ProviderClusterDiscovery,
  type TargetInstallResponse,
  type TargetPreflightResponse,
} from "./cluster-registration-schemas";

export const PROVIDERS_CATALOG_PATH = "/api/providers/catalog" as const;
export const PROVIDERS_CLUSTER_DISCOVERY_PATH =
  "/api/providers/cluster-discovery" as const;
export const TARGETS_PREFLIGHT_PATH = "/api/targets/preflight" as const;
export const TARGETS_PATH = "/api/targets" as const;

export interface TargetProviderSelectionInput {
  clusterRole: "management" | "target";
  managementBaseUrl: string;
  image: string;
  apply: boolean;
  kubeContext: string | null;
  cloudProvider: string;
  deployProvider: string;
  providerConfig: Record<string, unknown>;
}

export interface TargetPreflightInput extends TargetProviderSelectionInput {
  clusterId: string;
  name?: string | null;
  environment?: string | null;
}

export interface TargetRegisterInput extends TargetProviderSelectionInput {
  clusterId: string | null;
  name: string;
  environment: string;
  prometheusBaseUrl?: string;
  lokiBaseUrl?: string;
  tempoBaseUrl?: string;
  otelTracesEndpoint?: string;
  evidenceIntervalSeconds?: number;
  controlNamespaces?: string;
  installNodeCollector?: boolean;
  installSampleWorkload?: boolean;
  sampleWorkloadName?: string | null;
  sampleWorkloadImage?: string | null;
}

/** Loads the admin-visible provider definitions used to build dynamic fields. */
export function getProviderCatalog(signal?: AbortSignal): Promise<ProviderCatalog> {
  return apiRequest(PROVIDERS_CATALOG_PATH as ApiPath, providerCatalogSchema, { signal });
}

/** Loads registration flows and server-discovered import candidates. */
export function getProviderClusterDiscovery(
  signal?: AbortSignal,
): Promise<ProviderClusterDiscovery> {
  return apiRequest(
    PROVIDERS_CLUSTER_DISCOVERY_PATH as ApiPath,
    providerClusterDiscoverySchema,
    { signal },
  );
}

/** Validates a target registration without issuing an agent credential. */
export function preflightTargetRegistration(
  input: TargetPreflightInput,
  signal?: AbortSignal,
): Promise<TargetPreflightResponse> {
  assertProviderSelection(input);
  return apiRequest(TARGETS_PREFLIGHT_PATH as ApiPath, targetPreflightResponseSchema, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      ...providerSelectionBody(input),
      cluster_id: input.clusterId,
      name: input.name,
      environment: input.environment,
    }),
    signal,
  });
}

/**
 * Registers one target and returns its one-time credential-bearing receipt.
 * Callers must keep the result in ephemeral memory and must not retry transport failures.
 */
export function registerTarget(
  input: TargetRegisterInput,
  signal?: AbortSignal,
): Promise<TargetInstallResponse> {
  assertProviderSelection(input);
  return apiRequest(TARGETS_PATH as ApiPath, targetInstallResponseSchema, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify({
      ...providerSelectionBody(input),
      cluster_id: input.clusterId,
      name: input.name,
      environment: input.environment,
      prometheus_base_url: input.prometheusBaseUrl,
      loki_base_url: input.lokiBaseUrl,
      tempo_base_url: input.tempoBaseUrl,
      otel_traces_endpoint: input.otelTracesEndpoint,
      evidence_interval_seconds: input.evidenceIntervalSeconds,
      control_namespaces: input.controlNamespaces,
      install_node_collector: input.installNodeCollector,
      install_sample_workload: input.installSampleWorkload,
      sample_workload_name: input.sampleWorkloadName,
      sample_workload_image: input.sampleWorkloadImage,
    }),
    signal,
  });
}

function providerSelectionBody(input: TargetProviderSelectionInput) {
  return {
    cluster_role: input.clusterRole,
    management_base_url: input.managementBaseUrl,
    image: input.image,
    apply: input.apply,
    kube_context: input.kubeContext,
    cloud_provider: input.cloudProvider,
    deploy_provider: input.deployProvider,
    provider_config: input.providerConfig,
  };
}

function assertProviderSelection(input: TargetProviderSelectionInput): void {
  if (input.cloudProvider.trim() === "") {
    throw new TypeError("cloudProvider is required");
  }
  if (input.deployProvider.trim() === "") {
    throw new TypeError("deployProvider is required");
  }
}
