import { describe, expect, it } from "vitest";

import { rcaIssuePinGroupKey, upsertStoredRcaIssuePin } from "./rcaPinnedIssueStore";

describe("rcaPinnedIssueStore", () => {
  it("builds the same issue key from resource and raw symptom identity", () => {
    expect(rcaIssuePinGroupKey({
      cluster: "cluster-1",
      ns: "sandbox",
      resourceKind: "ReplicaSet",
      svc: "game-room",
      rawSymptom: "FailedScheduling",
      correlationId: "correlation-1",
    })).toBe([
      "cluster 1",
      "sandbox",
      "replicaset",
      "game room",
      "failedscheduling",
    ].join("\u0000"));
  });

  it("keeps only the newest pin for the same issue group", () => {
    const pins = upsertStoredRcaIssuePin([
      { groupKey: "group-a", correlationId: "old", touchedAt: "2026-07-24T00:00:00Z" },
    ], {
      groupKey: "group-a",
      correlationId: "new",
      touchedAt: "2026-07-24T00:01:00Z",
    });

    expect(pins).toEqual([
      { groupKey: "group-a", correlationId: "new", touchedAt: "2026-07-24T00:01:00Z" },
    ]);
  });
});
