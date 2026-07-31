import { describe, expect, it } from "vitest";

import { isSafePrRoute, recoveryRouteLabel } from "./recoveryRoute";

describe("recovery route labels", () => {
  it("treats the backend draft_pr route and legacy safe_pr alias as the same path", () => {
    expect(isSafePrRoute("draft_pr")).toBe(true);
    expect(isSafePrRoute("safe_pr")).toBe(true);
    expect(recoveryRouteLabel("draft_pr")).toBe("복구 PR");
    expect(recoveryRouteLabel("safe_pr")).toBe("복구 PR");
  });

  it("labels automatic and fallback recovery paths", () => {
    expect(recoveryRouteLabel("auto")).toBe("자동 복구");
    expect(recoveryRouteLabel("approval_required")).toBe("복구 요청");
  });
});
