import { z } from "zod";

const nullableStringSchema = z.string().nullable();

const evidenceSourceSummarySchema = z.strictObject({
  source: z.string().min(1),
  summary: z.string(),
  schema_version: z.number().int().nullable(),
  collector: nullableStringSchema,
  collector_version: nullableStringSchema,
  source_version: nullableStringSchema,
  query_version: nullableStringSchema,
  collected_at: nullableStringSchema,
  evidence_key: nullableStringSchema,
  source_id: nullableStringSchema,
  agent_id: nullableStringSchema,
  window_start: nullableStringSchema,
});

export const evidenceRecordSchema = z.strictObject({
  id: z.number().int(),
  workspace_id: z.string().min(1),
  correlation_id: z.string().min(1),
  kind: z.string().min(1),
  cluster_id: nullableStringSchema,
  evidence_ref: nullableStringSchema,
  summary: z.string(),
  sources: z.array(evidenceSourceSummarySchema),
  created_at: nullableStringSchema,
});

export const evidenceListSchema = z.strictObject({
  items: z.array(evidenceRecordSchema),
  limit: z.number().int().positive(),
  offset: z.number().int().nonnegative(),
  has_more: z.boolean(),
  next_cursor: nullableStringSchema,
});

export const evidenceWindowPayloadSchema = z.strictObject({
  evidence_key: z.string().min(1),
  workspace_id: z.string().min(1),
  cluster_id: nullableStringSchema,
  source: nullableStringSchema,
  payload: z.record(z.string(), z.unknown()),
});

const rcaCandidateScoreSchema = z.strictObject({
  candidate_id: z.string().min(1),
  title: nullableStringSchema,
  source: nullableStringSchema,
  score: z.number().finite().nullable(),
  reason: nullableStringSchema,
  supporting_evidence: z.array(z.string()),
  missing_evidence: z.array(z.string()),
});

const rcaEvidenceRefSchema = z.strictObject({
  source: z.string().min(1),
  name: z.string().min(1),
  check_id: nullableStringSchema,
  summary: nullableStringSchema,
  query: nullableStringSchema,
  evidence_ref: nullableStringSchema,
  schema_version: z.number().int().nullable(),
  source_version: nullableStringSchema,
  collector: nullableStringSchema,
  collector_version: nullableStringSchema,
  query_version: nullableStringSchema,
  collected_at: nullableStringSchema,
  evidence_key: nullableStringSchema,
  source_id: nullableStringSchema,
  agent_id: nullableStringSchema,
  window_start: nullableStringSchema,
});

const rcaMissingCheckSchema = z.strictObject({
  check_id: z.string().min(1),
  source: nullableStringSchema,
  status: nullableStringSchema,
  reason: nullableStringSchema,
});

const rcaNarrativeSchema = z.strictObject({
  locale: z.literal("ko"),
  executive_summary: z.string().min(1),
  impact: z.string().min(1),
  reasoning: z.string().min(1),
  recommended_action: z.string().min(1),
  recurrence_prevention: z.array(z.string().min(1)).min(1),
  limitations: z.array(z.string().min(1)).min(1),
});

export const rcaReportSchema = z.strictObject({
  id: z.number().int(),
  workspace_id: z.string().min(1),
  correlation_id: z.string().min(1),
  analysis_status: z.enum(["completed", "blocked"]),
  root_cause: z.string(),
  action: z.string(),
  incident_id: nullableStringSchema,
  cluster_id: nullableStringSchema,
  symptom: nullableStringSchema,
  severity: nullableStringSchema,
  first_seen_at: nullableStringSchema,
  confidence: z.number().finite().nullable(),
  reason: nullableStringSchema,
  evidence_ref: nullableStringSchema,
  supporting_evidence: z.array(z.string()),
  missing_evidence: z.array(z.string()),
  evidence_summary: nullableStringSchema,
  evidence_bundle_summary: nullableStringSchema,
  created_at: nullableStringSchema,
  resource_kind: nullableStringSchema,
  resource_name: nullableStringSchema,
  namespace: nullableStringSchema,
  secondary_symptoms: z.array(z.string()),
  selected_candidate_id: nullableStringSchema,
  candidates: z.array(rcaCandidateScoreSchema),
  supporting_evidence_refs: z.array(rcaEvidenceRefSchema),
  missing_evidence_checks: z.array(rcaMissingCheckSchema),
  narrative: rcaNarrativeSchema.nullable(),
  narrative_status: z.enum(["generated", "unavailable"]),
});

export const rcaReportListSchema = z.strictObject({
  items: z.array(rcaReportSchema),
  limit: z.number().int().positive(),
  offset: z.number().int().nonnegative(),
  has_more: z.boolean(),
  next_cursor: nullableStringSchema,
});

export type EvidenceRecord = z.infer<typeof evidenceRecordSchema>;
export type EvidenceList = z.infer<typeof evidenceListSchema>;
export type EvidenceWindowPayload = z.infer<typeof evidenceWindowPayloadSchema>;
export type RcaReport = z.infer<typeof rcaReportSchema>;
export type RcaReportList = z.infer<typeof rcaReportListSchema>;
