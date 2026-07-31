import { describe, expect, it } from "vitest";

import type { InventoryResource } from "../api/inventory-schemas";
import { projectInventoryResourceRow } from "./inventoryResourceTableProjection";
import { kindToResourceType } from "./inventoryResourcesFeed";

const NOW = Date.parse("2026-07-24T00:00:00Z");

function resource(
  kind: string,
  status: string,
  summary: Record<string, unknown>,
): InventoryResource {
  return {
    inventory_key: `${kind}:sandbox/example`,
    snapshot_id: "snapshot",
    workspace_id: "default",
    cluster_id: "game-server",
    resource_type: kind.toLowerCase(),
    api_version: "v1",
    kind,
    namespace: "sandbox",
    name: "example",
    uid: `${kind}:example`,
    resource_version: "1",
    status,
    health: "healthy",
    labels: {},
    annotations: {},
    summary,
    observed_at: "2026-07-24T00:00:00Z",
    first_seen_at: "2026-07-23T00:00:00Z",
    last_seen_at: "2026-07-24T00:00:00Z",
    deleted_at: null,
    created_at: "2026-07-23T00:00:00Z",
    updated_at: "2026-07-24T00:00:00Z",
  };
}

describe("inventory resource table projection", () => {
  it("projects observed Deployment replica and image evidence", () => {
    const row = projectInventoryResourceRow(resource("Deployment", "2/3", {
      desired_replicas: 3,
      ready_replicas: 2,
      updated_replicas: 3,
      available_replicas: 2,
      creation_timestamp: "2026-07-23T18:00:00Z",
      pod_template: {
        spec: {
          containers: [{ image: "ghcr.io/example/api:v2" }],
        },
      },
    }), NOW);

    expect(row).toMatchObject({
      ready: "2/3",
      utd: 3,
      avail: 2,
      img: "ghcr.io/example/api:v2",
      age: "6시간",
    });
  });

  it("projects Pod metrics without inventing missing limits", () => {
    const row = projectInventoryResourceRow(resource("Pod", "Running", {
      phase: "Running",
      containers: [{ name: "api", ready: true }],
      cpu_mcores: 125,
      cpu_limit_mcores: 250,
      mem_mib: 64,
    }), NOW);

    expect(row.ctr).toBe(1);
    expect(row.cpu).toEqual({ used: "125m", lim: "250m", pct: 50 });
    expect(row.mem).toBeUndefined();
  });

  it("projects Service selectors, ports, and observed external hosts", () => {
    const row = projectInventoryResourceRow(resource("Service", "LoadBalancer", {
      type: "LoadBalancer",
      selector: { app: "gateway", tier: "edge" },
      ports: [{ name: "http", port: 80, targetPort: 8080, protocol: "TCP" }],
      external_hosts: ["game.example.com"],
    }), NOW);

    expect(row).toMatchObject({
      type: "LoadBalancer",
      sel: "app=gateway, tier=edge",
      ports: "http · 80→8080 · TCP",
      ext: "game.example.com",
    });
  });

  it("uses the canonical backend aliases for abbreviated Kubernetes kinds", () => {
    expect(kindToResourceType("HPA")).toBe("hpa");
    expect(kindToResourceType("HorizontalPodAutoscaler")).toBe("hpa");
    expect(kindToResourceType("PVC")).toBe("pvc");
    expect(kindToResourceType("EndpointSlice")).toBe("endpoint");
  });
});
