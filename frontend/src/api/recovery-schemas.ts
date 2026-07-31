import { z } from "zod";

const jsonMapSchema = z.record(z.string(), z.unknown());

export const recoveryActionCandidateSchema = z.strictObject({
  action_id: z.string().min(1),
  title: z.string(),
  description: z.string(),
  route: z.string(),
  rank: z.number().int(),
  score: z.number().finite(),
  risk_level: z.string(),
  blast_radius: z.string(),
  approval_required: z.boolean(),
  prerequisites: z.array(z.string()),
  validation_checks: z.array(z.string()),
  rollback_plan: z.string(),
  evidence_refs: z.array(z.string()),
  recommendation_reason: z.string().nullable().optional(),
  expected_outcome: z.string().nullable().optional(),
  risk_explanation: z.string().nullable().optional(),
  rollback_reason: z.string().nullable().optional(),
  executable: z.boolean().optional().default(true),
  blocked_reason_code: z.string().nullable().optional().default(null),
  blocked_reason: z.string().nullable().optional().default(null),
});

export const recoveryPlanSchema = z.strictObject({
  plan_id: z.string().min(1),
  correlation_id: z.string().min(1),
  incident_id: z.string().min(1),
  evidence_ref: z.string().min(1),
  status: z.string().min(1),
  summary: z.string(),
  target: jsonMapSchema,
  recommended_action_id: z.string().min(1),
  execution_route: z.string(),
  selection_required: z.boolean(),
  selected_action_id: z.string().nullable().optional().default(null),
  selected_by: z.string().nullable().optional().default(null),
  selected_action: recoveryActionCandidateSchema.nullable().optional().default(null),
  candidates: z.array(recoveryActionCandidateSchema),
  lifecycle: jsonMapSchema.nullable().optional(),
});

export const recoveryActionAcceptedSchema = z.strictObject({
  accepted: z.boolean(),
  event_id: z.string().min(1),
  correlation_id: z.string().min(1),
  command_id: z.string().min(1).nullable(),
});

export type RecoveryActionCandidate = z.infer<
  typeof recoveryActionCandidateSchema
>;
export type RecoveryPlan = z.infer<typeof recoveryPlanSchema>;
export type RecoveryActionAccepted = z.infer<
  typeof recoveryActionAcceptedSchema
>;
