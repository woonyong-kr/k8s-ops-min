import {
  createEmptyProductDetailQuery,
  createEmptyUnifiedFilterState,
  type FilterUrlParseResult,
  type InvalidFilterValues,
  type KubernetesLabelFilter,
  type NamespaceFilterRef,
  type ProductDetailQuery,
  type UnifiedFilterState,
} from "./filterContract";
import {
  appendBoolean,
  appendList,
  appendText,
  hasQueryKey,
  isKubernetesNamespace,
  isStableFilterValue,
  labelSelector,
  namespaceSelector,
  normalizeLabels,
  normalizeNamespaceRefs,
  normalizeSearch,
  normalizeStableList,
  parseLabelSelector,
  parseStrictQuery,
  readMultiValues,
  readQueryValue,
  type StrictQuery,
} from "./filterUrlSyntax";
import {
  parseBooleanQuery,
  parseResourceView,
} from "./filterUrlScalars";
import { appendProductDetail, parseProductDetailQuery } from "./detailUrlCodec";

const LEGACY_CLUSTER_QUERY_KEY = "cluster";
type MutableInvalidFilterValues = Record<keyof InvalidFilterValues, string[]>;

export function parseProductFilterUrl(search: string): FilterUrlParseResult {
  const params = parseStrictQuery(search);
  const invalid = createInvalidFilterValues();
  const state = createEmptyUnifiedFilterState();

  state.common.clusters = hasQueryKey(params, "clusters")
    ? parseStableList(params, "clusters", invalid.clusters)
    : parseLegacyCluster(params, invalid.clusters);
  state.common.namespaces = parseNamespaceList(params, invalid.namespaces);
  state.common.applications = parseStableList(params, "applications", invalid.applications);
  state.common.labels = parseLabelList(params, invalid.labels);

  state.resources.types = parseStableList(
    params,
    "resources.types",
    invalid.resourcesTypes,
  );
  state.resources.health = parseStableList(
    params,
    "resources.health",
    invalid.resourcesHealth,
  );
  state.resources.includeDeleted = parseBooleanQuery(
    params,
    "resources.includeDeleted",
    invalid.resourcesIncludeDeleted,
  );
  state.resources.query = readQueryValue(params, "resources.q") ?? "";
  state.resources.view = parseResourceView(params, invalid.resourcesView);

  state.issues.severity = parseStableList(
    params,
    "issues.severity",
    invalid.issuesSeverity,
  );
  state.issues.status = parseStableList(params, "issues.status", invalid.issuesStatus);
  state.issues.environment = parseStableList(
    params,
    "issues.environment",
    invalid.issuesEnvironment,
  );
  state.issues.query = readQueryValue(params, "issues.q") ?? "";

  state.applicationSurface.environment = parseStableList(
    params,
    "applications.environment",
    invalid.applicationsEnvironment,
  );
  state.applicationSurface.status = parseStableList(
    params,
    "applications.status",
    invalid.applicationsStatus,
  );
  state.applicationSurface.pendingPromotion = parseBooleanQuery(
    params,
    "applications.pendingPromotion",
    invalid.applicationsPendingPromotion,
  );
  state.applicationSurface.query = readQueryValue(params, "applications.q") ?? "";

  state.gitops.environment = parseStableList(
    params,
    "gitops.environment",
    invalid.gitopsEnvironment,
  );
  state.gitops.approval = parseStableList(
    params,
    "gitops.approval",
    invalid.gitopsApproval,
  );
  state.gitops.changeType = parseStableList(
    params,
    "gitops.changeType",
    invalid.gitopsChangeType,
  );
  state.gitops.query = readQueryValue(params, "gitops.q") ?? "";

  state.checks.severity = parseStableList(
    params,
    "checks.severity",
    invalid.checksSeverity,
  );
  state.checks.category = parseStableList(
    params,
    "checks.category",
    invalid.checksCategory,
  );
  state.checks.query = readQueryValue(params, "checks.q") ?? "";

  const detail = parseProductDetailQuery(
    params,
    invalid.detailFull,
    invalid.timeRange,
    invalid.timeAt,
    invalid.graph,
  );
  return {
    state,
    detail,
    invalidValues: invalid,
    needsCanonicalWrite:
      serializeProductFilterUrl(state, detail) !== normalizeSearch(search),
  };
}

export function serializeProductFilterUrl(
  state: UnifiedFilterState,
  detail: ProductDetailQuery = createEmptyProductDetailQuery(),
): string {
  const pairs: string[] = [];
  appendCommonFilters(pairs, state);
  appendResourceFilters(pairs, state);
  appendIssueFilters(pairs, state);
  appendApplicationFilters(pairs, state);
  appendGitOpsFilters(pairs, state);
  appendCheckFilters(pairs, state);
  appendProductDetail(pairs, detail);
  return pairs.length > 0 ? `?${pairs.join("&")}` : "";
}

