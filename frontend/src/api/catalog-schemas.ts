import { z } from "zod";

export const catalogItemSchema = z.record(z.string(), z.unknown());

export const catalogItemListSchema = z.strictObject({
  items: z.array(catalogItemSchema),
});

export const catalogItemResponseSchema = z.strictObject({
  item: catalogItemSchema,
});

export type CatalogItem = z.infer<typeof catalogItemSchema>;
export type CatalogItemList = z.infer<typeof catalogItemListSchema>;
export type CatalogItemResponse = z.infer<typeof catalogItemResponseSchema>;
