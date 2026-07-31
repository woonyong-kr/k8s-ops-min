import { z } from "zod";

import {
  filterCountCompletenessSchema,
  rfc3339TimestampSchema,
} from "./resource-filter-schemas";

const nullableTimestampSchema = rfc3339TimestampSchema.nullable();
const nullableTextSchema = z.string().min(1).nullable();

export const applicationHealthSchema = z.strictObject({
  status: z.enum(["healthy", "degraded", "unknown"]),
  ready_pods: z.number().int().nonnegative().nullable(),
  total_pods: z.number().int().nonnegative().nullable(),
  restarts: z.number().int().nonnegative().nullable(),
}).superRefine((health, context) => {
  if (
    health.ready_pods !== null &&
    health.total_pods !== null &&
    health.ready_pods > health.total_pods
  ) {
    context.addIssue({
      code: "custom",
      message: "ready pods cannot exceed total pods",
      path: ["ready_pods"],
    });
  }
});

export const applicationRuntimeReadinessSchema = z.strictObject({
  completeness: filterCountCompletenessSchema,
  status: z.enum(["healthy", "degraded", "unknown"]),
  ready_pods: z.number().int().nonnegative().nullable(),
  total_pods: z.number().int().nonnegative().nullable(),
  restarts: z.number().int().nonnegative().nullable(),
}).superRefine((runtime, context) => {
  if (
    runtime.ready_pods !== null &&
    runtime.total_pods !== null &&
    runtime.ready_pods > runtime.total_pods
  ) {
    context.addIssue({
      code: "custom",
      message: "ready pods cannot exceed total pods",
      path: ["ready_pods"],
    });
  }
  if (
    runtime.completeness === "unavailable" &&
    (runtime.status !== "unknown" ||
      runtime.ready_pods !== null ||
      runtime.total_pods !== null ||
      runtime.restarts !== null)
  ) {
    context.addIssue({
      code: "custom",
      message: "unavailable runtime readiness cannot claim runtime evidence",
      path: ["completeness"],
    });
  }
});

export const applicationDeliveryStateSchema = z.strictObject({
  availability: z.enum(["available", "unavailable"]),
  status: z.enum(["succeeded", "failed", "running", "pending", "unknown"]).nullable(),
  workflow_run_id: nullableTextSchema,
  observed_at: nullableTimestampSchema,
}).superRefine((delivery, context) => {
  if (
    delivery.availability === "unavailable" &&
    (delivery.status !== null || delivery.workflow_run_id !== null || delivery.observed_at !== null)
  ) {
    context.addIssue({
      code: "custom",
      message: "unavailable delivery cannot claim delivery evidence",
      path: ["availability"],
    });
  }
  if (
    delivery.availability === "available" &&
    (delivery.status === null || delivery.workflow_run_id === null)
  ) {
    context.addIssue({
      code: "custom",
      message: "available delivery requires an observed workflow run",
      path: ["availability"],
    });
  }
});

export const applicationBatchRuntimeSchema = z.strictObject({
  availability: z.enum(["available", "unavailable"]),
  completeness: filterCountCompletenessSchema,
  status: z.enum(["running", "failed", "succeeded", "suspended", "unknown"]).nullable(),
  active_runs: z.number().int().nonnegative().nullable(),
  failed_runs: z.number().int().nonnegative().nullable(),
  succeeded_runs: z.number().int().nonnegative().nullable(),
}).superRefine((batch, context) => {
  if (
    batch.availability === "unavailable" &&
    (batch.completeness !== "unavailable" ||
      batch.status !== null ||
      batch.active_runs !== null ||
      batch.failed_runs !== null ||
      batch.succeeded_runs !== null)
  ) {
    context.addIssue({
      code: "custom",
      message: "unavailable batch runtime cannot claim batch evidence",
      path: ["availability"],
    });
  }
  if (
    batch.availability === "available" &&
    (batch.completeness === "unavailable" || batch.status === null)
  ) {
    context.addIssue({
      code: "custom",
      message: "available batch runtime requires an observed state",
      path: ["availability"],
    });
  }
});

export const applicationCurrentDeploymentSchema = z.strictObject({
  version: nullableTextSchema,
  image: nullableTextSchema,
  image_digest: nullableTextSchema,
  git_sha: nullableTextSchema,
  deployed_at: nullableTimestampSchema,
  deployed_by: nullableTextSchema,
});

