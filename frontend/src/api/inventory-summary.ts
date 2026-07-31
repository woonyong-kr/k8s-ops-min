import { apiRequest, type ApiPath } from "./client";
import {
  inventorySummarySchema,
  type InventorySummary,
} from "./inventory-summary-schemas";
import { encodePathSegment } from "./url";

/** Loads the latest inventory snapshot and resource counts for a cluster. */
export function getInventorySummary(
  clusterId: string,
  signal?: AbortSignal,
): Promise<InventorySummary> {
  const path =
    `/api/clusters/${encodePathSegment(clusterId)}/inventory/summary` as ApiPath;
  return apiRequest(path, inventorySummarySchema, { signal });
}