function appendCommonFilters(pairs: string[], state: UnifiedFilterState) {
  appendList(pairs, "clusters", normalizeStableList(state.common.clusters));
  appendList(
    pairs,
    "namespaces",
    normalizeNamespaceRefs(state.common.namespaces).map(namespaceSelector),
  );
  appendList(pairs, "applications", normalizeStableList(state.common.applications));
  appendList(pairs, "labels", normalizeLabels(state.common.labels).map(labelSelector));
}

function appendResourceFilters(pairs: string[], state: UnifiedFilterState) {
  appendList(pairs, "resources.types", normalizeStableList(state.resources.types));
  appendList(pairs, "resources.health", normalizeStableList(state.resources.health));
  appendBoolean(pairs, "resources.includeDeleted", state.resources.includeDeleted);
  appendText(pairs, "resources.q", state.resources.query);
  if (state.resources.view === "graph") appendText(pairs, "resources.view", "graph");
}

function appendIssueFilters(pairs: string[], state: UnifiedFilterState) {
  appendList(pairs, "issues.severity", normalizeStableList(state.issues.severity));
  appendList(pairs, "issues.status", normalizeStableList(state.issues.status));
  appendList(pairs, "issues.environment", normalizeStableList(state.issues.environment));
  appendText(pairs, "issues.q", state.issues.query);
}

function appendApplicationFilters(pairs: string[], state: UnifiedFilterState) {
  appendList(
    pairs,
    "applications.environment",
    normalizeStableList(state.applicationSurface.environment),
  );
  appendList(
    pairs,
    "applications.status",
    normalizeStableList(state.applicationSurface.status),
  );
  appendBoolean(
    pairs,
    "applications.pendingPromotion",
    state.applicationSurface.pendingPromotion,
  );
  appendText(pairs, "applications.q", state.applicationSurface.query);
}

function appendGitOpsFilters(pairs: string[], state: UnifiedFilterState) {
  appendList(pairs, "gitops.environment", normalizeStableList(state.gitops.environment));
  appendList(pairs, "gitops.approval", normalizeStableList(state.gitops.approval));
  appendList(pairs, "gitops.changeType", normalizeStableList(state.gitops.changeType));
  appendText(pairs, "gitops.q", state.gitops.query);
}

function appendCheckFilters(pairs: string[], state: UnifiedFilterState) {
  appendList(pairs, "checks.severity", normalizeStableList(state.checks.severity));
  appendList(pairs, "checks.category", normalizeStableList(state.checks.category));
  appendText(pairs, "checks.q", state.checks.query);
}

function parseLegacyCluster(params: StrictQuery, invalid: string[]): readonly string[] {
  if (!hasQueryKey(params, LEGACY_CLUSTER_QUERY_KEY)) return [];
  const value = readQueryValue(params, LEGACY_CLUSTER_QUERY_KEY) ?? "";
  if (!isStableFilterValue(value)) {
    if (value.length > 0) invalid.push(value);
    return [];
  }
  return [value];
}

function parseStableList(
  params: StrictQuery,
  key: string,
  invalid: string[],
): readonly string[] {
  const accepted: string[] = [];
  for (const value of readMultiValues(params, key, invalid)) {
    if (isStableFilterValue(value)) accepted.push(value);
    else if (value.length > 0) invalid.push(value);
  }
  return normalizeStableList(accepted);
}

function parseNamespaceList(
  params: StrictQuery,
  invalid: string[],
): readonly NamespaceFilterRef[] {
  const accepted: NamespaceFilterRef[] = [];
  for (const value of readMultiValues(params, "namespaces", invalid)) {
    const slash = value.lastIndexOf("/");
    const clusterId = slash > 0 ? value.slice(0, slash) : "";
    const namespace = slash > 0 ? value.slice(slash + 1) : "";
    if (isStableFilterValue(clusterId) && isKubernetesNamespace(namespace)) {
      accepted.push({ clusterId, namespace });
    } else if (value.length > 0) invalid.push(value);
  }
  return normalizeNamespaceRefs(accepted);
}

function parseLabelList(
  params: StrictQuery,
  invalid: string[],
): readonly KubernetesLabelFilter[] {
  const accepted: KubernetesLabelFilter[] = [];
  for (const selector of readMultiValues(params, "labels", invalid)) {
    const label = parseLabelSelector(selector);
    if (label) accepted.push(label);
    else if (selector.length > 0) invalid.push(selector);
  }
  return normalizeLabels(accepted);
}

function createInvalidFilterValues(): MutableInvalidFilterValues {
  return {
    clusters: [], namespaces: [], applications: [], labels: [],
    resourcesTypes: [], resourcesHealth: [], resourcesIncludeDeleted: [], resourcesView: [],
    issuesSeverity: [], issuesStatus: [],
    issuesEnvironment: [], applicationsEnvironment: [], applicationsStatus: [],
    applicationsPendingPromotion: [],
    gitopsEnvironment: [], gitopsApproval: [], gitopsChangeType: [],
    checksSeverity: [], checksCategory: [], detailFull: [], timeRange: [], timeAt: [], graph: [],
  };
}
