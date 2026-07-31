import { apiRequest, type ApiPath } from "./client";
import {
  metricQueryPresetListSchema,
  type MetricQueryPresetList,
} from "./metric-query-presets-schemas";
import {
  agentDebugQueryReceiptSchema,
  type AgentDebugQueryReceipt,
} from "./metrics-schemas";
import { encodePathSegment } from "./url";

/** Loads the saved PromQL queries scoped to one cluster. */
export function listMetricQueryPresets(
  clusterId: string,
  signal?: AbortSignal,
): Promise<MetricQueryPresetList> {
  const path =
    `/api/clusters/${encodePathSegment(clusterId)}/metric-query-presets` as ApiPath;
  return apiRequest(path, metricQueryPresetListSchema, { signal });
}

/**
 * Queues one backend-owned metric preset. The request intentionally has no
 * body and is never retried after a possibly-sent POST.
 */
export function runMetricQueryPreset(
  clusterId: string,
  presetId: string,
  signal?: AbortSignal,
): Promise<AgentDebugQueryReceipt> {
  const path =
    `/api/clusters/${encodePathSegment(clusterId)}/metric-query-presets/${encodePathSegment(presetId)}/run` as ApiPath;
  return apiRequest(path, agentDebugQueryReceiptSchema, {
    method: "POST",
    signal,
  });
}
