import { apiRequest, type ApiPath } from "./client";
import {
  clusterConnectionStatusSchema,
  type ClusterConnectionStatus,
} from "./cluster-connection-schemas";
import { encodePathSegment } from "./url";

/** Loads the current management connection status for a selected cluster. */
export function getClusterConnectionStatus(
  clusterId: string,
  signal?: AbortSignal,
): Promise<ClusterConnectionStatus> {
  const path =
    `/api/clusters/${encodePathSegment(clusterId)}/connection-status` as ApiPath;
  return apiRequest(path, clusterConnectionStatusSchema, { signal });
}
