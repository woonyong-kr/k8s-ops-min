import { afterEach, describe, expect, it, vi } from "vitest";

import { isResourceManifestSourceConflict } from "../devpreview/resourceManifestFeed";
import { previewResourceManifestEdit } from "./resource-manifests";

afterEach(() => {
  vi.unstubAllGlobals();
});

describe("resource manifest source conflict contract", () => {
  it.each([
    "manifest_source_stale",
    "manifest_source_revision_invalid",
  ])("preserves the backend 409 code for %s without retrying", async (code) => {
    const fetchMock = vi.fn().mockResolvedValue(new Response(
      JSON.stringify({
        detail: {
          code,
          detail: "The Git source changed after it was loaded.",
        },
      }),
      {
        status: 409,
        headers: { "content-type": "application/json" },
      },
    ));
    vi.stubGlobal("fetch", fetchMock);

    const request = previewResourceManifestEdit("resource-1", {
      applicationId: "application-1",
      baseSha: "base-1",
      sourceSha256: "source-1",
      sourceRevisionToken: "revision-1",
      editedYaml: "apiVersion: apps/v1\nkind: Deployment\n",
    });

    const conflict = await request.catch((cause: unknown) => cause);

    expect(isResourceManifestSourceConflict(conflict)).toBe(true);
    expect(conflict).toMatchObject({ status: 409, code });
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });

  it("does not classify unrelated errors as a source conflict", () => {
    expect(isResourceManifestSourceConflict(new Error("offline"))).toBe(false);
  });
});
