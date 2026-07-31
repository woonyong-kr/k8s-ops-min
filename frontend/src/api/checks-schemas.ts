import { z } from "zod";

const availabilitySchema = z.enum(["available", "partial", "unavailable"]);
const freshnessSchema = z.enum(["live", "stale", "partial", "disconnected"]);
const reasonCodesSchema = z.array(z.string().min(1)).min(1);

export const checksClusterScopeSchema = z.strictObject({
  workspace_id: z.string().min(1),
  cluster_id: z.string().min(1),
  namespaces: z.array(z.string().min(1)),
  freshness: freshnessSchema,
});

export const checksScopeCoverageSchema = z.strictObject({
  availability: availabilitySchema,
  scopes: z.array(checksClusterScopeSchema),
  observed_at: z.string().min(1).nullable(),
  reason_codes: z.array(z.string().min(1)),
}).superRefine((value, context) => {
  if (value.availability !== "available" && value.reason_codes.length === 0) {
    context.addIssue({ code: "custom", message: "incomplete checks scope requires reasons" });
  }
});

export const checksResultSetSchema = z.strictObject({
  availability: z.literal("unavailable"),
  evaluated_at: z.null(),
  checks: z.null(),
  total_check_count: z.null(),
  total_finding_count: z.null(),
  reason_codes: reasonCodesSchema,
});

export const checksCatalogSchema = z.strictObject({
  availability: z.literal("unavailable"),
  entries: z.null(),
  reason_codes: reasonCodesSchema,
});

const checksVisibilityStateSchema = z.enum(["ok", "limited", "degraded"]);
const checksVisibilityAccessSchema = z.enum(["allowed", "namespace_limited", "unavailable"]);

// AgentChecksVisibility + cluster_id (ChecksVisibility contract model).
export const checksVisibilitySchema = z.strictObject({
  state: checksVisibilityStateSchema,
  namespace_scope: z.array(z.string()),
  core: z.record(z.string(), checksVisibilityAccessSchema),
  missing_optional_kinds: z.array(z.string()),
  cluster_id: z.string().min(1),
});

// ChecksVisibilitySummary contract model.
export const checksVisibilitySummarySchema = z.strictObject({
  availability: availabilitySchema,
  clusters: z.array(checksVisibilitySchema),
  reason_codes: z.array(z.string()),
});

export const checksOverviewSchema = z.strictObject({
  scope_coverage: checksScopeCoverageSchema,
  result_set: checksResultSetSchema,
  catalog: checksCatalogSchema,
  visibility: checksVisibilitySummarySchema,
});

export const checksDetailSchema = z.strictObject({
  requested_check_id: z.string().min(1).max(253),
  availability: z.literal("unavailable"),
  title: z.null(),
  category: z.null(),
  effective_severity: z.null(),
  message: z.null(),
  remediation: z.null(),
  affected_resource_count: z.null(),
  findings: z.null(),
  reason_codes: reasonCodesSchema,
});

export const checksDetailResponseSchema = z.strictObject({
  scope_coverage: checksScopeCoverageSchema,
  detail: checksDetailSchema,
});

export type ChecksVisibility = z.infer<typeof checksVisibilitySchema>;
export type ChecksVisibilitySummary = z.infer<typeof checksVisibilitySummarySchema>;
export type ChecksOverviewEndpoint = z.infer<typeof checksOverviewSchema>;
export type ChecksDetailEndpoint = z.infer<typeof checksDetailResponseSchema>;
