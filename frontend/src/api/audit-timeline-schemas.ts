import { z } from "zod";

const auditPayloadSummarySchema = z.record(z.string(), z.unknown());

export const auditTimelineItemSchema = z.strictObject({
  event_id: z.string().min(1),
  subject: z.string(),
  source: z.string(),
  created_at: z.string(),
  causation_id: z.string().nullable(),
  journey_stage: z.enum([
    "alert",
    "evidence",
    "rca",
    "recovery",
    "command",
    "pr",
    "workflow",
    "cluster",
    "ai",
    "notification",
    "system",
    "unknown",
  ]),
  payload_summary: auditPayloadSummarySchema,
});

export const auditTimelineResponseSchema = z.strictObject({
  items: z.array(auditTimelineItemSchema),
  limit: z.number().int().min(1).max(200),
  has_more: z.boolean(),
  next_cursor: z.string().nullable(),
});

export type AuditTimelineItem = z.infer<typeof auditTimelineItemSchema>;
export type AuditTimelineResponse = z.infer<typeof auditTimelineResponseSchema>;
