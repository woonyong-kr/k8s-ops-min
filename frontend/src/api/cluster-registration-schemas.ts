import { z } from "zod";

import { connectionStageSchema } from "./cluster-stage-schemas";

const jsonMapSchema = z.record(z.string(), z.unknown());

export const providerConfigFieldSchema = z.strictObject({
  key: z.string(),
  label: z.string(),
  required: z.boolean(),
  kind: z.string(),
  options: z.array(z.string()),
  description: z.string(),
});

export const providerCredentialRequirementSchema = z.strictObject({
  key: z.string(),
  ref_prefixes: z.array(z.string()),
  required_for: z.array(z.string()),
  description: z.string(),
});

export const providerDefinitionSchema = z.strictObject({
  category: z.enum(["source", "deploy", "cloud", "secret"]),
  key: z.string(),
  label: z.string(),
  status: z.enum(["available", "unavailable"]),
  adapter: z.string().nullable(),
  capabilities: z.array(z.string()),
  credential_requirements: z.array(providerCredentialRequirementSchema),
  config_keys: z.array(z.string()),
  config_fields: z.array(providerConfigFieldSchema),
  unavailable_reason: z.string().nullable(),
});

export const providerCatalogSchema = z.strictObject({
  providers: z.record(z.string(), z.array(providerDefinitionSchema)),
});

export const clusterImportCandidateSchema = z.strictObject({
  cluster_id: z.string(),
  name: z.string(),
  source: z.string(),
  cloud_provider: z.string(),
  deploy_provider: z.string(),
  kube_context: z.string().nullable(),
  external_handle: z.string().nullable(),
  console_url: z.string().nullable(),
  direct_apply_available: z.boolean(),
  labels: jsonMapSchema,
});

export const clusterRegistrationFlowSchema = z.strictObject({
  cloud_provider: z.string(),
  label: z.string(),
  status: z.string(),
  description: z.string(),
  deploy_providers: z.array(jsonMapSchema),
  default_deploy_provider: z.string(),
  supports_import: z.boolean(),
  unavailable_reason: z.string().nullable(),
  import_candidates: z.array(clusterImportCandidateSchema),
});

export const providerClusterDiscoverySchema = z.strictObject({
  default_cloud_provider: z.string(),
  default_deploy_provider: z.string(),
  flows: z.array(clusterRegistrationFlowSchema),
  import_candidates: z.array(clusterImportCandidateSchema),
});

export const managementAccessSchema = z.strictObject({
  mode: z.enum(["portforward", "loadbalancer", "ingress", "nodeport", "unknown"]),
  external_url: z.string().nullable(),
  agent_server_url: z.string(),
  reachability: z.enum(["external", "self_only"]),
  limitation_reason: z.enum(["external_url_not_configured"]).nullable(),
});

export const targetPreflightResponseSchema = z.strictObject({
  valid: z.boolean(),
  duplicate_cluster_id: z.boolean(),
  provider_ready: z.boolean(),
  agent_install_status: z.string(),
  connection_status: z.string(),
  kube_context_allowed: z.boolean().nullable(),
  errors: z.array(z.string()),
  warnings: z.array(z.string()),
  selected: z.record(z.string(), jsonMapSchema),
  last_agent_id: z.string().nullable(),
  last_seen_at: z.string().nullable(),
  management_access: managementAccessSchema.nullable(),
});

export const targetBootstrapStepSchema = z.strictObject({
  label: z.string(),
  command: z.string(),
});

export const targetInstallResponseSchema = z.strictObject({
  registered: z.boolean(),
  cluster_id: z.string(),
  status: z.string(),
  applied: z.boolean(),
  apply_output: z.string().nullable(),
  install_manifest: z.string(),
  agent_token: z.string(),
  install_command: z.string(),
  bootstrap_command: z.string(),
  powershell_install_command: z.string(),
  powershell_bootstrap_command: z.string(),
  bootstrap_steps: z.array(targetBootstrapStepSchema),
  connect_timeout_seconds: z.number().int().nullable(),
  connect_expires_at: z.string().nullable(),
  connection_stage: connectionStageSchema.nullable(),
  management_access: managementAccessSchema.nullable(),
});

export type ProviderConfigField = z.infer<typeof providerConfigFieldSchema>;
export type ProviderCredentialRequirement = z.infer<
  typeof providerCredentialRequirementSchema
>;
export type ProviderDefinition = z.infer<typeof providerDefinitionSchema>;
export type ProviderCatalog = z.infer<typeof providerCatalogSchema>;
export type ClusterImportCandidate = z.infer<typeof clusterImportCandidateSchema>;
export type ClusterRegistrationFlow = z.infer<typeof clusterRegistrationFlowSchema>;
export type ProviderClusterDiscovery = z.infer<typeof providerClusterDiscoverySchema>;
export type TargetPreflightResponse = z.infer<typeof targetPreflightResponseSchema>;
export type TargetBootstrapStep = z.infer<typeof targetBootstrapStepSchema>;
export type TargetInstallResponse = z.infer<typeof targetInstallResponseSchema>;
