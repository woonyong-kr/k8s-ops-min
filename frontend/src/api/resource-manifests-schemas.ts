import { z } from "zod";

import { commandAcceptedSchema } from "./commands-schemas";

export const resourceManifestSourceChoiceSchema = z.strictObject({
  application_id: z.string(),
  application_name: z.string(),
  repository_ref: z.string(),
  branch: z.string(),
  manifest_path: z.string(),
  environment: z.string(),
});

export const resourceManifestSourceSchema = z.strictObject({
  resource_id: z.string(),
  status: z.enum(["available", "ambiguous", "unsupported"]),
  choices: z.array(resourceManifestSourceChoiceSchema),
  selected: resourceManifestSourceChoiceSchema.nullable(),
  base_sha: z.string().nullable(),
  source_sha256: z.string().nullable(),
  source_revision_token: z.string().nullable().optional().default(null),
  content: z.string().nullable(),
  reason: z.string().nullable(),
  // live/edit projection은 backend와 console의 순차 배포 호환 경계다. 구버전
  // backend가 필드를 생략하면 관측 불가로 정규화하고, 값을 지어내지 않는다.
  live_yaml: z.string().nullable().optional().default(null),
  live_observed_at: z.string().nullable().optional().default(null),
  live_reason: z.string().nullable().optional().default(null),
  edit_target: z.strictObject({
    resource_id: z.string().min(1),
    relationship: z.enum(["self", "owner"]),
    kind: z.string().min(1),
    namespace: z.string().nullable(),
    name: z.string().min(1),
  }).nullable().optional().default(null),
});

export const resourceManifestPreviewSchema = z.strictObject({
  valid: z.boolean(),
  changed: z.boolean(),
  base_sha: z.string(),
  source_sha256: z.string(),
  desired_sha256: z.string(),
  diff: z.string(),
  errors: z.array(z.string()),
  warnings: z.array(z.string()),
  apply_availability: z.enum(["available", "unavailable"]),
  apply_reason_codes: z.array(z.string()),
  impact: z.array(z.strictObject({
    api_version: z.string().min(1),
    kind: z.string().min(1),
    namespace: z.string().nullable(),
    name: z.string().min(1),
    selected: z.boolean(),
  })),
});

export const resourceManifestApproveSchema = z.strictObject({
  accepted: z.boolean(),
  event_id: z.string(),
  correlation_id: z.string(),
  workflow_run_id: z.string(),
  approval_id: z.string(),
  sync_state: z.literal("awaiting_pr_merge"),
});

export type ResourceManifestSourceEndpoint = z.infer<typeof resourceManifestSourceSchema>;
export type ResourceManifestPreviewEndpoint = z.infer<typeof resourceManifestPreviewSchema>;
export type ResourceManifestApproveEndpoint = z.infer<typeof resourceManifestApproveSchema>;
export const resourceManifestApplySchema = commandAcceptedSchema;
export type ResourceManifestApplyEndpoint = z.infer<typeof resourceManifestApplySchema>;
