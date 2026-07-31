import { describe, expect, it } from "vitest";

import type { InvPod } from "./inventoryTopologyFeed";
import { podResourceSelection } from "./podSelection";

function pod(health: string): InvPod {
  return {
    name: "game-room-2-7d956c47f4-rqxwk",
    namespace: "sandbox",
    status: "Running",
    health,
    cluster: "battlegrounds-8352",
    key: "pod/game-room-2-7d956c47f4-rqxwk",
    serverId: "node-2",
    cpuMillicores: null,
    cpuRequestMillicores: null,
    cpuLimitMillicores: null,
    memoryMebibytes: null,
    memoryRequestMebibytes: null,
    memoryLimitMebibytes: null,
    restartCount: 0,
  };
}

describe("podResourceSelection", () => {
  it.each(["healthy", "critical", "unhealthy", "failed"])(
    "routes a %s Pod row to resource detail",
    (health) => {
      const selection = podResourceSelection(pod(health));

      expect(selection.destination).toBe("resource-detail");
      expect(selection.kind).toBe("Pod");
      expect(selection.data).toMatchObject({
        name: "game-room-2-7d956c47f4-rqxwk",
        ns: "sandbox",
        status: "Running",
        health,
        cluster: "battlegrounds-8352",
      });
    },
  );

  it("keeps the critical marker in detail data without changing the destination", () => {
    const selection = podResourceSelection(pod("critical"));

    expect(selection.destination).toBe("resource-detail");
    expect(selection.data.bad).toBe(true);
  });
});
