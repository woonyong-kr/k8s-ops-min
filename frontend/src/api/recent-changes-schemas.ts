import { z } from "zod";

export const recentChangeItemSchema = z.strictObject({
  event_id: z.string(),
  changed_at: z.string(),
  namespace: z.string(),
  resource_kind: z.string(),
  resource_name: z.string(),
  image_before: z.string().nullable(),
  image_after: z.string().nullable(),
  pr_url: z.string().nullable(),
  commit_sha: z.string(),
  repository_id: z.string(),
  repo_ref: z.string(),
  workflow_run_id: z.string(),
});

export const recentChangeListResponseSchema = z.strictObject({
  incident_id: z.string(),
  items: z.array(recentChangeItemSchema),
  limit: z.number().int().min(1).max(50),
});

export type RecentChangeItem = z.infer<typeof recentChangeItemSchema>;
export type RecentChangeListResponse = z.infer<typeof recentChangeListResponseSchema>;
