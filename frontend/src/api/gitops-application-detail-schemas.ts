import { z } from "zod";

const availabilitySchema = z.enum(["available", "partial", "unavailable"]);
const nullableTextSchema = z.string().min(1).nullable();
const gitOpsReasonCodeSchema = z.enum([
  "binding_scope_unavailable",
  "multiple_target_scopes",
  "live_observation_not_integrated",
  "source_revision_unavailable",
  "workflow_operation_unobserved",
  "provider_operation_not_integrated",
  "not_authorized",
  "operation_in_progress",
  "provider_refresh_not_integrated",
  "provider_sync_not_integrated",
]);

export const gitOpsResourceRefSchema = z.strictObject({
  api_group: z.string(),
  version: z.string(),
  kind: z.string().min(1),
  namespace: nullableTextSchema,
  name: z.string().min(1),
  uid: z.string().min(1),
});

export const gitOpsClusterScopeSchema = z.strictObject({
  workspace_id: z.string().min(1),
  cluster_id: z.string().min(1),
  namespaces: z.array(z.string().min(1)),
  freshness: z.enum(["live", "stale", "partial", "disconnected"]),
});

export const gitOpsApplicationScopeSchema = z.strictObject({
  availability: availabilitySchema,
  scope: gitOpsClusterScopeSchema.nullable(),
  reason_code: gitOpsReasonCodeSchema.nullable(),
}).superRefine((value, context) => {
  if (value.availability === "available" && value.scope === null) {
    context.addIssue({ code: "custom", message: "available scope requires a ClusterScope" });
  }
  if (value.availability === "unavailable" && value.scope !== null) {
    context.addIssue({ code: "custom", message: "unavailable scope cannot claim a ClusterScope" });
  }
  if (value.availability !== "available" && value.reason_code === null) {
    context.addIssue({ code: "custom", message: "non-available scope requires a reason" });
  }
});

export const gitOpsSourceSchema = z.strictObject({
  repository_ref: nullableTextSchema,
  default_branch: nullableTextSchema,
  manifest_path: nullableTextSchema,
});

export const gitOpsDesiredLiveDiffAvailabilitySchema = z.strictObject({
  availability: availabilitySchema,
  source_revision: nullableTextSchema,
  live_observation_revision: nullableTextSchema,
  reason_code: gitOpsReasonCodeSchema.nullable(),
}).superRefine((value, context) => {
  if (value.availability === "available") {
    context.addIssue({
      code: "custom",
      message: "available desired/live comparison requires a separate artifact contract",
    });
  }
  if (value.availability !== "available" && value.reason_code === null) {
    context.addIssue({ code: "custom", message: "non-available comparison requires a reason" });
  }
});

export const gitOpsOperationObservationSchema = z.strictObject({
  availability: availabilitySchema,
  in_progress: z.boolean().nullable(),
  workflow_run_id: nullableTextSchema,
  status: nullableTextSchema,
  observed_at: nullableTextSchema,
  reason_code: gitOpsReasonCodeSchema.nullable(),
}).superRefine((value, context) => {
  if (value.availability === "unavailable" && (
    value.in_progress !== null || value.workflow_run_id !== null || value.status !== null || value.observed_at !== null
  )) {
    context.addIssue({ code: "custom", message: "unavailable operation cannot claim workflow evidence" });
  }
  if (value.in_progress && (value.workflow_run_id === null || value.status === null)) {
    context.addIssue({ code: "custom", message: "in-progress operation requires workflow identity" });
  }
  if (value.availability !== "available" && value.reason_code === null) {
    context.addIssue({ code: "custom", message: "partial operation requires a reason" });
  }
});

export const gitOpsActionCapabilitySchema = z.strictObject({
  action: z.enum(["refresh", "sync"]),
  authorization: z.enum(["allowed", "denied"]),
  availability: availabilitySchema,
  enabled: z.literal(false),
  operation_blocked: z.boolean(),
  reason_code: gitOpsReasonCodeSchema.nullable(),
}).superRefine((value, context) => {
  if (value.enabled && (value.authorization !== "allowed" || value.availability !== "available")) {
    context.addIssue({ code: "custom", message: "enabled action needs authorization and integration" });
  }
  if (!value.enabled && value.reason_code === null) {
    context.addIssue({ code: "custom", message: "disabled action requires a reason" });
  }
  if (value.operation_blocked && (value.action !== "sync" || value.enabled)) {
    context.addIssue({ code: "custom", message: "only a disabled sync can be operation-blocked" });
  }
});

export const gitOpsApplicationDetailSchema = z.strictObject({
  application_id: z.string().min(1),
  name: z.string().min(1),
  resource: gitOpsResourceRefSchema,
  scope: gitOpsApplicationScopeSchema,
  source: gitOpsSourceSchema,
  desired_live_diff: gitOpsDesiredLiveDiffAvailabilitySchema,
  operation: gitOpsOperationObservationSchema,
  capabilities: z.tuple([gitOpsActionCapabilitySchema, gitOpsActionCapabilitySchema]),
}).superRefine((value, context) => {
  if (value.capabilities[0].action !== "refresh" || value.capabilities[1].action !== "sync") {
    context.addIssue({ code: "custom", message: "capabilities must be refresh then sync" });
  }
});

export const gitOpsApplicationDetailResponseSchema = z.strictObject({
  application: gitOpsApplicationDetailSchema,
});

export type GitOpsApplicationDetailEndpoint = z.infer<typeof gitOpsApplicationDetailResponseSchema>;
