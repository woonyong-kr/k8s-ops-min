import { apiRequest, type ApiPath } from "./client";
import {
  inventoryResourceDetailSchema,
  inventoryResourceListSchema,
  type InventoryResourceDetail,
  type InventoryResourceList,
} from "./inventory-schemas";
import { encodePathSegment, optionalQueryString, withQuery } from "./url";

const HOME_INVENTORY_LIMIT = 1000;
const RESOURCE_LIST_LIMIT = 200;
// resource-detail endpoint validates related/event limits as positive integers.
// Use the minimum when a caller only needs the primary resource projection.
export const RESOURCE_DETAIL_MIN_LIMIT = 1;
const RELATED_RESOURCE_LIMIT = 100;
const RESOURCE_EVENT_LIMIT = 50;

export type HomeInventoryResourceType = "node" | "pod";

export interface InventoryResourceQuery {
  resourceType: HomeInventoryResourceType;
  namespace?: string | null;
  includeDeleted?: boolean;
  limit?: number;
}

export interface InventoryListOptions {
  namespace?: string | null;
  limit?: number;
}

export interface InventoryResourceIdentity {
  resourceType: string;
  kind: string;
  name: string;
  namespace?: string | null;
}

export interface InventoryResourceDetailOptions {
  relatedLimit?: number;
  eventLimit?: number;
}

/**
 * Lists real node or pod inventory for the Home node-frame treemap.
 * Backend source of truth: `src/domains/inventory/router.py::list_inventory_resources`.
 */
export function listInventoryResources(
  clusterId: string,
  query: InventoryResourceQuery,
  signal?: AbortSignal,
): Promise<InventoryResourceList> {
  const path = inventoryPath(clusterId, "resources", [
    ["resource_type", query.resourceType],
    ["namespace", optionalQueryString(query.namespace)],
    ["include_deleted", query.includeDeleted ?? false],
    ["limit", query.limit ?? HOME_INVENTORY_LIMIT],
  ]);
  return apiRequest(path, inventoryResourceListSchema, { signal });
}

/**
 * Lists service inventory for the Resources list screen.
 * Backend source of truth: `src/domains/inventory/router.py::list_inventory_services`.
 */
export function listInventoryServices(
  clusterId: string,
  options: InventoryListOptions = {},
  signal?: AbortSignal,
): Promise<InventoryResourceList> {
  return listInventoryCollection(clusterId, "services", options, signal);
}

/**
 * Lists workload inventory for the Resources list screen.
 * Backend source of truth: `src/domains/inventory/router.py::list_inventory_workloads`.
 */
export function listInventoryWorkloads(
  clusterId: string,
  options: InventoryListOptions = {},
  signal?: AbortSignal,
): Promise<InventoryResourceList> {
  return listInventoryCollection(clusterId, "workloads", options, signal);
}

/**
 * Loads the backend read model shown under an expanded Resources row.
 * Backend source of truth:
 * `src/domains/inventory/router.py::get_inventory_resource_detail`.
 */
export function getInventoryResourceDetail(
  clusterId: string,
  identity: InventoryResourceIdentity,
  options: InventoryResourceDetailOptions = {},
  signal?: AbortSignal,
): Promise<InventoryResourceDetail> {
  const path = inventoryPath(clusterId, "resource-detail", [
    ["resource_type", identity.resourceType],
    ["kind", identity.kind],
    ["name", identity.name],
    ["namespace", optionalQueryString(identity.namespace)],
    ["related_limit", options.relatedLimit ?? RELATED_RESOURCE_LIMIT],
    ["event_limit", options.eventLimit ?? RESOURCE_EVENT_LIMIT],
  ]);
  return apiRequest(path, inventoryResourceDetailSchema, { signal });
}

function listInventoryCollection(
  clusterId: string,
  collection: "services" | "workloads",
  options: InventoryListOptions,
  signal?: AbortSignal,
): Promise<InventoryResourceList> {
  const path = inventoryPath(clusterId, collection, [
    ["namespace", optionalQueryString(options.namespace)],
    ["limit", options.limit ?? RESOURCE_LIST_LIMIT],
  ]);
  return apiRequest(path, inventoryResourceListSchema, { signal });
}

function inventoryPath(
  clusterId: string,
  collection: "resources" | "services" | "workloads" | "resource-detail",
  query: Parameters<typeof withQuery>[1],
): ApiPath {
  const path = `/api/clusters/${encodePathSegment(clusterId)}/inventory/${collection}` as ApiPath;
  return withQuery(path, query);
}