export const applicationResourceCountSchema = z.strictObject({
  kind: z.string().min(1),
  count: z.number().int().nonnegative(),
});

export const applicationCatalogItemSchema = z.strictObject({
  id: z.string().min(1),
  name: z.string().min(1),
  environments: z.array(z.string().min(1)),
  lifecycle_status: z.string().min(1),
  health: applicationHealthSchema,
  runtime_readiness: applicationRuntimeReadinessSchema,
  current_deployment: applicationCurrentDeploymentSchema.nullable(),
  delivery: applicationDeliveryStateSchema,
  batch_runtime: applicationBatchRuntimeSchema,
  has_drift: z.boolean().nullable(),
  drift_summary: nullableTextSchema,
  resource_counts: z.array(applicationResourceCountSchema).nullable(),
  resource_counts_completeness: filterCountCompletenessSchema,
  open_incidents: z.number().int().nonnegative().nullable(),
  repository_ref: nullableTextSchema,
  default_branch: nullableTextSchema,
  manifest_path: nullableTextSchema,
}).superRefine((item, context) => {
  if (item.has_drift !== true && item.drift_summary !== null) {
    context.addIssue({
      code: "custom",
      message: "an in-sync application cannot have a drift summary",
      path: ["drift_summary"],
    });
  }
  if (item.has_drift === true && item.drift_summary === null) {
    context.addIssue({
      code: "custom",
      message: "confirmed drift requires a summary",
      path: ["drift_summary"],
    });
  }
  if (item.resource_counts_completeness === "unavailable" && item.resource_counts !== null) {
    context.addIssue({
      code: "custom",
      message: "unavailable resource counts must be null",
      path: ["resource_counts"],
    });
  }
  if (item.resource_counts_completeness !== "unavailable" && item.resource_counts === null) {
    context.addIssue({
      code: "custom",
      message: "available resource counts must be an array",
      path: ["resource_counts"],
    });
  }
  const kinds = item.resource_counts?.map((count) => count.kind) ?? [];
  if (kinds.some((kind, index) => index > 0 && kind <= kinds[index - 1]!)) {
    context.addIssue({
      code: "custom",
      message: "resource count kinds must be unique and sorted",
      path: ["resource_counts"],
    });
  }
});

export const applicationCatalogSchema = z.strictObject({
  applications: z.array(applicationCatalogItemSchema),
});

export const applicationEndpointSchema = z.strictObject({
  id: z.string().min(1),
  kind: z.string().min(1),
  name: z.string().min(1),
  url: z.string().min(1),
});

export const applicationActivitySchema = z.strictObject({
  id: z.string().min(1),
  type: z.enum(["deployment", "incident", "change"]),
  summary: nullableTextSchema,
  occurred_at: nullableTimestampSchema,
});

export const applicationIncidentPreviewSchema = z.strictObject({
  id: z.string().min(1),
  title: nullableTextSchema,
  status: z.string().min(1),
  started_at: nullableTimestampSchema,
});

const applicationPartialReasonCodesSchema = z.array(z.string().min(1));

const applicationClusterScopeSchema = z.strictObject({
  workspace_id: z.string().min(1),
  cluster_id: z.string().min(1),
  namespaces: z.array(z.string().min(1)),
  freshness: z.enum(["live", "stale", "partial", "disconnected"]),
});

const applicationInstanceScopeSchema = z.strictObject({
  id: z.string().min(1),
  environment: z.string().min(1),
  status: z.string().min(1),
  scope: applicationClusterScopeSchema,
});

const applicationWorkloadResourceRefSchema = z.strictObject({
  api_group: z.string(),
  version: z.string(),
  kind: z.string().min(1),
  namespace: nullableTextSchema,
  name: z.string().min(1),
  uid: z.string().min(1),
});

const applicationWorkloadScopeItemSchema = z.strictObject({
  key: z.string().min(1).max(128),
  resource: applicationWorkloadResourceRefSchema,
  scope: applicationClusterScopeSchema,
  observed_at: nullableTimestampSchema,
});

