import { afterEach, describe, expect, it, vi } from "vitest";

import type { InventoryResourceDetail } from "../api/inventory-schemas";

const getInventoryResourceDetail = vi.fn();

vi.mock("../api/inventory", () => ({
  getInventoryResourceDetail: (...args: unknown[]) => getInventoryResourceDetail(...args),
}));

import {
  loadSharedInventoryResourceDetail,
  resetSharedInventoryResourceDetailForTests,
  SHARED_RESOURCE_DETAIL_EVENT_LIMIT,
  SHARED_RESOURCE_DETAIL_RELATED_LIMIT,
} from "./resourceDetailFeed";

const identity = {
  resourceType: "pod",
  kind: "Pod",
  namespace: "sandbox",
  name: "api-0",
};

const detail = { cluster_id: "cluster-a" } as InventoryResourceDetail;

afterEach(() => {
  resetSharedInventoryResourceDetailForTests();
  getInventoryResourceDetail.mockReset();
});

describe("resource detail request coalescing", () => {
  it("shares simultaneous identical drawer reads and drops the entry after settlement", async () => {
    let resolveRequest!: (value: InventoryResourceDetail) => void;
    getInventoryResourceDetail.mockImplementation(() => new Promise((resolve) => {
      resolveRequest = resolve;
    }));

    const first = loadSharedInventoryResourceDetail("cluster-a", identity);
    const second = loadSharedInventoryResourceDetail("cluster-a", { ...identity });

    expect(second).toBe(first);
    expect(getInventoryResourceDetail).toHaveBeenCalledTimes(1);
    expect(getInventoryResourceDetail).toHaveBeenCalledWith(
      "cluster-a",
      identity,
      {
        relatedLimit: SHARED_RESOURCE_DETAIL_RELATED_LIMIT,
        eventLimit: SHARED_RESOURCE_DETAIL_EVENT_LIMIT,
      },
    );
    resolveRequest(detail);
    await expect(first).resolves.toBe(detail);
    await Promise.resolve();

    getInventoryResourceDetail.mockResolvedValue(detail);
    await loadSharedInventoryResourceDetail("cluster-a", identity);
    expect(getInventoryResourceDetail).toHaveBeenCalledTimes(2);
  });

  it("never coalesces a distinct authorization or resource scope", async () => {
    getInventoryResourceDetail.mockResolvedValue(detail);

    await Promise.all([
      loadSharedInventoryResourceDetail("cluster-a", identity),
      loadSharedInventoryResourceDetail("cluster-b", identity),
      loadSharedInventoryResourceDetail("cluster-a", { ...identity, name: "api-1" }),
    ]);

    expect(getInventoryResourceDetail).toHaveBeenCalledTimes(3);
  });

  it("releases a rejected request so an explicit retry can reach the backend", async () => {
    getInventoryResourceDetail
      .mockRejectedValueOnce(new Error("temporary inventory failure"))
      .mockResolvedValueOnce(detail);

    await expect(
      loadSharedInventoryResourceDetail("cluster-a", identity),
    ).rejects.toThrow("temporary inventory failure");
    await loadSharedInventoryResourceDetail("cluster-a", identity);

    expect(getInventoryResourceDetail).toHaveBeenCalledTimes(2);
  });
});
