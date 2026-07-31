import {
  inventoryResourceListSchema,
  type InventoryResourceList,
} from "./inventory-schemas";

/** Kubernetes events use the same sanitized inventory read model as resources. */
export const inventoryEventListSchema = inventoryResourceListSchema;

export type InventoryEventList = InventoryResourceList;
