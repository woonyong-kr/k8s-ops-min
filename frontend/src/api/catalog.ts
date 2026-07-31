import { apiRequest, type ApiPath } from "./client";
import {
  catalogItemListSchema,
  catalogItemResponseSchema,
  type CatalogItemList,
  type CatalogItemResponse,
} from "./catalog-schemas";
import { encodePathSegment } from "./url";

/** Lists services available in the platform catalog. */
export function listCatalogItems(
  signal?: AbortSignal,
): Promise<CatalogItemList> {
  return apiRequest("/api/catalog/items" as ApiPath, catalogItemListSchema, {
    signal,
  });
}

/** Loads one catalog service and its versions/configuration metadata. */
export function getCatalogItem(
  itemId: string,
  signal?: AbortSignal,
): Promise<CatalogItemResponse> {
  if (!itemId.trim()) {
    throw new TypeError("itemId must not be empty");
  }
  const path = `/api/catalog/items/${encodePathSegment(itemId)}` as ApiPath;
  return apiRequest(path, catalogItemResponseSchema, { signal });
}
