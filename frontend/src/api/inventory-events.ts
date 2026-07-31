import { apiRequest, type ApiPath } from "./client";
import {
  inventoryEventListSchema,
  type InventoryEventList,
} from "./inventory-events-schemas";
import { encodePathSegment, optionalQueryString, withQuery } from "./url";

const DEFAULT_EVENT_LIMIT = 200;
const MIN_EVENT_LIMIT = 1;
const MAX_EVENT_LIMIT = 1000;

export interface InventoryEventListOptions {
  namespace?: string | null;
  limit?: number;
}

/** Loads sanitized Kubernetes events for the cluster activity/timeline views. */
export function listInventoryEvents(
  clusterId: string,
  options: InventoryEventListOptions = {},
  signal?: AbortSignal,
): Promise<InventoryEventList> {
  const limit = options.limit ?? DEFAULT_EVENT_LIMIT;
  assertEventLimit(limit);
  const path = withQuery(
    `/api/clusters/${encodePathSegment(clusterId)}/inventory/events` as ApiPath,
    [
      ["namespace", optionalQueryString(options.namespace)],
      ["limit", limit],
    ],
  );
  return apiRequest(path, inventoryEventListSchema, { signal });
}

function assertEventLimit(limit: number): void {
  if (!Number.isInteger(limit) || limit < MIN_EVENT_LIMIT || limit > MAX_EVENT_LIMIT) {
    throw new RangeError(`event limit must be an integer from ${MIN_EVENT_LIMIT} to ${MAX_EVENT_LIMIT}`);
  }
}
