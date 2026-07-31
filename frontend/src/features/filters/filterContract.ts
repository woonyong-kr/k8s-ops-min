export type FilterAxisOperator = "and" | "or";

export interface NamespaceFilterRef {
  clusterId: string;
  namespace: string;
}

export interface KubernetesLabelFilter {
  key: string;
  value: string;
}

export type ResourceView = "graph" | "table";
export type TimelineRange = "15m" | "1h" | "6h" | "24h";

export interface UnifiedFilterState {
  common: {
    clusters: readonly string[];
    namespaces: readonly NamespaceFilterRef[];
    applications: readonly string[];
    labels: readonly KubernetesLabelFilter[];
  };
  resources: {
    types: readonly string[];
    health: readonly string[];
    includeDeleted: boolean;
    query: string;
    view: ResourceView;
  };
  issues: {
    severity: readonly string[];
    status: readonly string[];
    environment: readonly string[];
    query: string;
  };
  applicationSurface: {
    environment: readonly string[];
    status: readonly string[];
    pendingPromotion: boolean;
    query: string;
  };
  gitops: {
    environment: readonly string[];
    approval: readonly string[];
    changeType: readonly string[];
    query: string;
  };
  checks: {
    severity: readonly string[];
    category: readonly string[];
    query: string;
  };
}

export const COMMON_FILTER_AXIS_OPERATORS = {
  clusters: "or",
  namespaces: "or",
  applications: "or",
  labels: "and",
} as const satisfies Record<keyof UnifiedFilterState["common"], FilterAxisOperator>;

export interface ProductDetailQuery {
  detail: string | null;
  application?: string | null;
  applicationInstance?: string | null;
  applicationWorkload?: string | null;
  resource: string | null;
  resourceKind: string | null;
  tab: string | null;
  full: boolean;
  node: string | null;
  resourceTopologyView?: "physical" | "relations" | null;
  workflowPlan?: string | null;
  workflowView?: "overview" | "edit" | "runs" | "yaml" | null;
  workflowMode?: "new" | null;
  timeRange?: TimelineRange;
  timeAt?: number;
  graphCollapsed?: true;
}

export interface InvalidFilterValues {
  clusters: readonly string[];
  namespaces: readonly string[];
  applications: readonly string[];
  labels: readonly string[];
  resourcesTypes: readonly string[];
  resourcesHealth: readonly string[];
  resourcesIncludeDeleted: readonly string[];
  resourcesView: readonly string[];
  issuesSeverity: readonly string[];
  issuesStatus: readonly string[];
  issuesEnvironment: readonly string[];
  applicationsEnvironment: readonly string[];
  applicationsStatus: readonly string[];
  applicationsPendingPromotion: readonly string[];
  gitopsEnvironment: readonly string[];
  gitopsApproval: readonly string[];
  gitopsChangeType: readonly string[];
  checksSeverity: readonly string[];
  checksCategory: readonly string[];
  detailFull: readonly string[];
  timeRange: readonly string[];
  timeAt: readonly string[];
  graph: readonly string[];
}

export interface FilterUrlParseResult {
  state: UnifiedFilterState;
  detail: ProductDetailQuery;
  invalidValues: InvalidFilterValues;
  needsCanonicalWrite: boolean;
}

export type FilterMutationIntent =
  | "canonicalize"
  | "chip-add"
  | "chip-remove"
  | "clear-labels"
  | "clear-filters"
  | "view-change"
  | "legacy-migration"
  | "typing";

export type FilterHistoryMode = "push" | "replace";

export type DetailMutationIntent =
  | "detail-open"
  | "detail-close"
  | "detail-tab"
  | "detail-instance"
  | "detail-instance-default"
  | "detail-workload"
  | "detail-workload-default"
  | "detail-workload-recovery"
  | "detail-expand"
  | "topology-view"
  | "topology-view-reset"
  | "time-range"
  | "time-at"
  | "graph-visibility"
  | "drill-in";

export type UnifiedFilterUpdater =
  | UnifiedFilterState
  | ((current: UnifiedFilterState) => UnifiedFilterState);

export type ProductDetailUpdater =
  | ProductDetailQuery
  | ((current: ProductDetailQuery) => ProductDetailQuery);

export interface UnifiedFilterController extends FilterUrlParseResult {
  canonicalize(): void;
  navigationHref(path: `/${string}`, detail?: ProductDetailQuery): string;
  updateDetail(update: ProductDetailUpdater, intent: DetailMutationIntent): void;
  updateFilters(update: UnifiedFilterUpdater, intent: FilterMutationIntent): void;
}

export function createEmptyUnifiedFilterState(): UnifiedFilterState {
  return {
    common: {
      clusters: [],
      namespaces: [],
      applications: [],
      labels: [],
    },
    resources: {
      types: [],
      health: [],
      includeDeleted: false,
      query: "",
      view: "table",
    },
    issues: {
      severity: [],
      status: [],
      environment: [],
      query: "",
    },
    applicationSurface: {
      environment: [],
      status: [],
      pendingPromotion: false,
      query: "",
    },
    gitops: {
      environment: [],
      approval: [],
      changeType: [],
      query: "",
    },
    checks: {
      severity: [],
      category: [],
      query: "",
    },
  };
}

export function createEmptyProductDetailQuery(): ProductDetailQuery {
  return {
    detail: null,
    resource: null,
    resourceKind: null,
    tab: null,
    full: false,
    node: null,
  };
}
