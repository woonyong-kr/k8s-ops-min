import { apiRequest, apiRequestNoContent, type ApiPath } from "./client";
import {
  nodeAliasItemSchema,
  nodeAliasListResponseSchema,
  nodeAliasUpdateRequestSchema,
  type NodeAliasItem,
  type NodeAliasListResponse,
} from "./node-aliases-schemas";
import { encodePathSegment } from "./url";

export function listNodeAliases(
  clusterId: string,
  signal?: AbortSignal,
): Promise<NodeAliasListResponse> {
  return apiRequest(
    `/api/clusters/${encodePathSegment(clusterId)}/nodes/aliases` as ApiPath,
    nodeAliasListResponseSchema,
    { signal },
  );
}

export function updateNodeAlias(
  clusterId: string,
  nodeName: string,
  alias: string,
  signal?: AbortSignal,
): Promise<NodeAliasItem> {
  const request = nodeAliasUpdateRequestSchema.parse({ alias });
  return apiRequest(
    `/api/clusters/${encodePathSegment(clusterId)}/nodes/${encodePathSegment(nodeName)}/alias` as ApiPath,
    nodeAliasItemSchema,
    {
      body: JSON.stringify(request),
      headers: { "content-type": "application/json" },
      method: "PUT",
      signal,
    },
  );
}

export function deleteNodeAlias(
  clusterId: string,
  nodeName: string,
  signal?: AbortSignal,
): Promise<void> {
  return apiRequestNoContent(
    `/api/clusters/${encodePathSegment(clusterId)}/nodes/${encodePathSegment(nodeName)}/alias` as ApiPath,
    {
      method: "DELETE",
      signal,
    },
  );
}
