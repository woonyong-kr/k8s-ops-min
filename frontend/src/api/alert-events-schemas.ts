import { z } from "zod";

import { rfc3339TimestampSchema } from "./resource-filter-schemas";

export const alertEventSeveritySchema = z.enum([
  "critical",
  "high",
  "medium",
  "low",
  "warning",
  "info",
]);
export const alertEventStatusSchema = z.enum(["firing", "resolved", "acked"]);
export const alertEventSourceSchema = z.enum(["opsia", "alertmanager", "incident"]);

export const alertEventSubjectSchema = z.strictObject({
  cluster: z.string().min(1).max(512),
  namespace: z.string().max(253).nullable(),
  kind: z.string().min(1).max(253),
  name: z.string().min(1).max(253),
});

export const alertEvidenceItemSchema = z.strictObject({
  type: z.string().min(1).max(80),
  metric: z.string().min(1).max(120).nullable(),
  observed_at: rfc3339TimestampSchema.nullable(),
  subject: alertEventSubjectSchema.nullable(),
  value: z.number().finite().nullable(),
  summary: z.string().min(1).max(1000).nullable(),
  link: z.string().startsWith("/").nullable(),
}).refine((evidence) => (
  evidence.metric !== null ||
  evidence.observed_at !== null ||
  evidence.subject !== null ||
  evidence.value !== null ||
  evidence.summary !== null ||
  evidence.link !== null
), { message: "alert evidence must contain a material fact or reference" });

export const alertEventSchema = z.strictObject({
  event_id: z.string().min(1).max(120),
  rule_id: z.string().max(120).nullable(),
  rule_name: z.string().max(120).nullable(),
  source: alertEventSourceSchema,
  severity: alertEventSeveritySchema,
  subject: alertEventSubjectSchema,
  fired_at: rfc3339TimestampSchema,
  resolved_at: rfc3339TimestampSchema.nullable(),
  status: alertEventStatusSchema,
  observed_value: z.number().finite().nullable(),
  threshold: z.number().finite().nullable(),
  evidence: z.array(alertEvidenceItemSchema).min(1),
  incident_id: z.string().nullable(),
  acknowledged_at: rfc3339TimestampSchema.nullable(),
  acknowledged_by: z.string().nullable(),
  promoted_at: rfc3339TimestampSchema.nullable(),
  promoted_by: z.string().nullable(),
}).superRefine((event, context) => {
  if (
    event.source !== "opsia" ||
    (
      event.rule_id !== null &&
      event.rule_name !== null &&
      event.observed_value !== null &&
      event.threshold !== null
    )
  ) return;
  context.addIssue({
    code: "custom",
    message: "Kyro alert events require measured rule evidence",
  });
});

export const alertEventListSchema = z.array(alertEventSchema);
export const alertIncidentPromotionSchema = z.strictObject({
  incident_id: z.string().min(1).max(120),
});

export type AlertEvent = z.output<typeof alertEventSchema>;
export type AlertEventSeverity = z.output<typeof alertEventSeveritySchema>;
export type AlertEventStatus = z.output<typeof alertEventStatusSchema>;
export type AlertIncidentPromotion = z.output<typeof alertIncidentPromotionSchema>;
