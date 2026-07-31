import { describe, expect, it } from "vitest";

import { buildAiContext, isAiProviderFailureTurn, toAssistantTurn } from "./aiFeed";

describe("AI context identity", () => {
  it("uses an empty cluster filter for the all-clusters scope", () => {
    expect(buildAiContext("이슈", "").filters.clusters).toEqual([]);
  });

  it("sends a selected cluster id unchanged", () => {
    expect(buildAiContext("이슈", "battlegrounds-8352").filters.clusters)
      .toEqual(["battlegrounds-8352"]);
  });
});

describe("AI provider failure messages", () => {
  it("localizes a rate-limit fallback and does not treat it as a completed review", () => {
    const turn = toAssistantTurn({
      answer: "The AI provider is temporarily rate-limited, so no diagnosis was generated. Your request is preserved. Review it and retry in a moment.",
      evidence: [],
      answer_kind: "capability",
    }, "reply");

    expect(turn.parts).toEqual([{
      kind: "text",
      markdown: "AI 제공자의 요청 한도에 일시적으로 도달해 분석을 생성하지 못했습니다. 요청은 저장되었습니다. 잠시 후 다시 시도해 주세요.",
    }]);
    expect(isAiProviderFailureTurn(turn)).toBe(true);
  });

  it("keeps a normal Korean answer as a successful review", () => {
    const turn = toAssistantTurn({
      answer: "현재 근거로 안전 조건을 확인했습니다.",
      evidence: [],
      answer_kind: "capability",
    }, "reply");

    expect(isAiProviderFailureTurn(turn)).toBe(false);
  });
});
