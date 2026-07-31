import type { HomeClusterChoice } from "../home/homeContract";

export function canOfferClusterDisconnect(
  roles: readonly string[] | null | undefined,
  cluster: HomeClusterChoice,
): boolean {
  return roles?.includes("service_admin") === true && !isManagementCluster(cluster);
}

export function activeClusterChoices<T extends HomeClusterChoice>(clusters: readonly T[]): T[] {
  return clusters.filter((cluster) => cluster.registrationState !== "expired");
}

export function refreshAfterClusterDisconnect(
  scope: {
    requestedClusterIds: readonly string[];
    toggleCluster: (clusterId: string) => void;
    refresh: () => void;
  },
  clusterId: string,
): void {
  if (scope.requestedClusterIds.includes(clusterId)) scope.toggleCluster(clusterId);
  scope.refresh();
}

function isManagementCluster(cluster: HomeClusterChoice): boolean {
  return cluster.environment.trim().toLocaleLowerCase() === "management";
}
