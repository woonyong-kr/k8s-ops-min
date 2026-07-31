import { z } from "zod";

const countedFacetSchema = z.strictObject({
  id: z.string().min(1),
  label: z.string().min(1),
  count: z.number().int().nonnegative().nullable(),
  count_completeness: z.enum(["exact", "partial", "unavailable"]),
});

export const globalFilterFacetsSchema = z.strictObject({
  clusters: z.array(countedFacetSchema),
  namespaces: z.array(countedFacetSchema.extend({ cluster_id: z.string().min(1) })),
  applications: z.array(countedFacetSchema),
  resource_types: z.array(countedFacetSchema),
  labels: z.array(z.strictObject({
    key: z.string().min(1),
    value: z.string(),
    count: z.number().int().nonnegative().nullable(),
    count_completeness: z.enum(["exact", "partial", "unavailable"]),
  })),
  resources: z.array(countedFacetSchema.extend({ kind: z.string().min(1) })),
});

export type GlobalFilterFacets = z.infer<typeof globalFilterFacetsSchema>;