export const applicationWorkloadScopeSchema = z.strictObject({
  availability: z.enum(["available", "unavailable"]),
  completeness: filterCountCompletenessSchema,
  application_scope_available: z.boolean(),
  selected_workload_key: nullableTextSchema,
  workloads: z.array(applicationWorkloadScopeItemSchema).max(200),
  partial_reason_codes: applicationPartialReasonCodesSchema,
}).superRefine((scope, context) => {
  if (
    scope.availability === "unavailable" &&
    (scope.completeness !== "unavailable" ||
      scope.application_scope_available ||
      scope.selected_workload_key !== null ||
      scope.workloads.length > 0 ||
      scope.partial_reason_codes.length > 0)
  ) {
    context.addIssue({ code: "custom", message: "unavailable workload scope cannot claim workload evidence", path: ["availability"] });
  }
  if (scope.availability === "available" && scope.completeness === "unavailable") {
    context.addIssue({ code: "custom", message: "available workload scope requires completeness", path: ["completeness"] });
  }
  const keys = scope.workloads.map((workload) => workload.key);
  if (new Set(keys).size !== keys.length) {
    context.addIssue({ code: "custom", message: "workload identities must be unique", path: ["workloads"] });
  }
  if (scope.selected_workload_key !== null && !keys.includes(scope.selected_workload_key)) {
    context.addIssue({ code: "custom", message: "selected workload must be authorized", path: ["selected_workload_key"] });
  }
  if (!scope.application_scope_available && !(
    scope.completeness === "exact" &&
    scope.workloads.length === 1 &&
    scope.selected_workload_key === scope.workloads[0]?.key
  )) {
    context.addIssue({ code: "custom", message: "only one exact workload may hide application scope", path: ["application_scope_available"] });
  }
  if (scope.completeness === "exact" && scope.partial_reason_codes.length > 0) {
    context.addIssue({ code: "custom", message: "exact workload scope has no partial reasons", path: ["partial_reason_codes"] });
  }
  if (scope.completeness === "partial" && scope.partial_reason_codes.length === 0) {
    context.addIssue({ code: "custom", message: "partial workload scope requires reasons", path: ["partial_reason_codes"] });
  }
});

export const applicationDetailScopeSchema = z.strictObject({
  availability: z.enum(["available", "unavailable"]),
  completeness: filterCountCompletenessSchema,
  selected_instance_id: nullableTextSchema,
  instances: z.array(applicationInstanceScopeSchema).max(500),
  partial_reason_codes: applicationPartialReasonCodesSchema,
  selected_scope: z.enum(["application", "workload"]),
  workload_scope: applicationWorkloadScopeSchema,
}).superRefine((scope, context) => {
  if (
    scope.availability === "unavailable" &&
    (scope.completeness !== "unavailable" ||
      scope.selected_instance_id !== null ||
      scope.instances.length > 0 ||
      scope.partial_reason_codes.length > 0)
  ) {
    context.addIssue({
      code: "custom",
      message: "unavailable instance scope cannot claim scope evidence",
      path: ["availability"],
    });
  }
  if (
    scope.availability === "available" &&
    (scope.completeness === "unavailable" || scope.instances.length === 0)
  ) {
    context.addIssue({
      code: "custom",
      message: "available instance scope requires selectable instances",
      path: ["availability"],
    });
  }
  const ids = scope.instances.map((instance) => instance.id);
  if (new Set(ids).size !== ids.length) {
    context.addIssue({ code: "custom", message: "instance identities must be unique", path: ["instances"] });
  }
  if (scope.selected_instance_id !== null && !ids.includes(scope.selected_instance_id)) {
    context.addIssue({
      code: "custom",
      message: "selected instance must be among server-authorized instances",
      path: ["selected_instance_id"],
    });
  }
  if (scope.availability === "available" && scope.selected_instance_id === null) {
    context.addIssue({
      code: "custom",
      message: "available instance scope requires a selected instance",
      path: ["selected_instance_id"],
    });
  }
  if (scope.completeness === "exact" && scope.partial_reason_codes.length > 0) {
    context.addIssue({ code: "custom", message: "exact scope has no partial reasons", path: ["partial_reason_codes"] });
  }
  if (scope.completeness === "partial" && scope.partial_reason_codes.length === 0) {
    context.addIssue({ code: "custom", message: "partial scope requires source reasons", path: ["partial_reason_codes"] });
  }
  if (scope.selected_scope === "workload" && scope.workload_scope.selected_workload_key === null) {
    context.addIssue({ code: "custom", message: "workload selection requires a workload", path: ["selected_scope"] });
  }
  if (
    scope.selected_scope === "application" &&
    scope.workload_scope.availability === "available" &&
    !scope.workload_scope.application_scope_available
  ) {
    context.addIssue({ code: "custom", message: "application selection is unavailable", path: ["selected_scope"] });
  }
});

