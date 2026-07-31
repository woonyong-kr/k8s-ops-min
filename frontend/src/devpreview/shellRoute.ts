const SURFACES: ReadonlySet<string> = new Set([
  "home",
  "resources",
  "connect",
  "deploy",
  "issues",
  "timeline",
  "checks",
  "cost",
  "alerts",
  "ai",
  "settings",
] as const);

const RESOURCE_VIEWS: ReadonlySet<string> = new Set(["map", "list", "flow"]);

export type ShellRoute = {
  surface:
    | "home"
    | "resources"
    | "connect"
    | "deploy"
    | "issues"
    | "timeline"
    | "checks"
    | "cost"
    | "alerts"
    | "ai"
    | "settings";
  resourceView: "map" | "list" | "flow";
  clusterId: string | null;
};

export function parseShellRoute(search: string): ShellRoute {
  const params = new URLSearchParams(search);
  const rawSurface = params.get("surface");
  const surface = rawSurface !== null && SURFACES.has(rawSurface)
    ? rawSurface as ShellRoute["surface"]
    : "home";
  const rawResourceView = params.get("resource_view");
  const resourceView =
    rawResourceView !== null && RESOURCE_VIEWS.has(rawResourceView)
      ? rawResourceView as ShellRoute["resourceView"]
      : "map";
  const clusterId =
    surface === "resources" && resourceView === "map"
      ? params.get("cluster")?.trim() || null
      : null;

  return { surface, resourceView, clusterId };
}

export function updateShellRouteSearch(
  currentSearch: string,
  route: ShellRoute,
): string {
  const params = new URLSearchParams(currentSearch);

  if (route.surface === "home") params.delete("surface");
  else params.set("surface", route.surface);

  if (route.surface === "resources") {
    params.set("resource_view", route.resourceView);
  } else {
    params.delete("resource_view");
  }

  if (
    route.surface === "resources"
    && route.resourceView === "map"
    && route.clusterId
  ) {
    params.set("cluster", route.clusterId);
  } else {
    params.delete("cluster");
  }

  const serialized = params.toString();
  return serialized ? `?${serialized}` : "";
}
