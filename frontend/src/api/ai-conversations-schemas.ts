import { z } from "zod";

const jsonMapSchema = z.record(z.string(), z.unknown());

export const aiConversationSummarySchema = jsonMapSchema;
export const aiConversationListSchema = z.strictObject({
  conversations: z.array(aiConversationSummarySchema),
});

export const aiConversationMessagesCompletenessSchema = z.enum([
  "complete",
  "partial",
]);

export const aiConversationDetailSchema = z.strictObject({
  conversation: jsonMapSchema,
  messages: z.array(jsonMapSchema),
  limit: z.number().int(),
  has_more: z.boolean(),
  next_cursor: z.string().nullable(),
  messages_completeness: aiConversationMessagesCompletenessSchema,
  partial_reason_codes: z.array(z.string()),
});

export const aiConversationAcceptedSchema = z.strictObject({
  accepted: z.boolean(),
  conversation_id: z.string().min(1),
  message_id: z.string().min(1),
  event_id: z.string().min(1),
  correlation_id: z.string().min(1),
});

export type AiConversationSummary = z.infer<
  typeof aiConversationSummarySchema
>;
export type AiConversationList = z.infer<typeof aiConversationListSchema>;
export type AiConversationMessagesCompleteness = z.infer<
  typeof aiConversationMessagesCompletenessSchema
>;
export type AiConversationDetail = z.infer<typeof aiConversationDetailSchema>;
export type AiConversationAccepted = z.infer<
  typeof aiConversationAcceptedSchema
>;
