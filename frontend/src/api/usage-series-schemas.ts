import { z } from "zod";

const jsonMapSchema = z.record(z.string(), z.unknown());

/** Raw usage contract: nested pod/node rollups must survive this boundary. */
export const usageSeriesResponseSchema = z.strictObject({
  cluster_id: z.string().min(1),
  samples: z.array(
    z.strictObject({
      sampled_at: z.string().nullable(),
      usage: jsonMapSchema,
    }),
  ),
});

export type UsageSeriesResponse = z.infer<typeof usageSeriesResponseSchema>;

export type ResourceUsageSeriesPoint = {
  sampledAt: string | null;
  cpuMcores: number | null;
  memMib: number | null;
  cpuPct: number | null;
  memPct: number | null;
};

export type ClusterResourceUsageSeries = {
  clusterId: string;
  resourceType: "pod" | "node";
  namespace: string | null;
  name: string;
  points: ResourceUsageSeriesPoint[];
};
