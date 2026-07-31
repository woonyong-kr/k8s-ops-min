import { z } from "zod";

const availabilitySchema = z.enum(["available", "partial", "unavailable"]);
const nullableTextSchema = z.string().min(1).nullable();
const reasonCodesSchema = z.array(z.string().min(1));

export const helmClusterScopeSchema = z.strictObject({
  workspace_id: z.string().min(1),
  cluster_id: z.string().min(1),
  namespaces: z.array(z.string().min(1)),
  freshness: z.enum(["live", "stale", "partial", "disconnected"]),
});

export const helmResourceRefSchema = z.strictObject({
  api_group: z.string(),
  version: z.string(),
  kind: z.string().min(1),
  namespace: nullableTextSchema,
  name: z.string().min(1),
  uid: z.string().min(1),
});

// HelmFeatureAvailability
export const helmUnavailableFeatureSchema = z.strictObject({
  availability: z.literal("unavailable"),
  reason_code: z.string().min(1),
});

// HelmResourceHealthAvailability
export const helmResourceHealthSchema = helmUnavailableFeatureSchema.extend({
  health: z.null(),
});

// HelmResourceHealthObservation
export const helmResourceHealthObservationSchema = z.strictObject({
  availability: z.enum(["available", "partial"]),
  health: z.string(),
  resource_count: z.number().int(),
  observed_at: nullableTextSchema,
  reason_codes: reasonCodesSchema,
});

// HelmResourceHealthObservation | HelmResourceHealthAvailability
export const helmResourceHealthUnionSchema = z.union([
  helmResourceHealthObservationSchema,
  helmResourceHealthSchema,
]);

export const helmObservationCoverageSchema = z.strictObject({
  availability: availabilitySchema,
  observed_at: nullableTextSchema,
  reason_codes: reasonCodesSchema,
}).superRefine((value, context) => {
  if (value.availability !== "available" && value.reason_codes.length === 0) {
    context.addIssue({ code: "custom", message: "incomplete Helm coverage requires reasons" });
  }
});

export const helmReleaseSchema = z.strictObject({
  scope: helmClusterScopeSchema,
  name: z.string().min(1),
  storage_namespace: z.string().min(1),
  storage: helmResourceRefSchema,
  storage_resource_version: nullableTextSchema,
  chart: nullableTextSchema,
  chart_version: nullableTextSchema,
  chart_reason_codes: reasonCodesSchema,
  app_version: z.null(),
  status: nullableTextSchema,
  revision: z.number().int().positive().nullable(),
  observed_at: nullableTextSchema,
  resource_health: helmResourceHealthUnionSchema,
});

export const helmReleaseHistoryEntrySchema = z.strictObject({
  storage: helmResourceRefSchema,
  revision: z.number().int().positive().nullable(),
  status: nullableTextSchema,
  observed_at: nullableTextSchema,
});

// HelmOwnedResource
export const helmOwnedResourceSchema = z.strictObject({
  resource: helmResourceRefSchema,
  status: z.string(),
  health: z.string(),
  observed_at: nullableTextSchema,
});

// HelmOwnedResourceObservation
export const helmOwnedResourceObservationSchema = z.strictObject({
  availability: z.enum(["available", "partial"]),
  items: z.array(helmOwnedResourceSchema),
  observed_at: nullableTextSchema,
  truncated: z.boolean(),
  reason_codes: reasonCodesSchema,
});

const helmUpgradeScalarSchema = z.union([z.string(), z.number(), z.boolean(), z.null()]);

// HelmUpgradeInput
export const helmUpgradeInputSchema = z.strictObject({
  name: z.string().min(1),
  value_type: z.enum(["string", "integer", "number", "boolean"]),
  required: z.boolean(),
  default: helmUpgradeScalarSchema,
  allowed_values: z.array(helmUpgradeScalarSchema),
});

// HelmUpgradeTarget
export const helmUpgradeTargetSchema = z.strictObject({
  item_id: z.string().min(1),
  name: z.string().min(1),
  version: z.string().min(1),
  chart_version: z.string().min(1),
  inputs: z.array(helmUpgradeInputSchema),
});

// HelmReleaseCommands
export const helmReleaseCommandsSchema = z.strictObject({
  availability: z.literal("available"),
  actions: z.array(z.enum(["upgrade", "rollback", "uninstall"])),
  confirmation_required: z.literal(true),
  realtime: z.literal(true),
  upgrade_targets: z.array(helmUpgradeTargetSchema),
});

export const helmReleaseListSchema = z.strictObject({
  releases: z.array(helmReleaseSchema),
  coverage: helmObservationCoverageSchema,
  refresh_after_seconds: z.number().int(),
  post_mutation_refresh_after_seconds: z.number(),
});

export const helmReleaseDetailSchema = z.strictObject({
  detail: z.strictObject({
    release: helmReleaseSchema,
    history: z.array(helmReleaseHistoryEntrySchema),
    manifest: helmUnavailableFeatureSchema,
    values: helmUnavailableFeatureSchema,
    owned_resources: z.union([helmOwnedResourceObservationSchema, helmUnavailableFeatureSchema]),
    commands: z.union([helmUnavailableFeatureSchema, helmReleaseCommandsSchema]),
  }),
  refresh_after_seconds: z.number().int(),
  post_mutation_refresh_after_seconds: z.number(),
});

export type HelmReleaseListEndpoint = z.infer<typeof helmReleaseListSchema>;
export type HelmReleaseDetailEndpoint = z.infer<typeof helmReleaseDetailSchema>;
