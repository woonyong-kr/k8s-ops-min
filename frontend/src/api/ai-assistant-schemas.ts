import { z } from "zod";

import { alertRuleCreateRequestSchema } from "./alert-rules-schemas";

const stringList = z.array(z.string().min(1));

export const aiAssistantFiltersSchema = z.strictObject({
  clusters: stringList,
  namespaces: stringList,
  applications: stringList,
  labels: stringList,
  resource_types: stringList,
  health: stringList,
  query: z.string(),
});

export const aiAssistantSelectionSchema = z.strictObject({
  type: z.literal("resource"),
  identity: z.string().min(1),
}).nullable();

export const aiAssistantContextSchema = z.strictObject({
  screen: z.string().min(1),
  filters: aiAssistantFiltersSchema,
  selection: aiAssistantSelectionSchema,
  time: z.string().datetime({ offset: true }).nullable(),
  log_stream_id: z.string().min(1).nullable(),
});

export const aiEvidenceLinkSchema = z.strictObject({
  type: z.string().min(1),
  id: z.string().min(1),
  label: z.string().min(1),
  link: z.string().regex(/^\/(?!\/)[^\s]*$/u),
});

export const aiChatActionSchema = z.strictObject({
  type: z.literal("create_alert_rule"),
  payload: alertRuleCreateRequestSchema,
  rationale: z.string().min(1).max(1_000),
});

export const aiChatResponseSchema = z.strictObject({
  answer: z.string().min(1),
  evidence: z.array(aiEvidenceLinkSchema),
  action: aiChatActionSchema.nullable().optional(),
  answer_kind: z.enum(["capability", "clarification"]).nullable().optional(),
}).superRefine((response, context) => {
  const ids = response.evidence.map((item) => `${item.type}\u001f${item.id}`);
  if (new Set(ids).size !== ids.length) {
    context.addIssue({ code: "custom", message: "AI evidence must be unique", path: ["evidence"] });
  }
});

export const aiSuggestionSchema = z.strictObject({
  id: z.string().min(1),
  label: z.string().min(1),
  prompt: z.string().min(1),
});

export const aiSuggestionsResponseSchema = z.strictObject({
  suggestions: z.array(aiSuggestionSchema),
});

export type AiAssistantContextEndpoint = z.infer<typeof aiAssistantContextSchema>;
export type AiChatResponseEndpoint = z.infer<typeof aiChatResponseSchema>;
export type AiSuggestionsResponseEndpoint = z.infer<typeof aiSuggestionsResponseSchema>;
