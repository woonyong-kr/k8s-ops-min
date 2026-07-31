// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

vi.mock("./devpreview/aiFeed", async (importOriginal) => ({
  ...(await importOriginal<typeof import("./devpreview/aiFeed")>()),
  useAiSuggestions: () => ({ status: "ready" as const, items: [] }),
  useAiConversations: () => ({ status: "ready" as const, items: [] }),
  useConversationDetail: () => ({ status: "idle" as const, turns: [] }),
}));

import { AiPanel } from "./devpreview-ai";

afterEach(cleanup);
beforeEach(() => {
  Object.defineProperty(HTMLElement.prototype, "scrollTo", {
    configurable: true,
    value: vi.fn(),
  });
});

describe("AI panel viewport layout", () => {
  it("keeps the message list shrinkable and the composer in a bounded footer", () => {
    const { container } = render(<AiPanel embedded />);

    const root = container.querySelector<HTMLElement>("[data-ai-panel-root='true']");
    const scrollRegion = container.querySelector<HTMLElement>("[data-ai-scroll-region='true']");
    const composerRegion = container.querySelector<HTMLElement>("[data-ai-composer-region='true']");
    const composer = container.querySelector<HTMLElement>("[data-ai-composer='true']");

    expect(root?.className).toContain("min-h-0");
    expect(root?.className).toContain("max-h-full");
    expect(scrollRegion?.className).toContain("min-h-0");
    expect(scrollRegion?.className).toContain("overflow-y-auto");
    expect(composerRegion?.className).toContain("shrink-0");
    expect(composerRegion?.className).toContain("overflow-hidden");
    expect(composerRegion?.style.maxHeight).toBe("min(52%, 28rem)");
    expect(composer?.className).toContain("shrink-0");
    expect(screen.getByPlaceholderText("질문 입력")).not.toBeNull();
  });
});
