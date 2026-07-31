import {
  inventoryResourceListSchema,
  type InventoryResourceList,
} from "./inventory-schemas";

/** Resource-type queries return the shared sanitized inventory list contract. */
export const inventoryQueryResponseSchema = inventoryResourceListSchema;

export type InventoryQueryResponse = InventoryResourceList;
