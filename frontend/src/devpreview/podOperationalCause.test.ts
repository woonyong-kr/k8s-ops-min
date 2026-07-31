import { describe, expect, it } from "vitest";

import {
  projectPodOperationalCause,
  type PodOperationalCause,
} from "./podOperationalCause";
import type { PodResourceSummaryView } from "./podResourceDetailFeed";
import type { ResourceConditionItem, ResourceConditionTone } from "./resourceConditions";
import type { ResourceEventView } from "./resourceEventsFeed";

function condition(
  changes: Partial<ResourceConditionItem> = {},
): ResourceConditionItem {
  return {
    id: "condition",
    sourceLabel: null,
    type: "Ready",
    status: "False",
    reason: "ContainersNotReady",
    message: "containers with unready status: [app]",
    lastTransitionAt: "2026-07-24T03:00:00Z",
    tone: "crit",
    ...changes,
  };
}

function event(changes: Partial<ResourceEventView> = {}): ResourceEventView {
  return {
    id: "event",
    reason: "Unhealthy",
    message: "Readiness probe failed: HTTP probe failed with statuscode: 503",
    type: "Warning",
    count: 7,
    lastAt: "2026-07-24T03:02:00Z",
    ...changes,
  };
}

function summary(
  conditions: ResourceConditionItem[],
): Pick<PodResourceSummaryView, "conditions"> {
  return { conditions };
}

function tone(value: ResourceConditionTone): ResourceConditionTone {
  return value;
}

