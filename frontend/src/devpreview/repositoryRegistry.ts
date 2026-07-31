import type { ApplicationView } from "./deployFeed";

export interface RepositoryApplicationSummary {
  id: string;
  name: string;
  branch: string | null;
  manifestPath: string | null;
  deliveryStatus: string | null;
  healthStatus: string | null;
}

export interface RepositoryGroup {
  repositoryRef: string;
  applications: RepositoryApplicationSummary[];
}

/** Collapses active application rows into the repository identity visible to users. */
export function groupApplicationsByRepository(
  applications: readonly ApplicationView[],
): RepositoryGroup[] {
  const groups = new Map<string, RepositoryApplicationSummary[]>();
  for (const application of applications) {
    const repositoryRef = application.repositoryRef?.trim();
    if (!repositoryRef || application.lifecycleStatus === "archived") continue;
    const key = repositoryRef.toLowerCase();
    const group = groups.get(key) ?? [];
    group.push({
      id: application.id,
      name: application.name,
      branch: application.defaultBranch,
      manifestPath: application.manifestPath,
      deliveryStatus: application.deliveryStatus,
      healthStatus: application.healthStatus,
    });
    groups.set(key, group);
  }
  return [...groups.entries()]
    .map(([key, applicationsForRepository]) => ({
      repositoryRef:
        applications.find(
          (application) => application.repositoryRef?.trim().toLowerCase() === key,
        )?.repositoryRef?.trim() ?? key,
      applications: applicationsForRepository.sort((left, right) =>
        left.name.localeCompare(right.name),
      ),
    }))
    .sort((left, right) => left.repositoryRef.localeCompare(right.repositoryRef));
}
