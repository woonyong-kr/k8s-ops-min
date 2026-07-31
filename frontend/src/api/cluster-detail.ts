import { apiRequest, type ApiPath } from "./client";
import {
  clusterResponseSchema,
  type ClusterResponse,
} from "./cluster-schemas";
import { encodePathSegment } from "./url";

/** Loads the selected cluster and its registered agent statuses. */
export function getCluster(
  clusterId: string,
  signal?: AbortSignal,
): Promise<ClusterResponse> {
  const path = `/api/clusters/${encodePathSegment(clusterId)}` as ApiPath;
  return apiRequest(path, clusterResponseSchema, { signal });
}