export const applicationTopologyNodeSchema = z.strictObject({
  id: z.string().min(1),
  cluster_id: z.string().min(1),
  resource_type: z.string().min(1),
  kind: z.string().min(1),
  namespace: nullableTextSchema,
  name: z.string().min(1),
  status: z.string().min(1),
  health: z.string().min(1),
  observed_at: nullableTimestampSchema,
});

export const applicationTopologyEdgeSchema = z.strictObject({
  id: z.string().min(1),
  from_id: z.string().min(1),
  to_id: z.string().min(1),
  type: z.enum(["owns", "runs_on", "selects", "routes_to"]),
  evidence_type: z.string().min(1),
  authority: z.enum(["authoritative", "derived"]),
  observed_at: nullableTimestampSchema,
});

export const applicationTopologySchema = z.strictObject({
  availability: z.enum(["available", "unavailable"]),
  completeness: filterCountCompletenessSchema,
  observed_at: nullableTimestampSchema,
  nodes: z.array(applicationTopologyNodeSchema).max(200).nullable(),
  edges: z.array(applicationTopologyEdgeSchema).max(1000).nullable(),
  partial_reason_codes: applicationPartialReasonCodesSchema,
}).superRefine((topology, context) => {
  if (
    topology.availability === "unavailable" &&
    (topology.completeness !== "unavailable" ||
      topology.observed_at !== null ||
      topology.nodes !== null ||
      topology.edges !== null ||
      topology.partial_reason_codes.length > 0)
  ) {
    context.addIssue({
      code: "custom",
      message: "unavailable topology cannot claim topology evidence",
      path: ["availability"],
    });
  }
  if (
    topology.availability === "available" &&
    (topology.completeness === "unavailable" || topology.nodes === null || topology.edges === null)
  ) {
    context.addIssue({
      code: "custom",
      message: "available topology requires node and edge collections",
      path: ["availability"],
    });
  }
  if (topology.completeness === "exact" && topology.partial_reason_codes.length > 0) {
    context.addIssue({ code: "custom", message: "exact topology has no partial reasons", path: ["partial_reason_codes"] });
  }
  if (topology.completeness === "partial" && topology.partial_reason_codes.length === 0) {
    context.addIssue({ code: "custom", message: "partial topology requires source reasons", path: ["partial_reason_codes"] });
  }
  const nodeIds = new Set(topology.nodes?.map((node) => node.id) ?? []);
  if (nodeIds.size !== (topology.nodes?.length ?? 0)) {
    context.addIssue({ code: "custom", message: "topology node identities must be unique", path: ["nodes"] });
  }
  if ((topology.edges ?? []).some((edge) => !nodeIds.has(edge.from_id) || !nodeIds.has(edge.to_id))) {
    context.addIssue({ code: "custom", message: "topology edges must reference returned nodes", path: ["edges"] });
  }
});

export const applicationHistoryEntrySchema = z.strictObject({
  id: z.string().min(1),
  type: z.enum(["delivery", "incident"]),
  status: z.string().min(1),
  summary: nullableTextSchema,
  occurred_at: nullableTimestampSchema,
  workflow_run_id: nullableTextSchema,
  gitops_change_id: nullableTextSchema,
}).superRefine((entry, context) => {
  if (entry.type === "delivery" && entry.workflow_run_id === null) {
    context.addIssue({ code: "custom", message: "delivery history requires a workflow run anchor", path: ["workflow_run_id"] });
  }
  if (entry.type === "incident" && (entry.workflow_run_id !== null || entry.gitops_change_id !== null)) {
    context.addIssue({ code: "custom", message: "incident history cannot claim deployment anchors", path: ["type"] });
  }
});

