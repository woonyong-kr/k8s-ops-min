export type ProductSurfaceId =
  | "home"
  | "resources"
  | "issues"
  | "topology"
  | "applications"
  | "timeline"
  | "traffic"
  | "helm"
  | "gitops"
  | "checks"
  | "cost"
  | "clusters"
  | "alerts"
  | "settings";

export type ProductRouteIcon = ProductSurfaceId;

export type ProductRouteFamily = "reference-primary" | "product";

export interface ProductRouteDefinition {
  id: ProductSurfaceId;
  label: string;
  path: `/${string}`;
  aliases: readonly `/${string}`[];
  icon: ProductRouteIcon;
  shortcut: `g ${string}`;
  match: "exact" | "prefix";
  landing: boolean;
  family: ProductRouteFamily;
  navigation: boolean;
  redirect: ProductRouteRedirect | null;
}

export interface ProductRouteRedirect {
  path: `/${string}`;
  search: readonly (readonly [key: string, value: string])[];
}

/**
 * The only product-owned route descriptor. Screens, shortcut handling, aliases,
 * and unavailable-route feedback must consume this list rather than duplicate
 * route paths in their own UI code.
 */
export const PRODUCT_ROUTE_CATALOG = [
  route("home", "Home", "/home", "g h", { match: "exact", landing: true, family: "reference-primary" }),
  route("resources", "Resources", "/resources", "g r", { family: "reference-primary" }),
  route("issues", "Incidents", "/issues", "g i", { family: "reference-primary" }),
  route("topology", "Topology", "/topology", "g t", { family: "reference-primary" }),
  route("applications", "Applications", "/applications", "g a", { family: "reference-primary" }),
  route("timeline", "Timeline", "/timeline", "g l", { family: "reference-primary" }),
  route("traffic", "Traffic", "/traffic", "g f", { family: "reference-primary" }),
  route("helm", "Helm", "/helm", "g m", { family: "reference-primary" }),
  route("gitops", "GitOps", "/gitops", "g o", {
    aliases: ["/workflows"],
    family: "reference-primary",
  }),
  route("checks", "Checks", "/checks", "g u", {
    aliases: ["/audit"],
    family: "reference-primary",
    navigation: false,
    redirect: { path: "/issues", search: [["view", "checks"]] },
  }),
  route("cost", "Cost", "/cost", "g c", { family: "reference-primary" }),
  route("clusters", "Clusters", "/clusters", "g k"),
  route("alerts", "Alerts", "/alerts", "g b"),
  route("settings", "Settings", "/settings", "g s"),
] as const satisfies readonly ProductRouteDefinition[];

export function referenceNavigationRoutes(): readonly ProductRouteDefinition[] {
  return PRODUCT_ROUTE_CATALOG.filter(({ family, navigation }) => (
    family === "reference-primary" && navigation
  ));
}

export function productKeyboardNavigationRoutes(): readonly ProductRouteDefinition[] {
  return PRODUCT_ROUTE_CATALOG.filter(({ navigation }) => navigation);
}

export function productNavigationForReleasedSurfaces(
  releasedSurfaceIds: ReadonlySet<ProductSurfaceId>,
): readonly ProductRouteDefinition[] {
  return PRODUCT_ROUTE_CATALOG.filter((routeDefinition) => (
    routeDefinition.navigation && releasedSurfaceIds.has(routeDefinition.id)
  ));
}

export function landingProductRouteForReleasedSurfaces(
  releasedSurfaceIds: ReadonlySet<ProductSurfaceId>,
): ProductRouteDefinition {
  const releasedRoutes = productNavigationForReleasedSurfaces(releasedSurfaceIds);
  const landingRoute = releasedRoutes.find((routeDefinition) => routeDefinition.landing);
  return landingRoute ?? releasedRoutes[0]
    ?? failToResolveLandingRoute(releasedSurfaceIds);
}

export function productRouteForPath(pathname: string): ProductRouteDefinition | null {
  return PRODUCT_ROUTE_CATALOG.find((routeDefinition) => ownsPath(routeDefinition, pathname)) ?? null;
}

export function productRoutePaths(routeDefinition: ProductRouteDefinition): readonly `/${string}`[] {
  return [routeDefinition.path, ...routeDefinition.aliases];
}

export function resolveProductRoute(pathname: string): ProductRouteDefinition {
  return productRouteForPath(pathname) ?? PRODUCT_ROUTE_CATALOG[0];
}

export function routeDefinitionForSurface(
  surfaceId: ProductSurfaceId,
): ProductRouteDefinition {
  const routeDefinition = PRODUCT_ROUTE_CATALOG.find((candidate) => candidate.id === surfaceId);
  if (!routeDefinition) throw new Error(`unknown product surface: ${surfaceId}`);
  return routeDefinition;
}

function route(
  id: ProductSurfaceId,
  label: string,
  path: `/${string}`,
  shortcut: `g ${string}`,
  behavior: ProductRouteBehavior = {},
): ProductRouteDefinition {
  return {
    id,
    label,
    path,
    aliases: behavior.aliases ?? [],
    icon: id,
    shortcut,
    match: behavior.match ?? "prefix",
    landing: behavior.landing ?? false,
    family: behavior.family ?? "product",
    navigation: behavior.navigation ?? true,
    redirect: behavior.redirect ?? null,
  };
}

interface ProductRouteBehavior {
  aliases?: readonly `/${string}`[];
  family?: ProductRouteFamily;
  match?: "exact" | "prefix";
  landing?: boolean;
  navigation?: boolean;
  redirect?: ProductRouteRedirect;
}

function failToResolveLandingRoute(
  releasedSurfaceIds: ReadonlySet<ProductSurfaceId>,
): never {
  throw new Error(`cannot resolve landing route without released surfaces: ${[...releasedSurfaceIds].join(",")}`);
}

function ownsPath(routeDefinition: ProductRouteDefinition, pathname: string): boolean {
  return productRoutePaths(routeDefinition).some((path) => {
    if (routeDefinition.match === "exact") return pathname === path;
    return pathname === path || pathname.startsWith(`${path}/`);
  });
}
