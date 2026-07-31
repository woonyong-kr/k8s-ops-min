import { z } from "zod";

const nonNegativeIntegerSchema = z.number().int().nonnegative();
const positiveIntegerSchema = z.number().int().positive();
const finiteNumberSchema = z.number().finite();
const nullableTimestampSchema = z.string().nullable();
const unknownRecordSchema = z.record(z.string(), z.unknown());

/**
 * Stable usage fields consumed by product state. The backend usage object is an
 * intentionally open rollup, so unknown keys are accepted at the transport
 * boundary and stripped before this value leaves the API adapter.
 */
export const clusterUsageSchema = z.object({
  pod_total: nonNegativeIntegerSchema.optional(),
  pod_running: nonNegativeIntegerSchema.optional(),
  pod_pending: nonNegativeIntegerSchema.optional(),
  pod_failed: nonNegativeIntegerSchema.optional(),
  restart_total: nonNegativeIntegerSchema.optional(),
  node_total: nonNegativeIntegerSchema.optional(),
  node_ready: nonNegativeIntegerSchema.optional(),
  cpu_pct: finiteNumberSchema.nullable().optional(),
  mem_pct: finiteNumberSchema.nullable().optional(),
});

const normalizedClusterUsageSchema = unknownRecordSchema.pipe(clusterUsageSchema);

export const clusterUsageSampleSchema = z.strictObject({
  sampled_at: nullableTimestampSchema,
  usage: normalizedClusterUsageSchema,
});

export const clusterUsageResponseSchema = z.strictObject({
  cluster_id: z.string().min(1),
  samples: z.array(clusterUsageSampleSchema),
});

export const queryExecutionIdSchema = z
  .string()
  .min(1)
  .max(64)
  .regex(
    /^[A-Za-z0-9][A-Za-z0-9._-]*$/,
    "executionId must contain only letters, digits, dot, underscore, or hyphen",
  );

export const prometheusQueryNameSchema = z
  .string()
  .min(1)
  .max(120)
  .regex(
    /^[A-Za-z_][A-Za-z0-9_.-]*$/,
    "query name must start with a letter or underscore and use a stable identifier",
  );

export const prometheusQueryDefinitionSchema = z.strictObject({
  source: z.literal("prometheus"),
  name: prometheusQueryNameSchema,
  description: z.string(),
  query: z.string().trim().min(1).max(10_000),
  range_seconds: positiveIntegerSchema,
  step_seconds: positiveIntegerSchema.nullable().optional(),
});

export const agentDebugQueryReceiptSchema = z.strictObject({
  accepted: z.literal(true),
  command_id: z.string().min(1),
  correlation_id: z.string().min(1),
});

export const commandStatusValueSchema = z.enum([
  "queued",
  "leased",
  "running",
  "completed",
  "failed",
]);

export const commandStatusSchema = z.strictObject({
  command_id: z.string().min(1),
  cluster_id: z.string().min(1),
  correlation_id: z.string().min(1),
  action: z.string().min(1),
  status: commandStatusValueSchema,
  result: unknownRecordSchema,
  completed_at: nullableTimestampSchema,
});

const metricLabelsSchema = z.record(z.string(), z.string());

export const prometheusMetricPointSchema = z.strictObject({
  timestamp: finiteNumberSchema.nullable(),
  value: finiteNumberSchema.nullable(),
});

export const prometheusMetricSeriesSchema = z.strictObject({
  metric: metricLabelsSchema,
  values: z.array(prometheusMetricPointSchema),
});

export const prometheusRangeResultSchema = z
  .strictObject({
    query: z.string().min(1),
    query_mode: z.literal("range"),
    range_seconds: positiveIntegerSchema,
    step_seconds: positiveIntegerSchema.nullable(),
    result_type: z.literal("matrix"),
    series: z.array(prometheusMetricSeriesSchema),
    point_count: nonNegativeIntegerSchema,
  })
  .superRefine((value, context) => {
    const actualPointCount = value.series.reduce(
      (total, series) => total + series.values.length,
      0,
    );
    if (value.point_count !== actualPointCount) {
      context.addIssue({
        code: "custom",
        message: "point_count must equal the number of normalized series points",
        path: ["point_count"],
      });
    }
  });

export const telemetryCommandResultSchema = z.strictObject({
  status: z.literal("completed"),
  cluster_id: z.string().min(1),
  applied: z.boolean(),
  message: z.string(),
  retryable: z.boolean(),
  resources: z.array(unknownRecordSchema),
  stdout: z.string(),
  stderr: z.string(),
  query: prometheusQueryDefinitionSchema,
  result: z.strictObject({
    source: z.literal("prometheus"),
    results: z.record(z.string(), prometheusRangeResultSchema),
  }),
});

export type ClusterUsage = z.output<typeof clusterUsageSchema>;
export type ClusterUsageSample = z.output<typeof clusterUsageSampleSchema>;
export type ClusterUsageResponse = z.output<typeof clusterUsageResponseSchema>;
export type PrometheusQueryDefinition = z.output<
  typeof prometheusQueryDefinitionSchema
>;
export type AgentDebugQueryReceipt = z.output<typeof agentDebugQueryReceiptSchema>;
export type CommandStatusValue = z.output<typeof commandStatusValueSchema>;
export type CommandStatus = z.output<typeof commandStatusSchema>;
export type PrometheusMetricPoint = z.output<typeof prometheusMetricPointSchema>;
export type PrometheusMetricSeries = z.output<typeof prometheusMetricSeriesSchema>;
export type PrometheusRangeResult = z.output<typeof prometheusRangeResultSchema>;
