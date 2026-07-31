// @vitest-environment jsdom

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const getSession = vi.fn();

vi.mock("../api/auth", () => ({
  getSession: (...args: unknown[]) => getSession(...args),
  logout: vi.fn(),
}));

import { resetSharedSessionForTests, useSession } from "./sessionFeed";

afterEach(() => {
  resetSharedSessionForTests();
  getSession.mockReset();
});

describe("shared session feed", () => {
  it("loads the session once for shell and nested surface consumers", async () => {
    getSession.mockResolvedValue({
      authenticated: true,
      auth_enabled: true,
      auth_mode: "session",
      display_name: "Operator",
      email: "operator@example.test",
      user_id: "user-a",
      roles: ["operator"],
      workspace_id: "workspace-a",
      logout: { supported: true },
    });

    const shell = renderHook(() => useSession());
    const settings = renderHook(() => useSession());

    await waitFor(() => expect(shell.result.current.status).toBe("ready"));
    await waitFor(() => expect(settings.result.current.status).toBe("ready"));
    expect(getSession).toHaveBeenCalledTimes(1);
    expect(settings.result.current.workspaceId).toBe("workspace-a");

    shell.unmount();
    settings.unmount();
  });

  it("fails closed for every consumer when the shared authorization read fails", async () => {
    getSession.mockRejectedValue(new Error("session unavailable"));

    const shell = renderHook(() => useSession());
    const settings = renderHook(() => useSession());

    await waitFor(() => expect(shell.result.current.status).toBe("error"));
    expect(settings.result.current).toEqual(expect.objectContaining({
      status: "error",
      authenticated: false,
      roles: [],
      workspaceId: null,
    }));
    expect(getSession).toHaveBeenCalledTimes(1);

    shell.unmount();
    settings.unmount();
  });
});
