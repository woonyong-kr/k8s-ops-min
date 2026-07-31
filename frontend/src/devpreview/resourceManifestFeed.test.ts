import { describe, expect, it } from "vitest";

import { ApiError } from "../api/client";
import {
  isResourceManifestSourceStale,
  manifestIdempotencyKey,
  resourceManifestFailureText,
} from "./resourceManifestFeed";

describe("resourceManifestFeed apply idempotency", () => {
  it("changes the key when the Git source authority changes", () => {
    const first = manifestIdempotencyKey(
      "resource-1",
      "desired-sha",
      "base-sha-1",
      "source-sha-1",
    );
    const rebased = manifestIdempotencyKey(
      "resource-1",
      "desired-sha",
      "base-sha-2",
      "source-sha-2",
    );

    expect(rebased).not.toBe(first);
  });

  it("reuses the key for an exact retry of the same preview", () => {
    expect(
      manifestIdempotencyKey(
        "resource-1",
        "desired-sha",
        "base-sha-1",
        "source-sha-1",
      ),
    ).toBe(
      manifestIdempotencyKey(
        "resource-1",
        "desired-sha",
        "base-sha-1",
        "source-sha-1",
      ),
    );
  });
});

describe("resourceManifestFeed stale source handling", () => {
  it.each(["manifest_source_stale", "manifest_source_revision_invalid"])(
    "recognizes %s as a recoverable source conflict",
    (code) => {
      const error = new ApiError("http", "stale", {
        status: 409,
        code,
        detail: "server detail",
      });

      expect(isResourceManifestSourceStale(error)).toBe(true);
      expect(resourceManifestFailureText(error)).toContain("편집 내용은 유지");
    },
  );

  it("does not classify unrelated conflicts as source staleness", () => {
    const error = new ApiError("http", "conflict", {
      status: 409,
      code: "approval_conflict",
    });

    expect(isResourceManifestSourceStale(error)).toBe(false);
  });
});
