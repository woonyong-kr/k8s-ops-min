import { describe, expect, it } from "vitest";

import type { InventoryResource } from "../api/inventory-schemas";
import {
  podsOwnedByWorkloads,
  podsSelectedByService,
} from "./podInventoryHighlight";

function resource(
  kind: string,
  namespace: string,
  name: string,
  options: {
    labels?: Record<string, string>;
    selector?: Record<string, string>;
    ownerKind?: string;
    ownerName?: string;
  } = {},
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
    labels: options.labels ?? {},
    annotations: {},
    summary: {
      ...(options.selector ? { selector: options.selector } : {}),
      ...(options.ownerKind ? { owner_kind: options.ownerKind } : {}),
      ...(options.ownerName ? { owner_name: options.ownerName } : {}),
    },
    observed_at: "2026-07-23T00:00:00Z",
    first_seen_at: "2026-07-23T00:00:00Z",
    last_seen_at: "2026-07-23T00:00:00Z",
    deleted_at: null,
    created_at: "2026-07-23T00:00:00Z",
    updated_at: "2026-07-23T00:00:00Z",
  };
}

describe("pod inventory highlight evidence", () => {
  it("matches only Pods satisfying every observed Service selector label", () => {
    const service = resource("Service", "sandbox", "game-room-2", {
      selector: {
        app: "game-server",
        "opsia.dev/room-id": "room-2",
      },
    });
    const matching = resource("Pod", "sandbox", "game-room-2-abc", {
      labels: {
        app: "game-server",
        "opsia.dev/room-id": "room-2",
      },
    });
    const otherRoom = resource("Pod", "sandbox", "game-room-1-abc", {
      labels: {
        app: "game-server",
        "opsia.dev/room-id": "room-1",
      },
    });

    expect(podsSelectedByService(
      "battle-ground-server-3647",
      service,
      [matching, otherRoom],
    )).toEqual([{
      clusterId: "battle-ground-server-3647",
      namespace: "sandbox",
      name: "game-room-2-abc",
    }]);
  });

  it("follows exact Deployment to ReplicaSet to Pod owner references", () => {
    const replicaSet = resource("ReplicaSet", "sandbox", "management-server-abc", {
      ownerKind: "Deployment",
      ownerName: "management-server",
    });
    const matching = resource("Pod", "sandbox", "management-server-abc-123", {
      ownerKind: "ReplicaSet",
      ownerName: "management-server-abc",
    });
    const other = resource("Pod", "sandbox", "game-room-2-abc-123", {
      ownerKind: "ReplicaSet",
      ownerName: "game-room-2-abc",
    });

    expect(podsOwnedByWorkloads(
      "battle-ground-server-3647",
      [{
        kind: "Deployment",
        namespace: "sandbox",
        name: "management-server",
      }],
      [replicaSet],
      [matching, other],
    )).toEqual([{
      clusterId: "battle-ground-server-3647",
      namespace: "sandbox",
      name: "management-server-abc-123",
    }]);
  });
});
