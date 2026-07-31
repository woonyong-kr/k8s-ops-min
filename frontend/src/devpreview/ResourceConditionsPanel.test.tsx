// @vitest-environment jsdom

import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { ResourceConditionsPanel } from "./ResourceConditionsPanel";
import { KUBERNETES_KIND } from "./kubernetesKinds";
import type { ResourceConditionsView } from "./resourceConditionsFeed";

const retry = vi.fn();

afterEach(() => {
  cleanup();
  retry.mockClear();
});

function view(overrides: Partial<ResourceConditionsView>): ResourceConditionsView {
  return {
    status: "ready",
    primary: [],
    relatedPods: [],
    events: [],
    relatedPodCount: 0,
    retry,
    ...overrides,
  };
}

describe("ResourceConditionsPanel", () => {
  it("uses related Pod conditions as ReplicaSet fallback", () => {
    render(
      <ResourceConditionsPanel
        kind={KUBERNETES_KIND.replicaSet}
        view={view({
          relatedPodCount: 1,
          relatedPods: [{
            id: "pod-ready",
            sourceLabel: "game-room-0-abc-123",
            type: "Ready",
            status: "False",
            reason: "ContainersNotReady",
            message: "containers with unready status: [game-server]",
            lastTransitionAt: null,
            tone: "crit",
          }],
        })}
      />,
    );

    expect(screen.getByText("관련 Pod 컨디션")).not.toBeNull();
    expect(screen.getByText("game-room-0-abc-123 · Ready")).not.toBeNull();
  });

  it("does not show related Pod fallback for a Deployment with no own conditions", () => {
    render(
      <ResourceConditionsPanel
        kind="Deployment"
        view={view({
          relatedPodCount: 1,
          relatedPods: [{
            id: "pod-ready",
            sourceLabel: "api-123",
            type: "Ready",
            status: "False",
            reason: "ContainersNotReady",
            message: null,
            lastTransitionAt: null,
            tone: "crit",
          }],
        })}
      />,
    );

    expect(screen.getByText("컨디션 정보 없음")).not.toBeNull();
    expect(screen.queryByText("관련 Pod 컨디션")).toBeNull();
  });
});
