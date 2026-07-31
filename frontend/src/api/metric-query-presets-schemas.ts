import { z } from "zod";

const jsonMapSchema = z.record(z.string(), z.unknown());

/** Runtime contract for saved Prometheus queries returned by the dashboard API. */
export const metricQueryPresetSchema = z.strictObject({
  preset_id: z.string().min(1),
  workspace_id: z.string().min(1),
  cluster_id: z.string().min(1),
  name: z.string().min(1),
  description: z.string(),
  source: z.string().min(1),
  query: z.string().min(1),
  range_seconds: z.number().int().positive().nullable(),
  step_seconds: z.number().int().positive().nullable(),
  unit: z.string(),
  metadata: jsonMapSchema,
  created_by: z.string().min(1),
  created_at: z.string().nullable(),
  updated_at: z.string().nullable(),
});

export const metricQueryPresetListSchema = z.strictObject({
  items: z.array(metricQueryPresetSchema),
});

export type MetricQueryPreset = z.infer<typeof metricQueryPresetSchema>;
export type MetricQueryPresetList = z.infer<typeof metricQueryPresetListSchema>;
