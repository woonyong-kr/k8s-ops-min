import { apiRequest, type ApiPath } from "./client";
import {
  usageSeriesResponseSchema,
  type ClusterResourceUsageSeries,
  type ResourceUsageSeriesPoint,
} from "./usage-series-schemas";
import { encodePathSegment, withQuery } from "./url";

const DEFAULT_USAGE_SERIES_LIMIT = 288;
const MIN_USAGE_SERIES_LIMIT = 1;
const MAX_USAGE_SERIES_LIMIT = 2_000;

export type ResourceUsageTarget =
  | { resourceType: "pod"; namespace: string; name: string }
  | { resourceType: "node"; name: string };

export interface UsageSeriesOptions {
  limit?: number;
}

/** Loads raw usage samples and extracts one Pod or Node history for charts. */
export async function getClusterResourceUsageSeries(
  clusterId: string,
  target: ResourceUsageTarget,
  options: UsageSeriesOptions = {},
  signal?: AbortSignal,
): Promise<ClusterResourceUsageSeries> {
  const limit = options.limit ?? DEFAULT_USAGE_SERIES_LIMIT;
  assertUsageSeriesLimit(limit);
  const basePath = `/api/clusters/${encodePathSegment(clusterId)}/usage` as ApiPath;
  const path = withQuery(basePath, [["limit", limit]]);
  const response = await apiRequest(path, usageSeriesResponseSchema, { signal });
  const key = target.resourceType === "pod" ? `${target.namespace}/${target.name}` : target.name;

  return {
    clusterId: response.cluster_id,
    resourceType: target.resourceType,
    namespace: target.resourceType === "pod" ? target.namespace : null,
    name: target.name,
    points: response.samples.map((sample) => {
      const collection = target.resourceType === "pod" ? sample.usage.pods : sample.usage.nodes;
      const usage = recordValue(collection, key);
      return usagePoint(sample.sampled_at, usage);
    }),
  };
}

function usagePoint(
  sampledAt: string | null,
  usage: Record<string, unknown> | null,
): ResourceUsageSeriesPoint {
  return {
    sampledAt,
    cpuMcores: finiteNumber(usage?.cpu_mcores),
    memMib: finiteNumber(usage?.mem_mib),
    cpuPct: finiteNumber(usage?.cpu_pct),
    memPct: finiteNumber(usage?.mem_pct),
  };
}

function recordValue(value: unknown, key: string): Record<string, unknown> | null {
  if (!isRecord(value)) return null;
  const candidate = value[key];
  return isRecord(candidate) ? candidate : null;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function finiteNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

function assertUsageSeriesLimit(limit: number): void {
  if (
    !Number.isInteger(limit) ||
    limit < MIN_USAGE_SERIES_LIMIT ||
    limit > MAX_USAGE_SERIES_LIMIT
  ) {
    throw new RangeError(
      `usage series limit must be an integer from ${MIN_USAGE_SERIES_LIMIT} to ${MAX_USAGE_SERIES_LIMIT}`,
    );
  }
}