describe("projectPodOperationalCause", () => {
  it("projects an observed false Ready condition and attaches the newest Warning event", () => {
    const ready = condition();
    const result = projectPodOperationalCause(summary([
      condition({
        id: "initialized",
        type: "Initialized",
        status: "True",
        reason: null,
        message: null,
        tone: "ok",
      }),
      ready,
      condition({
        id: "containers-ready",
        type: "ContainersReady",
        lastTransitionAt: "2026-07-24T03:01:00Z",
      }),
    ]), [
      event({
        id: "older",
        reason: "FailedMount",
        message: "volume setup failed",
        lastAt: "2026-07-24T02:58:00Z",
      }),
      event({ id: "newest" }),
    ]);

    expect(result).toEqual<PodOperationalCause>({
      source: "condition",
      reason: "ContainersNotReady",
      message: "containers with unready status: [app]",
      condition: ready,
      supportingEvent: event({ id: "newest" }),
    });
  });

  it("prefers explicit false availability conditions over other critical conditions", () => {
    const genericFailure = condition({
      id: "failure",
      type: "ReplicaFailure",
      status: "True",
      reason: "FailedCreate",
      message: "failed to create a pod",
      lastTransitionAt: "2026-07-24T03:05:00Z",
    });
    const containersReady = condition({
      id: "containers-ready",
      type: "ContainersReady",
      reason: "ContainersNotReady",
      lastTransitionAt: "2026-07-24T03:00:00Z",
    });

    expect(projectPodOperationalCause(
      summary([genericFailure, containersReady]),
      [],
    )?.condition).toBe(containersReady);
  });

  it("prefers Ready=False over ContainersReady=False without inferring beyond the condition", () => {
    const containersReady = condition({
      id: "containers-ready",
      type: "ContainersReady",
      reason: "ContainerStarting",
      message: "one container is starting",
      lastTransitionAt: "2026-07-24T03:05:00Z",
    });
    const ready = condition({
      id: "ready",
      type: "Ready",
      reason: null,
      message: null,
      lastTransitionAt: "2026-07-24T03:00:00Z",
    });

    expect(projectPodOperationalCause(
      summary([containersReady, ready]),
      [],
    )).toMatchObject({
      source: "condition",
      reason: "Ready",
      message: null,
      condition: ready,
    });
  });

  it("uses the newest transition when active conditions have equal priority", () => {
    const older = condition({
      id: "older",
      type: "PodScheduled",
      reason: "Unschedulable",
      lastTransitionAt: "2026-07-24T03:00:00Z",
    });
    const newer = condition({
      id: "newer",
      type: "DisruptionTarget",
      reason: "EvictionByEvictionAPI",
      lastTransitionAt: "2026-07-24T03:04:00Z",
    });

    expect(projectPodOperationalCause(summary([older, newer]), [])?.condition).toBe(newer);
  });

  it.each([
    tone("crit"),
    tone("warn"),
  ])("treats %s conditions as active observed evidence", (activeTone) => {
    expect(projectPodOperationalCause(summary([
      condition({
        type: "CustomHealth",
        status: "Unknown",
        reason: "ObservationPending",
        tone: activeTone,
      }),
    ]), [])).toMatchObject({
      source: "condition",
      reason: "ObservationPending",
    });
  });

  it("falls back to a Warning event only when no active condition is observed", () => {
    const warning = event({
      reason: "FailedScheduling",
      message: "0/2 nodes are available",
    });

    expect(projectPodOperationalCause(summary([
      condition({
        status: "True",
        reason: null,
        message: null,
        tone: "ok",
      }),
    ]), [warning])).toEqual<PodOperationalCause>({
      source: "warning-event",
      reason: "FailedScheduling",
      message: "0/2 nodes are available",
      condition: null,
      supportingEvent: warning,
    });
  });

  it("chooses the latest Warning by observed timestamp and ignores Normal events", () => {
    const newestNormal = event({
      id: "normal",
      type: "Normal",
      reason: "Pulled",
      lastAt: "2026-07-24T03:09:00Z",
    });
    const olderWarning = event({
      id: "older-warning",
      reason: "FailedMount",
      lastAt: "2026-07-24T03:01:00Z",
    });
    const newerWarning = event({
      id: "newer-warning",
      reason: "Unhealthy",
      lastAt: "2026-07-24T03:08:00Z",
    });

    expect(projectPodOperationalCause(
      summary([]),
      [newestNormal, olderWarning, newerWarning],
    )?.supportingEvent).toBe(newerWarning);
  });

  it("keeps input order for Warning events without a comparable timestamp", () => {
    const first = event({ id: "first", lastAt: null });
    const second = event({ id: "second", lastAt: "not-a-timestamp" });

    expect(projectPodOperationalCause(summary([]), [first, second])?.supportingEvent).toBe(first);
  });

  it("trims observed text and falls back from an empty reason to the observed type", () => {
    expect(projectPodOperationalCause(summary([
      condition({
        type: " Ready ",
        reason: " ",
        message: "  container is not ready  ",
      }),
    ]), [])).toMatchObject({
      reason: "Ready",
      message: "container is not ready",
    });
  });

  it("does not fabricate a cause from healthy conditions, normal events, or an empty Warning", () => {
    const healthy = condition({
      status: "True",
      reason: "PodCompleted",
      tone: "ok",
    });
    const normal = event({ type: "Normal", reason: "Pulled" });
    const emptyWarning = event({ reason: null, message: null });

    expect(projectPodOperationalCause(summary([healthy]), [normal])).toBeNull();
    expect(projectPodOperationalCause(summary([]), [emptyWarning])).toBeNull();
    expect(projectPodOperationalCause(summary([]), [])).toBeNull();
  });

  it("does not mutate caller-owned condition or event ordering", () => {
    const conditions = [
      condition({ id: "first", lastTransitionAt: "2026-07-24T03:00:00Z" }),
      condition({ id: "second", lastTransitionAt: "2026-07-24T03:01:00Z" }),
    ];
    const events = [
      event({ id: "first", lastAt: "2026-07-24T03:00:00Z" }),
      event({ id: "second", lastAt: "2026-07-24T03:01:00Z" }),
    ];

    projectPodOperationalCause(summary(conditions), events);

    expect(conditions.map((item) => item.id)).toEqual(["first", "second"]);
    expect(events.map((item) => item.id)).toEqual(["first", "second"]);
  });
});
