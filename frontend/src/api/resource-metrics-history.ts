import { apiRequest, type ApiPath } from "./client";
import { resourceFilterEntries, type ResourceFilterQuery } from "./resource-filter-query";
import {
  resourceMetricsHistorySchema,
  type ResourceMetricsHistoryEndpoint,
} from "./resource-metrics-history-schemas";
import { withQuery } from "./url";

export const RESOURCE_METRICS_HISTORY_PATH: ApiPath = "/api/metrics/history";
export type ResourceMetricTimeRange = "15m" | "1h" | "6h" | "24h";

export interface ResourceMetricsHistoryQuery extends ResourceFilterQuery {
  ids: string[];
  snapshotRevision: number;
  range?: ResourceMetricTimeRange;
  limit?: number;
}

export function getResourceMetricsHistory(
  query: ResourceMetricsHistoryQuery,
  signal?: AbortSignal,
): Promise<ResourceMetricsHistoryEndpoint> {
  const ids = metricIds(query.ids);
  const snapshotRevision = positiveInteger(
    query.snapshotRevision,
    "snapshotRevision",
    Number.MAX_SAFE_INTEGER,
  );
  const limit = query.limit === undefined
    ? undefined
    : positiveInteger(query.limit, "limit", 288);
  const path = withQuery(RESOURCE_METRICS_HISTORY_PATH, [
    ["ids", ids.join(",")],
    ...resourceFilterEntries(query),
    ["snapshot_revision", snapshotRevision],
    ["range", query.range ?? "1h"],
    ["limit", limit],
  ]);
  return apiRequest(path, resourceMetricsHistorySchema, { signal });
}

function metricIds(values: string[]): string[] {
  if (values.length < 1 || values.length > 100) {
    throw new RangeError("metric history requires 1 to 100 resource ids");
  }
  const ids = values.map((value) => value.trim());
  if (ids.some((value) => value.length === 0) || new Set(ids).size !== ids.length) {
    throw new TypeError("metric history resource ids must be non-empty and unique");
  }
  return ids;
}

function positiveInteger(value: number, label: string, maximum: number): number {
  if (!Number.isInteger(value) || value < 1 || value > maximum) {
    throw new RangeError(`${label} must be an integer from 1 to ${maximum}`);
  }
  return value;
}
