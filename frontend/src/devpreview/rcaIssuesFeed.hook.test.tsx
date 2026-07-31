// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const listRcaIssues = vi.fn();

vi.mock("../api/rca-issues", () => ({
  listRcaIssues: (...args: unknown[]) => listRcaIssues(...args),
}));

import { RCA_ISSUES_REFRESH_MS, useRcaIssues } from "./rcaIssuesFeed";

const issue = {
  correlation_id: "correlation-a",
  incident_id: "incident-a",
  cluster_id: "cluster-a",
  incident_namespace: "sandbox",
  incident_resource_kind: "Pod",
  incident_resource_name: "pod-a",
  incident_symptom: "CrashLoopBackOff",
  status: "open",
  issue_severity: "critical",
  updated_at: "2026-07-24T00:00:00Z",
};

async function flush(): Promise<void> {
  await act(async () => {
    await Promise.resolve();
    await Promise.resolve();
  });
}

afterEach(() => {
  vi.useRealTimers();
  listRcaIssues.mockReset();
});

describe("RCA issue last-known-good contract", () => {
  it("retains the authorized issue snapshot across a refresh failure", async () => {
    vi.useFakeTimers();
    listRcaIssues
      .mockResolvedValueOnce({ items: [issue] })
      .mockRejectedValueOnce(new Error("temporary failure"));
    const { result, unmount } = renderHook(() => useRcaIssues(["cluster-a"]));

    await flush();
    expect(result.current.status).toBe("ready");
    expect(result.current.items.map((item) => item.correlationId)).toEqual(["correlation-a"]);

    await act(async () => {
      vi.advanceTimersByTime(RCA_ISSUES_REFRESH_MS);
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(result.current.status).toBe("stale");
    expect(result.current.items.map((item) => item.correlationId)).toEqual(["correlation-a"]);
    unmount();
  });
});
