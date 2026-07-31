import { afterEach, describe, expect, it, vi } from "vitest";

import {
  deleteAiConversation,
  deleteAllAiConversations,
} from "./ai-conversations";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("AI conversation deletion", () => {
  it("deletes one signed-in user's conversation", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await deleteAiConversation("conversation/1");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/ai/conversations/conversation%2F1",
      expect.objectContaining({
        method: "DELETE",
        credentials: "include",
        headers: expect.any(Headers),
      }),
    );
  });

  it("deletes all conversations through the collection endpoint", async () => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);

    await deleteAllAiConversations();

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/ai/conversations",
      expect.objectContaining({
        method: "DELETE",
        credentials: "include",
        headers: expect.any(Headers),
      }),
    );
  });
});
