import { describe, expect, it } from "vitest";

import { progressRailMetrics, type ProgressNode } from "./ProgressNodeRail";

function step(id: string, state: ProgressNode["state"]): ProgressNode {
  return { id, label: id, state, statusLabel: state };
}

describe("progressRailMetrics", () => {
  it("counts only observed terminal completion as progress", () => {
    expect(progressRailMetrics([
      step("registered", "complete"),
      step("agent", "active"),
      step("snapshot", "pending"),
    ])).toEqual({ completed: 1, failed: 0, total: 3 });
  });

  it("reports failures separately without turning activity into a percentage", () => {
    expect(progressRailMetrics([
      step("registered", "complete"),
      step("agent", "failed"),
      step("snapshot", "pending"),
    ])).toEqual({ completed: 1, failed: 1, total: 3 });
  });
});
