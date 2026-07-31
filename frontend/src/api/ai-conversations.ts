import { apiRequest, apiRequestNoContent, type ApiPath } from "./client";
import {
  aiConversationAcceptedSchema,
  aiConversationDetailSchema,
  aiConversationListSchema,
  type AiConversationAccepted,
  type AiConversationDetail,
  type AiConversationList,
} from "./ai-conversations-schemas";
import { encodePathSegment } from "./url";

export const MAX_AI_MESSAGE_LENGTH = 16_000;

export interface AiConversationContext {
  [key: string]: unknown;
}

export interface AiConversationCreateInput {
  message: string;
  title?: string;
  agent?: string;
  context?: AiConversationContext;
}

export interface AiMessageInput {
  message: string;
  agent?: string;
  context?: AiConversationContext;
}

/** Lists the signed-in user's AI conversations without message bodies. */
export function listAiConversations(
  signal?: AbortSignal,
): Promise<AiConversationList> {
  return apiRequest("/api/ai/conversations" as ApiPath, aiConversationListSchema, {
    signal,
  });
}

/** Loads one conversation with its messages and tool metadata. */
export function getAiConversation(
  conversationId: string,
  signal?: AbortSignal,
): Promise<AiConversationDetail> {
  const path =
    `/api/ai/conversations/${encodePathSegment(assertConversationId(conversationId))}` as ApiPath;
  return apiRequest(path, aiConversationDetailSchema, { signal });
}

/** Deletes one conversation and its stored messages for the signed-in user. */
export function deleteAiConversation(
  conversationId: string,
  signal?: AbortSignal,
): Promise<void> {
  if (!conversationId.trim()) {
    throw new TypeError("conversationId must not be empty");
  }
  const path = `/api/ai/conversations/${encodePathSegment(conversationId)}` as ApiPath;
  return apiRequestNoContent(path, { method: "DELETE", signal });
}

/** Deletes every stored conversation owned by the signed-in user. */
export function deleteAllAiConversations(
  signal?: AbortSignal,
): Promise<void> {
  return apiRequestNoContent("/api/ai/conversations" as ApiPath, {
    method: "DELETE",
    signal,
  });
}

/** Creates a conversation and queues its first user message. */
export async function createAiConversation(
  input: AiConversationCreateInput,
  signal?: AbortSignal,
): Promise<AiConversationAccepted> {
  const payload = {
    message: validateMessage(input.message),
    ...(input.title === undefined ? {} : { title: input.title }),
    ...(input.agent === undefined ? {} : { agent: input.agent }),
    context: input.context ?? {},
  };
  return apiRequest("/api/ai/conversations" as ApiPath, aiConversationAcceptedSchema, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
}

/** Adds a user message and queues AI processing for an existing conversation. */
export async function appendAiMessage(
  conversationId: string,
  input: AiMessageInput,
  signal?: AbortSignal,
): Promise<AiConversationAccepted> {
  const path =
    `/api/ai/conversations/${encodePathSegment(assertConversationId(conversationId))}/messages` as ApiPath;
  const payload = {
    message: validateMessage(input.message),
    ...(input.agent === undefined ? {} : { agent: input.agent }),
    context: input.context ?? {},
  };
  return apiRequest(path, aiConversationAcceptedSchema, {
    method: "POST",
    headers: { "content-type": "application/json" },
    body: JSON.stringify(payload),
    signal,
  });
}

function validateMessage(message: string): string {
  const normalized = message.trim();
  if (!normalized) throw new TypeError("AI message must not be empty");
  if (normalized.length > MAX_AI_MESSAGE_LENGTH) {
    throw new RangeError(
      `AI message must be at most ${MAX_AI_MESSAGE_LENGTH} characters`,
    );
  }
  return normalized;
}

function assertConversationId(conversationId: string): string {
  if (!conversationId.trim()) {
    throw new TypeError("conversationId must not be empty");
  }
  return conversationId;
}