export const applicationHistorySchema = z.strictObject({
  availability: z.enum(["available", "unavailable"]),
  completeness: filterCountCompletenessSchema,
  entries: z.array(applicationHistoryEntrySchema).max(6).nullable(),
  partial_reason_codes: applicationPartialReasonCodesSchema,
}).superRefine((history, context) => {
  if (
    history.availability === "unavailable" &&
    (history.completeness !== "unavailable" ||
      history.entries !== null ||
      history.partial_reason_codes.length > 0)
  ) {
    context.addIssue({ code: "custom", message: "unavailable history cannot claim history evidence", path: ["availability"] });
  }
  if (
    history.availability === "available" &&
    (history.completeness === "unavailable" || history.entries === null)
  ) {
    context.addIssue({ code: "custom", message: "available history requires entry collection", path: ["availability"] });
  }
  if (history.completeness === "exact" && history.partial_reason_codes.length > 0) {
    context.addIssue({ code: "custom", message: "exact history has no partial reasons", path: ["partial_reason_codes"] });
  }
  if (history.completeness === "partial" && history.partial_reason_codes.length === 0) {
    context.addIssue({ code: "custom", message: "partial history requires source reasons", path: ["partial_reason_codes"] });
  }
});

export const applicationSourceEvidenceSchema = z.strictObject({
  availability: z.enum(["available", "unavailable"]),
  completeness: filterCountCompletenessSchema,
  conflict: z.enum(["aligned", "conflict", "unknown"]).nullable(),
  repository_ref: nullableTextSchema,
  default_branch: nullableTextSchema,
  manifest_path: nullableTextSchema,
  partial_reason_codes: applicationPartialReasonCodesSchema,
}).superRefine((source, context) => {
  if (
    source.availability === "unavailable" &&
    (source.completeness !== "unavailable" ||
      source.conflict !== null ||
      source.repository_ref !== null ||
      source.default_branch !== null ||
      source.manifest_path !== null ||
      source.partial_reason_codes.length > 0)
  ) {
    context.addIssue({ code: "custom", message: "unavailable source cannot claim source evidence", path: ["availability"] });
  }
  if (
    source.availability === "available" &&
    (source.completeness === "unavailable" || source.conflict === null || source.repository_ref === null)
  ) {
    context.addIssue({ code: "custom", message: "available source requires repository provenance", path: ["availability"] });
  }
  if (
    source.completeness === "exact" &&
    (source.default_branch === null || source.manifest_path === null || source.partial_reason_codes.length > 0)
  ) {
    context.addIssue({ code: "custom", message: "exact source requires branch and manifest", path: ["completeness"] });
  }
  if (source.completeness === "partial" && source.partial_reason_codes.length === 0) {
    context.addIssue({ code: "custom", message: "partial source requires source reasons", path: ["partial_reason_codes"] });
  }
});

const applicationUnavailableEvidenceSchema = z.strictObject({
  availability: z.literal("unavailable"),
  reason_codes: z.array(z.string().min(1)).min(1).max(20),
}).superRefine((evidence, context) => {
  if (new Set(evidence.reason_codes).size !== evidence.reason_codes.length) {
    context.addIssue({ code: "custom", message: "unavailable evidence reasons must be unique", path: ["reason_codes"] });
  }
});

const applicationWorkloadDetailSchema = z.strictObject({
  workload: applicationWorkloadScopeItemSchema,
  runtime_readiness: applicationRuntimeReadinessSchema,
  resource_counts: z.array(applicationResourceCountSchema).nullable(),
  resource_counts_completeness: filterCountCompletenessSchema,
  topology: applicationTopologySchema,
  history: applicationUnavailableEvidenceSchema,
  cost: applicationUnavailableEvidenceSchema,
  actions: applicationUnavailableEvidenceSchema,
}).superRefine((detail, context) => {
  if (detail.resource_counts_completeness === "unavailable" && detail.resource_counts !== null) {
    context.addIssue({ code: "custom", message: "unavailable workload counts must be null", path: ["resource_counts"] });
  }
  if (detail.resource_counts_completeness !== "unavailable" && detail.resource_counts === null) {
    context.addIssue({ code: "custom", message: "available workload counts require an array", path: ["resource_counts"] });
  }
});

