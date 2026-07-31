import { z } from "zod";

const nonNegativeIntegerSchema = z.number().int().nonnegative();

export const liveMetricSourceSchema = z.enum([
  "kubelet_stats_summary",
  "metrics_server_fallback",
  "mixed",
  "unavailable",
]);

export const liveNodeResourceObservationSchema = z.strictObject({
  name: z.string().min(1),
  status: z.enum(["ready", "not_ready", "unknown"]),
  cpu_mcores: z.number().finite().nonnegative().nullable(),
  mem_mib: z.number().finite().nonnegative().nullable(),
  cpu_capacity_mcores: z.number().finite().positive().nullable(),
  mem_capacity_mib: z.number().finite().positive().nullable(),
  cpu_pct: z.number().finite().nonnegative().nullable(),
  mem_pct: z.number().finite().nonnegative().nullable(),
  observed_at: z.string().datetime({ offset: true }).nullable(),
  source: liveMetricSourceSchema,
  stale: z.boolean(),
  degraded_reason: z.string().min(1).nullable(),
  status_observed_at: z.string().datetime({ offset: true }).nullable(),
  status_source: z.literal("kubernetes_api"),
  status_stale: z.boolean(),
}).superRefine((value, context) => {
  if (!value.stale && (
    value.cpu_mcores === null
    || value.mem_mib === null
    || value.observed_at === null
  )) {
    context.addIssue({
      code: "custom",
      message: "fresh node metrics require measured cpu, memory, and observed_at",
    });
  }
  if (!value.status_stale && value.status_observed_at === null) {
    context.addIssue({
      code: "custom",
      message: "fresh node status requires status_observed_at",
    });
  }
  if (value.cpu_pct !== null && (
    value.cpu_mcores === null || value.cpu_capacity_mcores === null
  )) {
    context.addIssue({
      code: "custom",
      message: "node cpu percentage requires measured usage and capacity",
    });
  }
  if (value.mem_pct !== null && (
    value.mem_mib === null || value.mem_capacity_mib === null
  )) {
    context.addIssue({
      code: "custom",
      message: "node memory percentage requires measured usage and capacity",
    });
  }
});

export const liveClusterResourceObservationSchema = z.strictObject({
  resource_type: z.literal("cluster_metrics"),
  kind: z.literal("ClusterMetrics"),
  cluster_id: z.string().min(1),
  name: z.string().min(1),
  actual_interval_seconds: z.number().finite().nonnegative().nullable(),
  collection_complete: z.boolean(),
  status: z.enum(["ready", "degraded", "unknown"]),
  cpu_mcores: z.number().finite().nonnegative().nullable(),
  mem_mib: z.number().finite().nonnegative().nullable(),
  cpu_capacity_mcores: z.number().finite().positive().nullable(),
  mem_capacity_mib: z.number().finite().positive().nullable(),
  cpu_pct: z.number().finite().nonnegative().nullable(),
  mem_pct: z.number().finite().nonnegative().nullable(),
  observed_at: z.string().datetime({ offset: true }).nullable(),
  source: liveMetricSourceSchema,
  stale: z.boolean(),
  degraded_reason: z.string().min(1).nullable(),
  status_observed_at: z.string().datetime({ offset: true }).nullable(),
  status_source: z.literal("kubernetes_api"),
  status_stale: z.boolean(),
  nodes_ready: nonNegativeIntegerSchema.nullable(),
  nodes_total: nonNegativeIntegerSchema.nullable(),
  nodes: z.array(liveNodeResourceObservationSchema).max(200),
}).superRefine((value, context) => {
  if (!value.stale && (
    value.cpu_mcores === null
    || value.mem_mib === null
    || value.observed_at === null
  )) {
    context.addIssue({
      code: "custom",
      message: "fresh cluster metrics require measured cpu, memory, and observed_at",
    });
  }
  if (!value.status_stale && value.status_observed_at === null) {
    context.addIssue({
      code: "custom",
      message: "fresh cluster status requires status_observed_at",
    });
  }
  if (value.collection_complete) {
    if (value.nodes_total !== value.nodes.length) {
      context.addIssue({
        code: "custom",
        message: "complete cluster metrics require exact nodes_total",
      });
    }
    const nodesReady = value.nodes.filter((node) => node.status === "ready").length;
    if (value.nodes_ready !== nodesReady) {
      context.addIssue({
        code: "custom",
        message: "complete cluster metrics require exact nodes_ready",
      });
    }
  } else if (value.nodes_total !== null || value.nodes_ready !== null) {
    context.addIssue({
      code: "custom",
      message: "partial cluster metrics cannot claim node totals",
    });
  }
  if (value.cpu_pct !== null && (
    value.cpu_mcores === null || value.cpu_capacity_mcores === null
  )) {
    context.addIssue({
      code: "custom",
      message: "cluster cpu percentage requires measured usage and capacity",
    });
  }
  if (value.mem_pct !== null && (
    value.mem_mib === null || value.mem_capacity_mib === null
  )) {
    context.addIssue({
      code: "custom",
      message: "cluster memory percentage requires measured usage and capacity",
    });
  }
});

export type LiveNodeResourceObservation = z.infer<typeof liveNodeResourceObservationSchema>;
export type LiveClusterResourceObservation = z.infer<
  typeof liveClusterResourceObservationSchema
>;
