import { z } from "zod";

/** Runtime contract for `GET /repositories/connection-status`. */
export const repositoryConnectionStatusSchema = z.strictObject({
  repo_ref: z.string().min(1),
  repository_id: z.string().min(1).nullable().optional(),
  repository_status: z.enum([
    "unregistered",
    "active",
    "invalid_credential",
    "disabled",
    "source_unreachable",
    "disconnected",
    "unknown",
  ]),
  connection_stage: z.enum(["awaiting_validation", "ready", "error"]),
  terminal: z.boolean(),
  refresh_after_seconds: z.number().min(0.25).max(30).nullable(),
  // 비정상/해제 사유(선택) — 없으면 종전과 동일(하위호환).
  degraded_reason: z
    .enum([
      "credential_invalid",
      "source_unreachable",
      "permission_revoked",
      "disconnected",
      "disabled",
    ])
    .nullable()
    .optional(),
});

export type RepositoryConnectionStatus = z.infer<
  typeof repositoryConnectionStatusSchema
>;

/** Runtime contract for `GET /repositories` (연결 상태 관리 목록). */
export const repositoryListItemSchema = z.strictObject({
  repo_ref: z.string().min(1),
  repository_id: z.string().min(1),
  provider: z.string(),
  default_branch: z.string(),
  repository_status: z.enum([
    "active",
    "invalid_credential",
    "disabled",
    "source_unreachable",
    "disconnected",
    "unknown",
  ]),
  degraded_reason: z
    .enum([
      "credential_invalid",
      "source_unreachable",
      "permission_revoked",
      "disconnected",
      "disabled",
    ])
    .nullable()
    .optional(),
  application_count: z.number().int().nonnegative(),
  updated_at: z.string().nullable().optional(),
});
export type RepositoryListItem = z.infer<typeof repositoryListItemSchema>;

export const repositoryListSchema = z.strictObject({
  repositories: z.array(repositoryListItemSchema),
});
export type RepositoryList = z.infer<typeof repositoryListSchema>;
