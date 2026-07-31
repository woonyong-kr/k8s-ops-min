import { z } from "zod";

const jsonMapSchema = z.record(z.string(), z.unknown());

export const releasePlanStepSchema = z.looseObject({
  step_id: z.string().optional(),
  application_id: z.string(),
  name: z.string(),
  position: z.number().int().nonnegative(),
  depends_on: z.array(z.string()),
  config: jsonMapSchema,
});

export const releasePlanSchema = z.looseObject({
  plan_id: z.string().optional(),
  name: z.string(),
  description: z.string(),
  status: z.enum(["draft", "active", "paused", "archived"]),
  settings: jsonMapSchema,
  steps: z.array(releasePlanStepSchema),
  updated_at: z.string().optional(),
});

export const releasePlanListSchema = z.strictObject({
  plans: z.array(releasePlanSchema),
});

export const releasePlanResponseSchema = z.strictObject({
  plan: releasePlanSchema,
});

export const releasePreviewStepSchema = z.looseObject({
  step_id: z.string(),
  application_id: z.string(),
  name: z.string(),
  position: z.number().int().nonnegative(),
  wave: z.number().int().nullable(),
  blocked_by: z.array(z.string()),
  gate: z.string(),
  strategy: z.string(),
  environment: z.string(),
  action: z.string(),
});

export const releasePreviewSchema = z.looseObject({
  plan_id: z.string().optional(),
  executable: z.boolean(),
  summary: z.string(),
  waves: z.array(z.looseObject({
    wave: z.number().int(),
    step_ids: z.array(z.string()),
    applications: z.array(z.string()),
  })),
  steps: z.array(releasePreviewStepSchema),
  blockers: z.array(z.string()),
});

export const releasePreviewResponseSchema = z.strictObject({
  preview: releasePreviewSchema,
});

export const releaseReadinessSchema = z.looseObject({
  ready: z.boolean(),
  mode: z.string(),
  summary: z.string(),
  checks: z.array(z.looseObject({
    check_id: z.string(),
    name: z.string(),
    status: z.string(),
    message: z.string(),
    blockers: z.array(z.string()),
  })),
  impact: z.looseObject({
    summary: z.string(),
    runtime_mode: z.string(),
    live_side_effects: z.boolean(),
    total_steps: z.number().int().nonnegative(),
    total_waves: z.number().int().nonnegative(),
    first_wave: z.number().int(),
    applications: z.array(z.string()),
    environments: z.array(z.string()),
    production_targets: z.array(z.string()),
    production_target_count: z.number().int().nonnegative(),
    first_wave_steps: z.array(jsonMapSchema),
  }).optional(),
  next_actions: z.array(z.looseObject({
    action_id: z.string(),
    check_id: z.string(),
    label: z.string(),
    severity: z.string(),
    message: z.string(),
    blockers: z.array(z.string()),
  })),
  blockers: z.array(z.string()),
  warnings: z.array(z.string()),
});

export const releaseRunStepSchema = z.looseObject({
  run_step_id: z.string(),
  application_id: z.string(),
  name: z.string(),
  wave: z.number().int(),
  status: z.string(),
  workflow_run_id: z.string().optional(),
  event_id: z.string().optional(),
  correlation_id: z.string().optional(),
  approval_id: z.string().nullable().optional(),
  health: jsonMapSchema,
  rollback: jsonMapSchema,
  details: jsonMapSchema,
  workflow: jsonMapSchema.optional(),
});

export const releaseRunSchema = z.looseObject({
  run_id: z.string(),
  plan_id: z.string(),
  plan_name: z.string(),
  status: z.string(),
  derived_status: z.string().optional(),
  current_wave: z.number().int(),
  total_waves: z.number().int().nonnegative(),
  started_by: z.string().optional(),
  settings: jsonMapSchema,
  github: jsonMapSchema,
  rollback: jsonMapSchema,
  health: jsonMapSchema,
  attention: jsonMapSchema.optional(),
  steps: z.array(releaseRunStepSchema),
  events: z.array(z.looseObject({
    audit_id: z.string(),
    event_type: z.string(),
    message: z.string(),
    actor: z.string().optional(),
    details: jsonMapSchema,
    created_at: z.string().optional(),
  })),
  created_at: z.string().optional(),
  updated_at: z.string().optional(),
});

export const releaseRunListSchema = z.strictObject({
  runs: z.array(releaseRunSchema),
});

export const releaseRunResponseSchema = z.strictObject({
  run: releaseRunSchema,
});

export const releaseGeneratedManifestSchema = z.looseObject({
  manifest: z.string(),
  files: z.array(z.looseObject({
    path: z.string(),
    content: z.string(),
    action: z.string(),
    description: z.string(),
  })),
  resources: z.array(z.looseObject({
    api_version: z.string(),
    kind: z.string(),
    namespace: z.string(),
    name: z.string(),
  })),
  resource_count: z.number().int().nonnegative(),
  diagnostics: z.array(z.looseObject({
    source: z.string(),
    severity: z.string(),
    message: z.string(),
    code: z.string(),
    line: z.number().int(),
    column: z.number().int(),
    end_line: z.number().int(),
    end_column: z.number().int(),
    path: z.string().optional(),
    action: z.string().nullable().optional(),
  })),
  warnings: z.array(z.string()),
  summary: z.string(),
});

export const releaseSafePrSchema = releaseGeneratedManifestSchema.extend({
  accepted: z.boolean(),
  event_id: z.string(),
  correlation_id: z.string(),
  workflow_run_id: z.string(),
  application_id: z.string(),
  repo_ref: z.string(),
  base_branch: z.string(),
  manifest_path: z.string(),
  commit_sha: z.string(),
  patch_sha256: z.string(),
});

export type ReleasePlanApi = z.infer<typeof releasePlanSchema>;
export type ReleasePlanStepApi = z.infer<typeof releasePlanStepSchema>;
export type ReleasePreviewApi = z.infer<typeof releasePreviewSchema>;
export type ReleaseReadinessApi = z.infer<typeof releaseReadinessSchema>;
export type ReleaseRunApi = z.infer<typeof releaseRunSchema>;
export type ReleaseGeneratedManifestApi = z.infer<typeof releaseGeneratedManifestSchema>;
export type ReleaseSafePrApi = z.infer<typeof releaseSafePrSchema>;
