import { describe, expect, it } from "vitest";

import type { DevpreviewCluster } from "./contracts";
import {
  canResumeClusterConnection,
  clusterConnectionProgress,
} from "./ConnectionControlCenter";
import { connectionPlatform } from "../devpreview-connect";

function cluster(
  connectionStage: DevpreviewCluster["connectionStage"],
  connectionStatus = "pending_install",
): DevpreviewCluster {
  return {
    id: "game-server-2040",
    workspaceId: "workspace-1",
    name: "game-server",
    displayName: "game-server",
    environment: "production",
    provider: "eks",
    connectionStatus,
    connectionStage,
    observationMode: "agent",
    lastObservedAt: null,
    kubernetesVersion: null,
    nodeCount: null,
    podCount: null,
    namespaceCount: null,
    incidentCount: null,
    role: "target",
    readOnly: false,
  };
}

describe("cluster connection lifecycle", () => {
  it("resumes a pending registration instead of creating another identity", () => {
    const pending = cluster("awaiting_install");
    expect(canResumeClusterConnection(pending)).toBe(true);
    expect(clusterConnectionProgress(pending).map((step) => step.state)).toEqual([
      "complete",
      "active",
      "pending",
      "pending",
    ]);
  });

  it("does not offer reinstall for an observed ready cluster", () => {
    const ready = cluster("ready", "online");
    expect(canResumeClusterConnection(ready)).toBe(false);
    expect(clusterConnectionProgress(ready).every((step) => step.state === "complete")).toBe(true);
  });

  it("maps observed providers to the existing install platform", () => {
    expect(connectionPlatform("eks")).toBe("aws");
    expect(connectionPlatform("gke")).toBe("gcp");
    expect(connectionPlatform("aks")).toBe("azure");
    expect(connectionPlatform("kind")).toBe("docker");
  });
});
