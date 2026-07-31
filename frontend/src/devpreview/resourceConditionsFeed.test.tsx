// @vitest-environment jsdom

import { renderHook, waitFor } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { InventoryResource, InventoryResourceDetail } from "../api/inventory-schemas";

const getInventoryResourceDetail = vi.fn();

vi.mock("../api/inventory", async (importOriginal) => {
  const actual = await importOriginal<typeof import("../api/inventory")>();
  return {
    ...actual,
    getInventoryResourceDetail: (...args: unknown[]) => getInventoryResourceDetail(...args),
  };
});

import {
  REPLICA_SET_CONDITION_RELATED_POD_LIMIT,
  useResourceConditions,
} from "./resourceConditionsFeed";
import { KUBERNETES_KIND } from "./kubernetesKinds";
import { RESOURCE_DETAIL_EVENT_LIMIT } from "./resourceEventsFeed";
import {
  resetSharedInventoryResourceDetailForTests,
  SHARED_RESOURCE_DETAIL_RELATED_LIMIT,
} from "./resourceDetailFeed";

function resource(kind: string, summary: Record<string, unknown> = {}): InventoryResource {
  return {
    inventory_key: `${kind}:sandbox/api`,
    snapshot_id: "snapshot",
    workspace_id: "default",
    cluster_id: "cluster",
    resource_type: kind.toLowerCase(),
    api_version: "v1",
    kind,
    namespace: "sandbox",
    name: "api",
    uid: `${kind}:api`,
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
  kind: string,
  options: {
    events?: InventoryResource[];
    related?: Record<string, InventoryResource[]>;
  } = {},
): InventoryResourceDetail {
  return {
    cluster_id: "cluster",
    identity: {},
    resource: resource(kind),
    provider_detail: null,
    access: null,
    related: options.related ?? {},
    events: options.events ?? [],
  };
}

afterEach(() => {
  resetSharedInventoryResourceDetailForTests();
  getInventoryResourceDetail.mockReset();
});

describe("useResourceConditions", () => {
  it("does not request detail while disabled", () => {
    renderHook(() =>
      useResourceConditions(false, "cluster", "deployment", "Deployment", "sandbox", "api")
    );

    expect(getInventoryResourceDetail).not.toHaveBeenCalled();
  });

  it("uses the shared bounded detail projection for non-ReplicaSet resources", async () => {
    getInventoryResourceDetail.mockResolvedValue(detail("Deployment"));

    renderHook(() =>
      useResourceConditions(true, "cluster", "deployment", "Deployment", "sandbox", "api")
    );

    await waitFor(() => expect(getInventoryResourceDetail).toHaveBeenCalledOnce());
    expect(getInventoryResourceDetail).toHaveBeenCalledWith(
      "cluster",
      expect.objectContaining({ kind: "Deployment", resourceType: "deployment" }),
      {
        relatedLimit: SHARED_RESOURCE_DETAIL_RELATED_LIMIT,
        eventLimit: RESOURCE_DETAIL_EVENT_LIMIT,
      },
    );
  });

  it("does not keep fallback evidence for non-ReplicaSet resources", async () => {
    const pod = resource(KUBERNETES_KIND.pod, {
      conditions: [{ type: "Ready", status: "False" }],
    });
    const event = resource("Event", {
      message: "Readiness probe failed.",
      reason: "Unhealthy",
      type: "Warning",
    });
    getInventoryResourceDetail.mockResolvedValue(detail("Deployment", {
      events: [event],
      related: { pods: [pod] },
    }));

    const { result } = renderHook(() =>
      useResourceConditions(true, "cluster", "deployment", "Deployment", "sandbox", "api")
    );

    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect(result.current.relatedPodCount).toBe(0);
    expect(result.current.relatedPods).toEqual([]);
    expect(result.current.events).toEqual([]);
  });

  it("does not expose its internal cache key on the public view", async () => {
    getInventoryResourceDetail.mockResolvedValue(detail("Deployment"));

    const { result } = renderHook(() =>
      useResourceConditions(true, "cluster", "deployment", "Deployment", "sandbox", "api")
    );

    await waitFor(() => expect(result.current.status).toBe("ready"));
    expect("key" in result.current).toBe(false);
  });

  it("keeps ReplicaSet fallback evidence available", async () => {
    getInventoryResourceDetail.mockResolvedValue(detail(KUBERNETES_KIND.replicaSet));

    renderHook(() =>
      useResourceConditions(true, "cluster", "replicaset", KUBERNETES_KIND.replicaSet, "sandbox", "api")
    );

    await waitFor(() => expect(getInventoryResourceDetail).toHaveBeenCalledOnce());
    expect(getInventoryResourceDetail).toHaveBeenCalledWith(
      "cluster",
      expect.objectContaining({ kind: KUBERNETES_KIND.replicaSet, resourceType: "replicaset" }),
      {
        relatedLimit: REPLICA_SET_CONDITION_RELATED_POD_LIMIT,
        eventLimit: RESOURCE_DETAIL_EVENT_LIMIT,
      },
    );
  });
});
