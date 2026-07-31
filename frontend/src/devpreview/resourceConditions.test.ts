import { describe, expect, it } from "vitest";

import type { InventoryResource, InventoryResourceDetail } from "../api/inventory-schemas";
import { KUBERNETES_KIND } from "./kubernetesKinds";
import {
  conditionItemsFromSummary,
  resourceConditionSnapshot,
} from "./resourceConditions";

function resource(
  kind: string,
  namespace: string,
  name: string,
  summary: Record<string, unknown> = {},
): InventoryResource {
  return {
    inventory_key: `${kind}:${namespace}/${name}`,
    snapshot_id: "snapshot",
    workspace_id: "default",
    cluster_id: "cluster",
    resource_type: kind.toLowerCase(),
    api_version: "v1",
    kind,
    namespace,
    name,
    uid: `${kind}:${name}`,
    resource_version: "1",
    status: "Running",
    health: "healthy",
    labels: {},
    annotations: {},
    summary,
    observed_at: "2026-07-23T00:00:00Z",
    first_seen_at: "2026-07-23T00:00:00Z",
    last_seen_at: "2026-07-23T00:00:00Z",
    deleted_at: null,
    created_at: "2026-07-23T00:00:00Z",
    updated_at: "2026-07-23T00:00:00Z",
  };
}

function detail(
  resourceItem: InventoryResource,
  options: {
    related?: Record<string, InventoryResource[]>;
    events?: InventoryResource[];
  } = {},
): InventoryResourceDetail {
  return {
    cluster_id: "cluster",
    identity: {},
    resource: resourceItem,
    provider_detail: null,
    access: null,
    related: options.related ?? {},
    events: options.events ?? [],
  };
}

describe("resource conditions", () => {
  it("parses observed Kubernetes condition rows from resource summary", () => {
    expect(conditionItemsFromSummary({
      conditions: [
        {
          type: "Available",
          status: "False",
          reason: "MinimumReplicasUnavailable",
          message: "Deployment does not have minimum availability.",
          lastTransitionTime: "2026-07-23T19:20:19Z",
        },
      ],
    })).toEqual([
      expect.objectContaining({
        type: "Available",
        status: "False",
        reason: "MinimumReplicasUnavailable",
        tone: "crit",
      }),
    ]);
  });

  it("treats false negative Kubernetes conditions as healthy", () => {
    expect(conditionItemsFromSummary({
      conditions: [
        {
          type: "ReplicaFailure",
          status: "False",
          reason: "NoFailure",
        },
      ],
    })).toEqual([
      expect.objectContaining({
        type: "ReplicaFailure",
        status: "False",
        tone: "ok",
      }),
    ]);
  });

  it("uses related Pod conditions when a ReplicaSet has no own conditions", () => {
    const replicaSet = resource(KUBERNETES_KIND.replicaSet, "sandbox", "game-room-0-abc");
    const pod = resource(KUBERNETES_KIND.pod, "sandbox", "game-room-0-abc-123", {
      conditions: [
        {
          type: "Ready",
          status: "False",
          reason: "ContainersNotReady",
          message: "containers with unready status: [game-server]",
        },
      ],
    });

    const snapshot = resourceConditionSnapshot(detail(replicaSet, { related: { pods: [pod] } }), {
      includeFallbackEvidence: true,
    });

    expect(snapshot.primary).toEqual([]);
    expect(snapshot.relatedPodCount).toBe(1);
    expect(snapshot.relatedPods).toEqual([
      expect.objectContaining({
        sourceLabel: "game-room-0-abc-123",
        type: "Ready",
        status: "False",
        reason: "ContainersNotReady",
      }),
    ]);
  });

  it("does not keep fallback evidence unless explicitly requested", () => {
    const deployment = resource("Deployment", "sandbox", "api");
    const pod = resource(KUBERNETES_KIND.pod, "sandbox", "api-123", {
      conditions: [{ type: "Ready", status: "False" }],
    });
    const event = resource("Event", "sandbox", "api.unhealthy", {
      type: "Warning",
      reason: "Unhealthy",
      message: "Readiness probe failed.",
    });

    const snapshot = resourceConditionSnapshot(detail(deployment, {
      events: [event],
      related: { pods: [pod] },
    }));

    expect(snapshot.relatedPodCount).toBe(0);
    expect(snapshot.relatedPods).toEqual([]);
    expect(snapshot.events).toEqual([]);
  });

  it("keeps readiness events as fallback evidence for conditionless resources", () => {
    const replicaSet = resource(KUBERNETES_KIND.replicaSet, "sandbox", "game-room-0-abc");
    const event = resource("Event", "sandbox", "game-room-0-abc.unhealthy", {
      type: "Warning",
      reason: "Unhealthy",
      message: "Readiness probe failed: HTTP probe failed with statuscode: 503",
      count: 3,
      last_timestamp: "2026-07-23T19:20:19Z",
    });

    const snapshot = resourceConditionSnapshot(detail(replicaSet, { events: [event] }), {
      includeFallbackEvidence: true,
    });

    expect(snapshot.relatedPodCount).toBe(0);
    expect(snapshot.events).toEqual([
      expect.objectContaining({
        reason: "Unhealthy",
        message: "Readiness probe failed: HTTP probe failed with statuscode: 503",
        tone: "warn",
      }),
    ]);
  });
});
