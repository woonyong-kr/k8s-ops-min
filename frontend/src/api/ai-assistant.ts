import { apiRequest, type ApiPath } from "./client";
import {
  aiAssistantContextSchema,
  aiChatResponseSchema,
  aiSuggestionsResponseSchema,
  type AiAssistantContextEndpoint,
  type AiChatResponseEndpoint,
  type AiSuggestionsResponseEndpoint,
} from "./ai-assistant-schemas";
import { withQuery } from "./url";

export const AI_CHAT_PATH: ApiPath = "/api/ai/chat";
export const AI_SUGGESTIONS_PATH: ApiPath = "/api/ai/suggestions";
export const MAX_AI_ASSISTANT_MESSAGE_LENGTH = 16_000;

export function postAiChat(
  context: AiAssistantContextEndpoint,
  message: string,
  signal?: AbortSignal,
): Promise<AiChatResponseEndpoint> {
  const payload = {
    context: aiAssistantContextSchema.parse(context),
    message: assistantMessage(message),
  };
  return apiRequest(AI_CHAT_PATH, aiChatResponseSchema, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
}

export function getAiSuggestions(
  context: AiAssistantContextEndpoint,
  signal?: AbortSignal,
): Promise<AiSuggestionsResponseEndpoint> {
  const canonical = aiAssistantContextSchema.parse(context);
  return apiRequest(
    withQuery(AI_SUGGESTIONS_PATH, [["context", JSON.stringify(canonical)]]),
    aiSuggestionsResponseSchema,
    { signal },
  );
}

function assistantMessage(value: string): string {
  const message = value.trim();
  if (!message) throw new TypeError("AI assistant message must not be empty");
  if (message.length > MAX_AI_ASSISTANT_MESSAGE_LENGTH) {
    throw new RangeError(`AI assistant message must be at most ${MAX_AI_ASSISTANT_MESSAGE_LENGTH} characters`);
  }
  return message;
}
