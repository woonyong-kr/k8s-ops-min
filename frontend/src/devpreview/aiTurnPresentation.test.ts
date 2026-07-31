import { describe, expect, it } from "vitest";

import { isStructuredAssistantTurn } from "../devpreview-ai";
import type { AiTurn } from "../features/ai-assistant/aiConversationContract";

function assistantTurn(parts: AiTurn["parts"]): AiTurn {
  return {
    id: "assistant-turn",
    role: "assistant",
    createdAt: "2026-07-24T00:00:00.000Z",
    collapsed: false,
    parts,
  };
}

describe("AI turn presentation", () => {
  it("keeps text-only replies in the plain chat presentation", () => {
    expect(isStructuredAssistantTurn(assistantTurn([
      { kind: "text", markdown: "일반 답변입니다." },
    ]))).toBe(false);
  });

  it("reserves cards for replies with a structured action or result", () => {
    expect(isStructuredAssistantTurn(assistantTurn([
      {
        kind: "result",
        title: "복구 계획",
        tone: "warning",
        summary: "검토가 필요합니다.",
      },
      { kind: "text", markdown: "변경 내용을 확인해 주세요." },
    ]))).toBe(true);
  });
});
