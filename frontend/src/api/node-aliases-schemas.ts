import { z } from "zod";

export const NODE_ALIAS_MAX_LENGTH = 80;

const nonEmptyString = z.string().min(1);
const nodeAliasSchema = z.string().trim().min(1).max(NODE_ALIAS_MAX_LENGTH);

export const nodeAliasItemSchema = z.strictObject({
  cluster_id: nonEmptyString,
  node_name: nonEmptyString,
  alias: nodeAliasSchema,
  revision: z.number().int().positive(),
  updated_at: z.string().nullable(),
});

export const nodeAliasListResponseSchema = z.strictObject({
  cluster_id: nonEmptyString,
  aliases: z.array(nodeAliasItemSchema),
});

export const nodeAliasUpdateRequestSchema = z.strictObject({
  alias: nodeAliasSchema,
});

export type NodeAliasItem = z.output<typeof nodeAliasItemSchema>;
export type NodeAliasListResponse = z.output<typeof nodeAliasListResponseSchema>;
export type NodeAliasUpdateRequest = z.output<typeof nodeAliasUpdateRequestSchema>;
