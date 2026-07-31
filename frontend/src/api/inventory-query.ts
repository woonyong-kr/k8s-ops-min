import { apiRequest, type ApiPath } from "./client";
import {
  inventoryQueryResponseSchema,
  type InventoryQueryResponse,
} from "./inventory-query-schemas";
import { encodePathSegment, optionalQueryString, withQuery } from "./url";

const DEFAULT_RESOURCE_QUERY_LIMIT = 200;
const MIN_RESOURCE_QUERY_LIMIT = 1;
const MAX_RESOURCE_QUERY_LIMIT = 1000;

export interface InventoryResourceTypeQuery {
  resourceType: string;
  namespace?: string | null;
  includeDeleted?: boolean;
  limit?: number;
}

/** Queries the generic inventory collection for one resource type. */
export function listInventoryResourcesByType(
  clusterId: string,
  query: InventoryResourceTypeQuery,
  signal?: AbortSignal,
): Promise<InventoryQueryResponse> {
  const resourceType = query.resourceType.trim();
  if (resourceType === "") {
    throw new TypeError("resourceType must not be empty");
  }

  const limit = query.limit ?? DEFAULT_RESOURCE_QUERY_LIMIT;
  assertResourceQueryLimit(limit);
  const path = withQuery(
    `/api/clusters/${encodePathSegment(clusterId)}/inventory/resources` as ApiPath,
    [
      ["resource_type", resourceType],
      ["namespace", optionalQueryString(query.namespace)],
      ["include_deleted", query.includeDeleted ?? false],
      ["limit", limit],
    ],
  );
  return apiRequest(path, inventoryQueryResponseSchema, { signal });
}

function assertResourceQueryLimit(limit: number): void {
  if (
    !Number.isInteger(limit) ||
    limit < MIN_RESOURCE_QUERY_LIMIT ||
    limit > MAX_RESOURCE_QUERY_LIMIT
  ) {
    throw new RangeError(
      `resource query limit must be an integer from ${MIN_RESOURCE_QUERY_LIMIT} to ${MAX_RESOURCE_QUERY_LIMIT}`,
    );
  }
}
