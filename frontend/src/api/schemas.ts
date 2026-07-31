import { z } from "zod";

const nullableStringSchema = z.string().nullable();
const integerSchema = z.number().int();

export const authLogoutDescriptorSchema = z.strictObject({
  action: z.string(),
  supported: z.boolean(),
  reauthentication_expected: z.boolean(),
});

export const authSessionSchema = z.strictObject({
  authenticated: z.boolean(),
  auth_enabled: z.boolean().optional(),
  auth_mode: z.string().optional(),
  display_name: z.string().nullable().optional(),
  email: z.string().nullable().optional(),
  user_id: z.string(),
  groups: z.array(z.string()).optional(),
  roles: z.array(z.string()),
  workspace_id: z.string(),
  logout: authLogoutDescriptorSchema.optional(),
});

export const logoutResponseSchema = z.strictObject({
  authenticated: z.literal(false),
});

export const fleetHealthSchema = z.enum([
  "healthy",
  "warning",
  "critical",
  "stale",
  "unknown",
]);

export const fleetClusterSummarySchema = z.strictObject({
  cluster_id: z.string(),
  name: z.string(),
  health: fleetHealthSchema,
  pods_running: integerSchema,
  pods_total: integerSchema,
  nodes_ready: integerSchema,
  nodes_total: integerSchema,
  open_incidents: integerSchema,
  restarts_recent: integerSchema,
  cpu_pct: z.number().nullable(),
  mem_pct: z.number().nullable(),
  last_seen_at: nullableStringSchema,
});

export const fleetTotalsSchema = z.strictObject({
  clusters: integerSchema,
  healthy: integerSchema,
  warning: integerSchema,
  critical: integerSchema,
  stale: integerSchema,
  unknown: integerSchema,
  open_incidents: integerSchema,
  pending_approvals: integerSchema,
  running_workflows: integerSchema,
  dead_letters: integerSchema.nullable(),
});

export const fleetSummarySchema = z.strictObject({
  clusters: z.array(fleetClusterSummarySchema),
  totals: fleetTotalsSchema,
});

export const fleetSummaryStreamFrameSchema = z.strictObject({
  cursor: z.string().min(1),
  revision: z.string().regex(/^[0-9a-f]{64}$/u),
  generated_at: z.string().datetime({ offset: true }),
  refresh_after_ms: z.number().int().min(5_000).max(10_000),
  summary: fleetSummarySchema,
});

export const rcaTimelineItemSchema = z.strictObject({
  workspace_id: z.string(),
  correlation_id: z.string(),
  cluster_id: nullableStringSchema,
  incident_id: nullableStringSchema,
  incident_namespace: nullableStringSchema,
  incident_resource_kind: nullableStringSchema,
  incident_resource_name: nullableStringSchema,
  incident_symptom: nullableStringSchema,
  evidence_ref: nullableStringSchema,
  current_subject: z.string(),
  status: z.string(),
  root_cause: nullableStringSchema,
  confidence: z.number().nullable(),
  supporting_evidence: z.array(z.string()),
  missing_evidence: z.array(z.string()),
  action_route: nullableStringSchema,
  command_id: nullableStringSchema,
  pr_url: nullableStringSchema,
  error_reason: nullableStringSchema,
  updated_at: nullableStringSchema,
});

export const rcaTimelineSchema = z.strictObject({
  items: z.array(rcaTimelineItemSchema),
});

/**
 * Additive queue contract.  It intentionally remains separate from the legacy
 * timeline schema so an older strict frontend can keep consuming timeline
 * responses while a new frontend rolls out the richer Issue presentation.
 */
export const rcaIssueItemSchema = rcaTimelineItemSchema.extend({
  incident_occurrence_id: nullableStringSchema.optional(),
  issue_severity: z.enum(["critical", "warning"]).nullable(),
  severity_availability: z.enum(["available", "unavailable"]),
  severity_reason_code: z.enum([
    "source_incomplete",
    "outside_two_tier_scale",
  ]).nullable(),
  // RcaIssueItem AI-authored summaries (str | None, always emitted).
  situation_summary: z.string().nullable(),
  recommended_action_summary: z.string().nullable(),
  evidence_summary: z.string().nullable(),
  evidence_bundle_summary: z.string().nullable(),
  recovery_reason_code: z.string().nullable().optional().default(null),
}).superRefine((item, context) => {
  if (
    item.severity_availability === "available"
    && (item.issue_severity === null || item.severity_reason_code !== null)
  ) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: "available severity requires a tier" });
  }
  if (
    item.severity_availability === "unavailable"
    && (item.issue_severity !== null || item.severity_reason_code === null)
  ) {
    context.addIssue({ code: z.ZodIssueCode.custom, message: "unavailable severity requires a reason" });
  }
});

export const rcaIssueListSchema = z.strictObject({
  items: z.array(rcaIssueItemSchema),
});

export type AuthSession = z.infer<typeof authSessionSchema>;
export type FleetHealth = z.infer<typeof fleetHealthSchema>;
export type FleetClusterSummary = z.infer<typeof fleetClusterSummarySchema>;
export type FleetTotals = z.infer<typeof fleetTotalsSchema>;
export type FleetSummary = z.infer<typeof fleetSummarySchema>;
export type FleetSummaryStreamFrame = z.infer<typeof fleetSummaryStreamFrameSchema>;
export type RcaTimelineItem = z.infer<typeof rcaTimelineItemSchema>;
export type RcaTimeline = z.infer<typeof rcaTimelineSchema>;
export type RcaIssueItem = z.infer<typeof rcaIssueItemSchema>;
export type RcaIssueList = z.infer<typeof rcaIssueListSchema>;
