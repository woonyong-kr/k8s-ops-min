import { describe, expect, it } from "vitest";

import { pullRequestNumber, pullRequestReference } from "./pullRequestReference";

describe("pullRequestReference", () => {
  it("extracts a GitHub pull request number", () => {
    expect(pullRequestNumber("https://github.com/opsia/platform/pull/605")).toBe("605");
    expect(pullRequestReference("https://github.com/opsia/platform/pull/605").label).toBe(
      "Kyro 복구 PR #605",
    );
  });

  it("falls back to a platform-generated label for an unrecognized URL", () => {
    expect(pullRequestReference("https://scm.example.local/change/abc")).toEqual({
      number: null,
      label: "Kyro에서 생성한 복구 PR",
    });
  });

  it("does not infer a number from malformed input", () => {
    expect(pullRequestNumber("not-a-url")).toBeNull();
  });
});
