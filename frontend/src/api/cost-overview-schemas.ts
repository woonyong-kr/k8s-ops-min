import { z } from "zod";

const availabilitySchema = z.enum(["available", "partial", "unavailable"]);
const freshnessSchema = z.enum(["live", "stale", "partial", "disconnected"]);
const reasonCodesSchema = z.array(z.string().min(1)).min(1);

export const costClusterScopeSchema = z.strictObject({
  workspace_id: z.string().min(1),
  cluster_id: z.string().min(1),
  namespaces: z.array(z.string().min(1)),
  freshness: freshnessSchema,
});

export const costScopeCoverageSchema = z.strictObject({
  availability: availabilitySchema,
  scopes: z.array(costClusterScopeSchema),
  observed_at: z.string().min(1).nullable(),
  reason_codes: z.array(z.string().min(1)),
}).superRefine((value, context) => {
  if (value.availability !== "available" && value.reason_codes.length === 0) {
    context.addIssue({ code: "custom", message: "incomplete cost scope requires reasons" });
  }
});

export const costObservationStatusSchema = z.strictObject({
  availability: z.literal("unavailable"),
  observed_at: z.null(),
  currency: z.null(),
  data_window: z.null(),
  reason_codes: reasonCodesSchema,
});

export const costObservationSummarySchema = z.strictObject({
  availability: z.literal("unavailable"),
  hourly_cost: z.null(),
  monthly_projection: z.null(),
  storage_cost: z.null(),
  idle_cost: z.null(),
  efficiency: z.null(),
  savings_recommendations: z.null(),
  reason_codes: reasonCodesSchema,
});

export const costTimeRangeSchema = z.enum(["6h", "24h", "7d"]);

export const costTrendPointSchema = z.strictObject({
  timestamp: z.number().int().min(0),
  rate_micros: z.number().int().min(0),
});

export const costTrendSeriesSchema = z.strictObject({
  key: z.string().min(1).max(160),
  label: z.string().min(1).max(240),
  points: z.array(costTrendPointSchema).min(2),
});

export const costObservedTrendSchema = z.strictObject({
  availability: z.enum(["available", "partial"]),
  range: costTimeRangeSchema,
  currency: z.string().regex(/^[A-Z]{3}$/),
  series: z.array(costTrendSeriesSchema).min(1),
  reason_codes: z.array(z.string().min(1)),
});

export const costUnavailableTrendSchema = z.strictObject({
  availability: z.literal("unavailable"),
  range: costTimeRangeSchema,
  currency: z.null(),
  series: z.tuple([]),
  reason_codes: reasonCodesSchema,
});

export const costTrendSchema = z.union([
  costObservedTrendSchema,
  costUnavailableTrendSchema,
]);

export const costOverviewSchema = z.strictObject({
  scope_coverage: costScopeCoverageSchema,
  observation: costObservationStatusSchema,
  summary: costObservationSummarySchema,
  trend: costTrendSchema,
  refresh_after_seconds: z.number().int().min(1).max(3600),
  trend_refresh_after_seconds: z.number().int(),
  nodes_refresh_after_seconds: z.number().int(),
});

export type CostOverviewEndpoint = z.infer<typeof costOverviewSchema>;
