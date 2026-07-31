import { afterEach, describe, expect, it, vi } from "vitest";

import type { RelationTopologyEndpoint } from "../api/relation-topology-schemas";

const getRelationTopology = vi.fn();

vi.mock("../api/relation-topology", () => ({
  getRelationTopology: (...args: unknown[]) => getRelationTopology(...args),
}));

import {
  loadSharedRelationTopology,
  resetSharedRelationTopologyForTests,
} from "./relationTopologyFeed";

const endpoint = {
  availability: "available",
  cluster: { cluster_id: "cluster-a" },
  nodes: [],
  edges: [],
  root_node_ids: [],
  truncated: false,
  omitted_node_count: 0,
  omitted_edge_count: 0,
  partial_reason_codes: [],
} as unknown as RelationTopologyEndpoint;

afterEach(() => {
  resetSharedRelationTopologyForTests();
  getRelationTopology.mockReset();
});

describe("relation topology request coalescing", () => {
  it("shares a simultaneous scope request and releases it after settlement", async () => {
    let resolveRequest!: (value: RelationTopologyEndpoint) => void;
    getRelationTopology.mockImplementation(() => new Promise((resolve) => {
      resolveRequest = resolve;
    }));

    const first = loadSharedRelationTopology({
      clusters: ["cluster-a"],
      applications: ["application-b", "application-a"],
    });
    const second = loadSharedRelationTopology({
      clusters: ["cluster-a"],
      applications: ["application-a", "application-b"],
    });

    expect(second).toBe(first);
    expect(getRelationTopology).toHaveBeenCalledTimes(1);
    resolveRequest(endpoint);
    await expect(first).resolves.toBe(endpoint);
    await Promise.resolve();

    getRelationTopology.mockResolvedValue(endpoint);
    await loadSharedRelationTopology({
      clusters: ["cluster-a"],
      applications: ["application-a", "application-b"],
    });
    expect(getRelationTopology).toHaveBeenCalledTimes(2);
  });

  it("never coalesces distinct authorization scopes", async () => {
    getRelationTopology.mockResolvedValue(endpoint);

    await Promise.all([
      loadSharedRelationTopology({ clusters: ["cluster-a"], applications: [] }),
      loadSharedRelationTopology({ clusters: ["cluster-b"], applications: [] }),
    ]);

    expect(getRelationTopology).toHaveBeenCalledTimes(2);
  });
});
