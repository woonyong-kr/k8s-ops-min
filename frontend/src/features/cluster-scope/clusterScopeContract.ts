import type {
  HomeClusterChoice,
  HomeClusterChoices,
  HomePort,
  HomePortFailure,
} from "../home/homeContract";
import type { AsyncResourceState } from "../../shared/data/asyncResourceState";

export type ClusterScopeChoice = HomeClusterChoice;
export type ClusterScopeCollection = HomeClusterChoices;
export type ClusterScopeFailure = HomePortFailure;
export type ClusterScopePort = Pick<HomePort, "listClusterChoices">;
export type ClusterScopeCollectionState = AsyncResourceState<
  ClusterScopeCollection,
  ClusterScopeFailure
>;

export type ClusterScopeSelection =
  | { kind: "resolving"; requestedIds: readonly string[] }
  | { kind: "unfiltered" }
  | { kind: "selected"; requestedId: string; cluster: ClusterScopeChoice; scopeKey: string }
  | {
    kind: "multiple";
    requestedIds: readonly string[];
    clusters: readonly ClusterScopeChoice[];
    unresolvedIds: readonly string[];
  }
  | { kind: "unknown"; requestedId: string }
  | { kind: "empty" }
  | { kind: "unavailable"; failure: ClusterScopeFailure };

export interface ClusterScopeValue {
  collection: ClusterScopeCollectionState;
  requestedClusterIds: readonly string[];
  requestedClusterId: string | null;
  selectedClusters: readonly ClusterScopeChoice[];
  selectedCluster: ClusterScopeChoice | null;
  selectedClusterExists: boolean;
  selection: ClusterScopeSelection;
  scopeKey: string | null;
  refresh: () => void;
  selectCluster: (clusterId: string) => void;
  toggleCluster: (clusterId: string) => void;
  clearClusters: () => void;
}
