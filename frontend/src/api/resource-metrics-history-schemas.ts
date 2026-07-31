import { z } from "zod";

import {
  filterCountCompletenessSchema,
  filterSnapshotMetaSchema,
  rfc3339TimestampSchema,
} from "./resource-filter-schemas";

const nullableMetric = z.number().finite().nonnegative().nullable();

export const resourceMetricHistoryPointSchema = z.strictObject({
  observed_at: rfc3339TimestampSchema,
  cpu_mcores: nullableMetric,
  mem_mib: nullableMetric,
});

export const resourceMetricHistorySeriesSchema = z.strictObject({
  resource_id: z.string().min(1),
  cluster_id: z.string().min(1),
  resource_type: z.enum(["pod", "node"]),
  namespace: z.string().min(1).nullable(),
  name: z.string().min(1),
  points: z.array(resourceMetricHistoryPointSchema),
  has_sparkline_points: z.boolean(),
  completeness: filterCountCompletenessSchema,
  partial_reason_codes: z.array(z.string()),
}).superRefine((series, context) => {
  if (series.resource_type === "pod" && series.namespace === null) {
    context.addIssue({ code: "custom", message: "pod metric history requires a namespace", path: ["namespace"] });
  }
  if (series.resource_type === "node" && series.namespace !== null) {
    context.addIssue({ code: "custom", message: "node metric history must be cluster scoped", path: ["namespace"] });
  }
  const observed = series.points.map((point) => point.observed_at);
  if (new Set(observed).size !== observed.length ||
    observed.some((value, index) => index > 0 && value <= observed[index - 1]!)) {
    context.addIssue({ code: "custom", message: "metric points must be unique and ordered", path: ["points"] });
  }
  const hasCpu = series.points.some((point) => point.cpu_mcores !== null);
  if (series.has_sparkline_points !== hasCpu) {
    context.addIssue({ code: "custom", message: "sparkline availability must match CPU points", path: ["has_sparkline_points"] });
  }
  if (series.completeness === "unavailable" && hasCpu) {
    context.addIssue({ code: "custom", message: "unavailable series cannot contain CPU", path: ["completeness"] });
  }
  if (series.completeness === "exact" && (
    series.points.length === 0 ||
    series.points.some((point) => point.cpu_mcores === null) ||
    series.partial_reason_codes.length > 0
  )) {
    context.addIssue({ code: "custom", message: "exact series must be complete", path: ["completeness"] });
  }
});

export const resourceMetricsHistorySchema = z.strictObject({
  series: z.array(resourceMetricHistorySeriesSchema),
  completeness: filterCountCompletenessSchema,
  partial_reason_codes: z.array(z.string()),
  snapshot: filterSnapshotMetaSchema,
}).superRefine((response, context) => {
  const ids = response.series.map((series) => series.resource_id);
  if (new Set(ids).size !== ids.length) {
    context.addIssue({ code: "custom", message: "metric series ids must be unique", path: ["series"] });
  }
  if (response.completeness === "exact" && (
    response.series.some((series) => series.completeness !== "exact") ||
    response.partial_reason_codes.length > 0
  )) {
    context.addIssue({ code: "custom", message: "exact response must contain exact series", path: ["completeness"] });
  }
});

export type ResourceMetricsHistoryEndpoint = z.infer<typeof resourceMetricsHistorySchema>;
