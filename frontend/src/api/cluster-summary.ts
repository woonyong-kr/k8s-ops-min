import { apiRequest, type ApiPath } from "./client";
import {
  clusterNodesSummarySchema,
  clusterSummaryDetailSchema,
  nodePodsSummarySchema,
  type ClusterNodesSummary,
  type ClusterSummaryDetail,
  type NodePodsSummary,
} from "./cluster-summary-schemas";
import { encodePathSegment } from "./url";

export function getClusterSummary(
  clusterId: string,
  signal?: AbortSignal,
): Promise<ClusterSummaryDetail> {
  return apiRequest(
    `/api/clusters/${encodePathSegment(clusterId)}/summary` as ApiPath,
    clusterSummaryDetailSchema,
    { signal },
  );
}

export function getClusterNodesSummary(
  clusterId: string,
  signal?: AbortSignal,
): Promise<ClusterNodesSummary> {
  return apiRequest(
    `/api/clusters/${encodePathSegment(clusterId)}/nodes/summary` as ApiPath,
    clusterNodesSummarySchema,
    { signal },
  );
}

export function getNodePodsSummary(
  clusterId: string,
  nodeName: string,
  signal?: AbortSignal,
): Promise<NodePodsSummary> {
  return apiRequest(
    `/api/clusters/${encodePathSegment(clusterId)}/nodes/${encodePathSegment(nodeName)}/pods/summary` as ApiPath,
    nodePodsSummarySchema,
    { signal },
  );
}
