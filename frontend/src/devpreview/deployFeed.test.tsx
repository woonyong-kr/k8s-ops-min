// @vitest-environment jsdom

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const listApplications = vi.fn();

vi.mock("../api/applications", () => ({
  listApplications: (...args: unknown[]) => listApplications(...args),
  listApplicationRuns: vi.fn(),
}));

vi.mock("../api/helm-releases", () => ({
  listHelmReleases: vi.fn(),
}));

import { useApplications } from "./deployFeed";

afterEach(() => {
  listApplications.mockReset();
});

describe("shared applications feed contract", () => {
  it("does not read while its owning surfaces are inactive", () => {
    const { result } = renderHook(() => useApplications(0, false));

    expect(result.current).toEqual({
      status: "loading",
      items: [],
      stale: false,
    });
    expect(listApplications).not.toHaveBeenCalled();
  });

  it("retains the last authorized snapshot when a refresh fails", async () => {
    listApplications.mockResolvedValueOnce({
      applications: [{
        id: "application-a",
        name: "application-a",
        repository_ref: "owner/repository",
      }],
    });
    const { result, rerender } = renderHook(
      ({ refreshKey }) => useApplications(refreshKey, true),
      { initialProps: { refreshKey: 0 } },
    );

    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.items).toHaveLength(1);
    expect(result.current.stale).toBe(false);

    listApplications.mockRejectedValueOnce(new Error("temporary failure"));
    rerender({ refreshKey: 1 });
    await waitFor(() => expect(result.current.stale).toBe(true));

    expect(result.current.status).toBe("ready");
    expect(result.current.items[0]?.id).toBe("application-a");
  });
});
