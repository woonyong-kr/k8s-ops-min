// @vitest-environment jsdom

import { act, renderHook } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

const loadRcaIssueRepresentativeItems = vi.fn();

vi.mock("./rcaIssuesFeed", () => ({
  loadRcaIssueRepresentativeItems: (...args: unknown[]) => loadRcaIssueRepresentativeItems(...args),
}));

import { useRcaIssueDetails } from "./rcaDetailFeed";

const rawIssue = {
  correlation_id: "correlation-1",
  incident_id: "incident-1",
  current_subject: "rca.completed",
  cluster_id: "cluster-1",
  incident_namespace: "sandbox",
  incident_resource_name: "game-room-abc",
  incident_resource_kind: "ReplicaSet",
  incident_symptom: "Readiness probe response failure",
  status: "rca_completed",
  issue_severity: "warning",
  root_cause: "probe_path_wrong",
  confidence: 0.92,
  supporting_evidence: [],
  missing_evidence: [],
  situation_summary: "준비 상태 확인 경로가 다릅니다.",
  recommended_action_summary: "준비 상태 확인 경로를 수정합니다.",
  evidence_summary: "404 응답이 반복됐습니다.",
  evidence_bundle_summary: "logs, kubernetes",
  action_route: "draft_pr",
  pr_url: null,
  error_reason: null,
  recovery_reason_code: null,
  updated_at: "2026-07-23T22:38:09Z",
};

afterEach(() => {
  vi.useRealTimers();
  loadRcaIssueRepresentativeItems.mockReset();
});

describe("useRcaIssueDetails polling", () => {
  it("delivers a matching projection and cancels the next poll on cleanup", async () => {
    vi.useFakeTimers();
    loadRcaIssueRepresentativeItems.mockResolvedValue([{
      item: rawIssue,
      latestItem: rawIssue,
      attemptCount: 1,
      newerAttemptCount: 0,
      recentAttempts: [],
    }]);
    const onItems = vi.fn();
    const { unmount } = renderHook(() => useRcaIssueDetails(["cluster-1"], 4000, onItems));

    await act(async () => {
      await Promise.resolve();
      await Promise.resolve();
    });
    expect(loadRcaIssueRepresentativeItems).toHaveBeenCalledTimes(1);
    expect(onItems).toHaveBeenCalledWith([
      expect.objectContaining({
        correlationId: "correlation-1",
        incidentId: "incident-1",
        rootCause: "probe_path_wrong",
      }),
    ]);

    unmount();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(8000);
    });
    expect(loadRcaIssueRepresentativeItems).toHaveBeenCalledTimes(1);
  });
});
