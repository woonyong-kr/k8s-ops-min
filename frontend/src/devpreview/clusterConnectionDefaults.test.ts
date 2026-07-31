import { describe, expect, it } from "vitest";

import { DEFAULT_CLUSTER_DISPLAY_NAME } from "./clusterConnectionDefaults";

describe("cluster connection defaults", () => {
  it("prepares the English presentation cluster name", () => {
    expect(DEFAULT_CLUSTER_DISPLAY_NAME).toBe("battlegrounds-new");
  });
});
