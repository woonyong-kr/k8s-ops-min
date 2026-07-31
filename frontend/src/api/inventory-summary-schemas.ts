import { z } from "zod";

const jsonMapSchema = z.record(z.string(), z.unknown());

// InventoryResourceCount (packages.contracts.gateway.responses).
export const inventoryResourceCountSchema = z.strictObject({
  resource_type: z.string(),
  health: z.string(),
  count: z.number().int(),
});

// InventoryNamespaceSummary.
export const inventoryNamespaceSummarySchema = z.strictObject({
  namespace: z.string(),
  total: z.number().int(),
  counts: z.array(inventoryResourceCountSchema),
});

// InventoryResourceCountForbidden.
export const inventoryResourceCountForbiddenSchema = z.strictObject({
  namespace: z.string().nullable(),
  api_group: z.string(),
  version: z.string(),
  resource: z.string(),
  kind: z.string(),
  namespaced: z.boolean(),
  reason_code: z.literal("list_permission_not_observed"),
});

// InventoryResourceCountsEvidence.
export const inventoryResourceCountsEvidenceSchema = z.strictObject({
  completeness: z.enum(["observed", "partial", "unavailable"]),
  observed_at: z.string().nullable(),
  namespace_scope: z.array(z.string()),
  reason_codes: z.array(z.string()),
  forbidden: z.array(inventoryResourceCountForbiddenSchema),
});

/** Runtime contract for `GET /clusters/{cluster_id}/inventory/summary`. */
export const inventorySummarySchema = z.strictObject({
  cluster_id: z.string(),
  latest_snapshot: jsonMapSchema.nullable(),
  counts: z.array(jsonMapSchema),
  namespaces: z.array(inventoryNamespaceSummarySchema),
  counts_evidence: inventoryResourceCountsEvidenceSchema,
});

export type InventorySummary = z.infer<typeof inventorySummarySchema>;
