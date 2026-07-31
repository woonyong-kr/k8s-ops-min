import { describe, expect, it } from "vitest";

import { fleetHeaderGroups } from "./fleetSummaryPresentation";

describe("fleetHeaderGroups", () => {
  it("maps every authorized fleet total and preserves observed zeros", () => {
    const groups = fleetHeaderGroups({
      clusters: 4,
      healthy: 1,
      warning: 1,
      critical: 1,
      stale: 1,
      unknown: 0,
      open_incidents: 2,
      pending_approvals: 0,
      running_workflows: 3,
      dead_letters: 0,
    });

    expect(groups.health.map(({ key }) => key)).toEqual([
      "clusters",
      "healthy",
      "warning",
      "critical",
      "stale",
    ]);
    expect(groups.operations).toEqual([
      { key: "open_incidents", label: "이슈", value: 2 },
      { key: "pending_approvals", label: "승인 대기", value: 0 },
      { key: "running_workflows", label: "실행 중", value: 3 },
      { key: "dead_letters", label: "처리 실패", value: 0 },
    ]);
  });

  it("does not misrepresent an unauthorized global count as zero", () => {
    const groups = fleetHeaderGroups({
      clusters: 0,
      healthy: 0,
      warning: 0,
      critical: 0,
      stale: 0,
      unknown: 0,
      open_incidents: 0,
      pending_approvals: 0,
      running_workflows: 0,
      dead_letters: null,
    });

    expect(groups.operations.some(({ key }) => key === "dead_letters")).toBe(false);
  });
});
