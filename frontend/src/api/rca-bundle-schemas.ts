import { z } from "zod";

import { recoveryActionCandidateSchema } from "./recovery-schemas";

const nullableStringSchema = z.string().nullable();
const optionalNullableStringSchema = nullableStringSchema.optional();

export const remediationBundleEvidenceRefSchema = z.strictObject({
  source: z.string(),
  name: z.string(),
  check_id: optionalNullableStringSchema,
  summary: optionalNullableStringSchema,
  query: optionalNullableStringSchema,
  evidence_ref: optionalNullableStringSchema,
  schema_version: z.number().int().nullable().optional(),
  source_version: optionalNullableStringSchema,
  collector: optionalNullableStringSchema,
  collector_version: optionalNullableStringSchema,
  query_version: optionalNullableStringSchema,
  collected_at: optionalNullableStringSchema,
  evidence_key: optionalNullableStringSchema,
  source_id: optionalNullableStringSchema,
  agent_id: optionalNullableStringSchema,
  window_start: optionalNullableStringSchema,
});

export const remediationBundleMissingCheckSchema = z.strictObject({
  check_id: z.string(),
  source: optionalNullableStringSchema,
  status: optionalNullableStringSchema,
  reason: optionalNullableStringSchema,
});

export const remediationBundleActionDraftSchema = z.strictObject({
  action_type: z.string(),
  namespace: z.string(),
  resource_kind: z.string(),
  resource_name: z.string(),
  reason: z.string(),
  risk_level: z.string(),
  dry_run: z.boolean(),
  source_evidence: z.array(z.string()),
  params: z.record(z.string(), z.unknown()),
});

export const remediationBundleRecoveryCandidateSchema = recoveryActionCandidateSchema.extend({
  draft: remediationBundleActionDraftSchema,
});

export const remediationBundleRemediationSchema = z.strictObject({
  status: z.string(),
  selected_action_id: nullableStringSchema,
  selected_by: nullableStringSchema,
  candidates: z.array(remediationBundleRecoveryCandidateSchema),
  evidence_ref: z.string(),
});

export const remediationBundleMetaSchema = z.strictObject({
  correlation_id: z.string(),
  incident_id: nullableStringSchema,
  cluster_id: z.string(),
  workspace_id: z.string(),
  created_at: nullableStringSchema,
});

export const remediationBundleDiagnosisSchema = z.strictObject({
  root_cause: z.string(),
  confidence: z.number().nullable(),
  supporting_evidence: z.array(z.string()),
  missing_evidence: z.array(z.string()),
  supporting_evidence_refs: z.array(remediationBundleEvidenceRefSchema),
  missing_evidence_checks: z.array(remediationBundleMissingCheckSchema),
  selected_candidate_id: nullableStringSchema,
});

export const remediationBundleResponseSchema = z.strictObject({
  meta: remediationBundleMetaSchema,
  diagnosis: remediationBundleDiagnosisSchema,
  remediation: remediationBundleRemediationSchema.nullable(),
});

export type RemediationBundleEvidenceRef = z.infer<
  typeof remediationBundleEvidenceRefSchema
>;
export type RemediationBundleMissingCheck = z.infer<
  typeof remediationBundleMissingCheckSchema
>;
export type RemediationBundleActionDraft = z.infer<
  typeof remediationBundleActionDraftSchema
>;
export type RemediationBundleRecoveryCandidate = z.infer<
  typeof remediationBundleRecoveryCandidateSchema
>;
export type RemediationBundleRemediation = z.infer<
  typeof remediationBundleRemediationSchema
>;
export type RemediationBundleDiagnosis = z.infer<
  typeof remediationBundleDiagnosisSchema
>;
export type RemediationBundleMeta = z.infer<typeof remediationBundleMetaSchema>;
export type RemediationBundleResponse = z.infer<
  typeof remediationBundleResponseSchema
>;
