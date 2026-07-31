import { z } from "zod";

const nullableStringSchema = z.string().nullable();

export const configReferenceKindSchema = z.enum(["ConfigMap", "Secret"]);
export const configReferenceSourceSchema = z.enum(["env", "env_from", "volume", "volume_mount"]);
export const configReferenceAvailabilitySchema = z.enum(["available", "partial", "unavailable"]);

export const configReferenceWorkloadSchema = z.strictObject({
  kind: z.literal("Deployment"),
  namespace: z.string(),
  name: z.string(),
  uid: nullableStringSchema.optional().default(null),
});

export const configReferenceUsageSchema = z.strictObject({
  workload: configReferenceWorkloadSchema,
  source: configReferenceSourceSchema,
  container_name: nullableStringSchema.optional().default(null),
  env_name: nullableStringSchema.optional().default(null),
  key: nullableStringSchema.optional().default(null),
  prefix: nullableStringSchema.optional().default(null),
  volume_name: nullableStringSchema.optional().default(null),
  mount_path: nullableStringSchema.optional().default(null),
  read_only: z.boolean().nullable().optional().default(null),
  optional: z.boolean().nullable().optional().default(null),
});

export const configReferenceItemSchema = z.strictObject({
  kind: configReferenceKindSchema,
  namespace: z.string(),
  name: z.string(),
  referenced_by: z.array(configReferenceUsageSchema),
});

export const configReferenceCoverageSchema = z.strictObject({
  availability: configReferenceAvailabilitySchema,
  snapshot_id: nullableStringSchema.optional().default(null),
  observed_at: nullableStringSchema.optional().default(null),
  workload_count: z.number().int().nonnegative(),
  projected_reference_count: z.number().int().nonnegative(),
  reason_codes: z.array(z.string()),
});

export const configReferenceListSchema = z.strictObject({
  cluster_id: z.string(),
  namespace: nullableStringSchema.optional().default(null),
  items: z.array(configReferenceItemSchema),
  coverage: configReferenceCoverageSchema,
});

export type ConfigReferenceKind = z.infer<typeof configReferenceKindSchema>;
export type ConfigReferenceSource = z.infer<typeof configReferenceSourceSchema>;
export type ConfigReferenceUsage = z.infer<typeof configReferenceUsageSchema>;
export type ConfigReferenceItem = z.infer<typeof configReferenceItemSchema>;
export type ConfigReferenceCoverage = z.infer<typeof configReferenceCoverageSchema>;
export type ConfigReferenceList = z.infer<typeof configReferenceListSchema>;
