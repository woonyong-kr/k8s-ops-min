import { z } from "zod";

export const approvalDecisionRequestSchema = z.strictObject({
  reason: z.string().nullable().optional(),
});

/** Shared response for a GitOps approval decision event. */
export const approvalDecisionResponseSchema = z.strictObject({
  accepted: z.boolean(),
  event_id: z.string().min(1),
  correlation_id: z.string().min(1),
});

export type ApprovalDecisionResponse = z.infer<
  typeof approvalDecisionResponseSchema
>;
export type ApprovalDecisionRequest = z.infer<typeof approvalDecisionRequestSchema>;
