import { describe, expect, it } from "vitest";

import {
  parseShellRoute,
  updateShellRouteSearch,
} from "./shellRoute";

describe("cluster detail shell route", () => {
  it("restores a cluster map drill from a copied or refreshed URL", () => {
    expect(
      parseShellRoute(
        "?surface=resources&resource_view=map&cluster=battlegrounds-8352",
      ),
    ).toEqual({
      surface: "resources",
      resourceView: "map",
      clusterId: "battlegrounds-8352",
    });
  });

  it("serializes the selected cluster without dropping unrelated callback state", () => {
    const search = updateShellRouteSearch(
      "?github_app_installation_id=1234",
      {
        surface: "resources",
        resourceView: "map",
        clusterId: "battlegrounds-8352",
      },
    );
    const params = new URLSearchParams(search);

    expect(params.get("surface")).toBe("resources");
    expect(params.get("resource_view")).toBe("map");
    expect(params.get("cluster")).toBe("battlegrounds-8352");
    expect(params.get("github_app_installation_id")).toBe("1234");
  });

  it("removes only shell-owned parameters when returning home", () => {
    const search = updateShellRouteSearch(
      "?surface=resources&resource_view=map&cluster=battlegrounds-8352&keep=yes",
      {
        surface: "home",
        resourceView: "map",
        clusterId: null,
      },
    );
    const params = new URLSearchParams(search);

    expect(params.get("surface")).toBeNull();
    expect(params.get("resource_view")).toBeNull();
    expect(params.get("cluster")).toBeNull();
    expect(params.get("keep")).toBe("yes");
  });
});