export const applicationDetailItemSchema = applicationCatalogItemSchema.extend({
  scope: applicationDetailScopeSchema,
  endpoints: z.array(applicationEndpointSchema).nullable(),
  endpoints_completeness: filterCountCompletenessSchema,
  recent_activity: z.array(applicationActivitySchema).max(3),
  recent_incidents: z.array(applicationIncidentPreviewSchema).max(3),
  topology: applicationTopologySchema,
  history: applicationHistorySchema,
  source: applicationSourceEvidenceSchema,
  workload: applicationWorkloadDetailSchema.nullable(),
}).superRefine((item, context) => {
  if (item.endpoints_completeness === "unavailable" && item.endpoints !== null) {
    context.addIssue({ code: "custom", message: "unavailable endpoints must be null", path: ["endpoints"] });
  }
  if (item.endpoints_completeness !== "unavailable" && item.endpoints === null) {
    context.addIssue({ code: "custom", message: "available endpoints must be an array", path: ["endpoints"] });
  }
  const workloadSelected = item.scope.selected_scope === "workload";
  if (workloadSelected !== (item.workload !== null)) {
    context.addIssue({ code: "custom", message: "workload detail must match selected scope", path: ["workload"] });
  }
  if (
    item.workload !== null &&
    item.workload.workload.key !== item.scope.workload_scope.selected_workload_key
  ) {
    context.addIssue({ code: "custom", message: "workload detail must match selected workload", path: ["workload"] });
  }
});

export const applicationDetailSchema = z.strictObject({
  application: applicationDetailItemSchema,
});

export const applicationDeploymentSchema = z.strictObject({
  id: z.string().min(1),
  environment: nullableTextSchema,
  cluster_id: z.string().min(1),
  git_sha: nullableTextSchema,
  version: nullableTextSchema,
  deployed_at: nullableTimestampSchema,
  deployed_by: nullableTextSchema,
  status: z.enum(["succeeded", "failed", "running", "pending", "unknown"]),
  gitops_change_id: nullableTextSchema,
});

export const applicationDeploymentHistorySchema = z.strictObject({
  deployments: z.array(applicationDeploymentSchema),
});

const driftValueSchema = z.union([
  z.string(),
  z.number().finite(),
  z.boolean(),
  z.null(),
]);

export const applicationDriftDifferenceSchema = z.strictObject({
  resource: z.string().min(1),
  field_path: z.string().min(1),
  old_value: driftValueSchema,
  new_value: driftValueSchema,
  value_redacted: z.boolean(),
  changed_by: nullableTextSchema,
  changed_at: nullableTimestampSchema,
}).superRefine((difference, context) => {
  if (
    difference.value_redacted &&
    (difference.old_value !== null || difference.new_value !== null)
  ) {
    context.addIssue({
      code: "custom",
      message: "redacted values must be null",
      path: ["value_redacted"],
    });
  }
});

export const applicationDriftSchema = z.strictObject({
  status: z.enum(["in_sync", "drifted", "unknown"]),
  summary: nullableTextSchema,
  differences: z.array(applicationDriftDifferenceSchema),
  observed_at: nullableTimestampSchema,
}).superRefine((drift, context) => {
  if (drift.status !== "drifted" && (drift.differences.length > 0 || drift.summary !== null)) {
    context.addIssue({
      code: "custom",
      message: "non-drift responses cannot contain evidence",
      path: ["differences"],
    });
  }
  if (drift.status === "drifted" && (drift.differences.length === 0 || drift.summary === null)) {
    context.addIssue({
      code: "custom",
      message: "drifted responses must contain evidence",
      path: ["differences"],
    });
  }
});

export type ApplicationCatalogEndpoint = z.infer<typeof applicationCatalogSchema>;
export type ApplicationCatalogEndpointItem = z.infer<typeof applicationCatalogItemSchema>;
export type ApplicationDetailEndpoint = z.infer<typeof applicationDetailSchema>;
export type ApplicationDetailEndpointItem = z.infer<typeof applicationDetailItemSchema>;
export type ApplicationDeploymentHistoryEndpoint = z.infer<typeof applicationDeploymentHistorySchema>;
export type ApplicationDriftEndpoint = z.infer<typeof applicationDriftSchema>;
