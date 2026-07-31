import { z } from "zod";

const positiveIntegerSchema = z.number().int().positive();
const nonNegativeIntegerSchema = z.number().int().nonnegative();
const jsonMapSchema = z.record(z.string(), z.unknown());

/** Backend-owned Loki query used for a one-shot Pod log snapshot. */
export const telemetryQueryDefinitionSchema = z.strictObject({
  source: z.literal("loki"),
  name: z.string().min(1).max(120),
  description: z.string(),
  query: z.string().trim().min(1).max(10_000),
  range_seconds: positiveIntegerSchema.nullable().optional(),
  step_seconds: positiveIntegerSchema.nullable().optional(),
});

const telemetryLogValueSchema = z.strictObject({
  timestamp: z.string().min(1),
  line: z.string().nullable(),
  line_truncated: z.boolean().optional(),
  original_line_length: nonNegativeIntegerSchema.optional(),
});

const telemetryLogStreamSchema = z.strictObject({
  stream: jsonMapSchema,
  values: z.array(telemetryLogValueSchema),
});

export const telemetryLogResultSchema = z.strictObject({
  source: z.literal("loki"),
  query_name: z.string().min(1),
  query: z.string().min(1),
  result_type: z.string().nullable(),
  streams: z.array(telemetryLogStreamSchema),
  line_count: nonNegativeIntegerSchema,
  pattern_counts: z.record(z.string(), nonNegativeIntegerSchema),
  severity_counts: z.record(z.string(), nonNegativeIntegerSchema),
  trace_ids: z.array(z.string()),
  redaction_summary: z.strictObject({
    applied: z.boolean(),
    redacted_line_count: nonNegativeIntegerSchema,
    truncated_line_count: nonNegativeIntegerSchema,
  }),
  range_seconds: positiveIntegerSchema.optional(),
});

export const telemetryCommandResultSchema = z.strictObject({
  status: z.literal("completed"),
  cluster_id: z.string().min(1),
  applied: z.boolean(),
  message: z.string(),
  retryable: z.boolean(),
  resources: z.array(jsonMapSchema),
  stdout: z.string(),
  stderr: z.string(),
  query: telemetryQueryDefinitionSchema,
  result: z.array(telemetryLogResultSchema),
});

export type TelemetryQueryDefinition = z.infer<
  typeof telemetryQueryDefinitionSchema
>;
export type TelemetryLogResult = z.infer<typeof telemetryLogResultSchema>;
export type TelemetryCommandResult = z.infer<
  typeof telemetryCommandResultSchema
>;
