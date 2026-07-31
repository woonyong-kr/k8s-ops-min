import { describe, expect, it } from "vitest";

import { connectionFailurePresentation } from "./connectErrors";

describe("connectionFailurePresentation", () => {
  it("turns an unavailable repository credential into an actionable Korean recovery", () => {
    expect(connectionFailurePresentation("repository credential is unavailable")).toEqual({
      kind: "repository_credential",
      title: "저장소 인증을 다시 확인해야 합니다",
      message:
        "저장된 GitHub App 권한을 확인할 수 없습니다. 이전 단계에서 GitHub App을 다시 연결해 주세요.",
      actionLabel: "GitHub App 다시 연결",
    });
  });

  it("keeps an unknown backend detail without inventing a recovery action", () => {
    expect(connectionFailurePresentation("unexpected failure")).toEqual({
      kind: "generic",
      title: "요청을 완료하지 못했습니다",
      message: "unexpected failure",
      actionLabel: null,
    });
  });
});
