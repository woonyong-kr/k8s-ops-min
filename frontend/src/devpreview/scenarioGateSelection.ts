export interface ScenarioRunIdentity {
  repositoryRef: string | null;
  workflowRunId: string;
}

export interface ScenarioRunSelection<T extends ScenarioRunIdentity> {
  repositoryRef: string | null;
  runs: T[];
}

export function selectScenarioRuns<T extends ScenarioRunIdentity>(
  runs: T[],
  selectedRepository: string | null,
): ScenarioRunSelection<T> {
  const deployRuns = runs.filter(
    (run) => !run.workflowRunId.startsWith("workflow-connect-validation-"),
  );
  const requested = selectedRepository?.trim() || null;
  const repositoryRef = requested ?? deployRuns.find((run) => run.repositoryRef)?.repositoryRef ?? null;
  if (repositoryRef === null) return { repositoryRef: null, runs: [] };
  const normalized = repositoryRef.toLowerCase();
  return {
    repositoryRef,
    runs: deployRuns.filter((run) => run.repositoryRef?.toLowerCase() === normalized),
  };
}
