import { useEffect, useState } from "react";

import { getRelationTopology } from "../api/relation-topology";
import type { RelationTopologyQuery } from "../api/relation-topology";
import type { RelationTopologyEndpoint } from "../api/relation-topology-schemas";

// UI-PHASE2-001 TOP-01/TOP-07 · DEMO_WIRING_PLAN §3.4: a typed live adapter for
// the service-topology (Resource Flow) surface. Structure — node/edge identity,
// relation kind, evidence plane — comes from `GET /api/topology?view=relations`.
// This feed carries NO telemetry: RPS/p99/error rate are never derived from
// relation evidence (join traffic separately). A legitimately `unavailable`
// topology renders an honest empty state, never fabricated structure.

export type RelationTopologyStatus = "loading" | "ready" | "unavailable" | "error";

export type RelationNodeHealth = "ok" | "warn" | "unknown";

export type RelationNodeCategory =
  | "workload"
  | "pod"
  | "node"
  | "service"
  | "endpoint"
  | "event"
  | "other";

export type RelationEdgeKind = "owns" | "runs_on" | "selects" | "routes_to";

export interface RelationNodeView {
  id: string;
  clusterId: string;
  name: string;
  kind: string;
  resourceType: string;
  namespace: string | null;
  category: RelationNodeCategory;
  status: string;
  health: RelationNodeHealth;
  /** Application bindings explicitly projected by the relation topology contract. */
  applicationIds: string[];
  /** Stable cluster/namespace/name identity used to join traffic observations. */
  serviceKey: string;
}

export interface RelationEdgeView {
  id: string;
  from: string;
  to: string;
  kind: RelationEdgeKind;
}

export interface RelationTopologyView {
  status: RelationTopologyStatus;
  nodes: RelationNodeView[];
  edges: RelationEdgeView[];
  rootIds: string[];
  /** Full evidence graph retained for exact resource-to-Pod interactions. */
  evidenceNodes: RelationNodeView[];
  evidenceEdges: RelationEdgeView[];
  truncated: boolean;
  omittedNodeCount: number;
  omittedEdgeCount: number;
  partialReasonCodes: string[];
  clusterId: string | null;
}

const LOADING: RelationTopologyView = {
  status: "loading",
  nodes: [],
  edges: [],
  rootIds: [],
  evidenceNodes: [],
  evidenceEdges: [],
  truncated: false,
  omittedNodeCount: 0,
  omittedEdgeCount: 0,
  partialReasonCodes: [],
  clusterId: null,
};

// The traffic graph and its auxiliary panel consume the same read-on-open
// relation snapshot. Share only the in-flight request: retaining settled
// payloads here could expose a previous session's authorized graph after an
// account/workspace switch. React StrictMode remounts and simultaneous
// consumers still collapse to one canonical HTTP request.
const inFlightRelationTopology = new Map<string, Promise<RelationTopologyEndpoint>>();

function relationTopologyRequestKey(query: RelationTopologyQuery): string {
  return JSON.stringify({
    clusters: [...(query.clusters ?? [])].sort(),
    applications: [...(query.applications ?? [])].sort(),
    snapshotRevision: query.snapshotRevision ?? null,
  });
}

export function loadSharedRelationTopology(
  query: RelationTopologyQuery,
): Promise<RelationTopologyEndpoint> {
  const key = relationTopologyRequestKey(query);
  const existing = inFlightRelationTopology.get(key);
  if (existing) return existing;
  const request = getRelationTopology(query);
  inFlightRelationTopology.set(key, request);
  const release = () => {
    if (inFlightRelationTopology.get(key) === request) {
      inFlightRelationTopology.delete(key);
    }
  };
  void request.then(release, release);
  return request;
}

/** @internal test isolation for the request coalescer. */
export function resetSharedRelationTopologyForTests(): void {
  inFlightRelationTopology.clear();
}

/** Stable identity a topology node and a traffic endpoint can both produce. */
export function serviceKeyOf(
  clusterId: string,
  namespace: string | null,
  name: string,
): string {
  return `${clusterId}/${namespace ?? "-"}/${name}`;
}

function healthOf(raw: string): RelationNodeHealth {
  if (raw === "healthy") return "ok";
  if (raw === "degraded") return "warn";
  return "unknown";
}

export function toRelationTopologyView(
  endpoint: RelationTopologyEndpoint,
): RelationTopologyView {
  const base = {
    truncated: endpoint.truncated,
    omittedNodeCount: endpoint.omitted_node_count,
    omittedEdgeCount: endpoint.omitted_edge_count,
    partialReasonCodes: [...endpoint.partial_reason_codes],
    clusterId: endpoint.cluster.cluster_id,
  };
  if (endpoint.availability === "unavailable") {
    return {
      status: "unavailable",
      nodes: [],
      edges: [],
      rootIds: [],
      evidenceNodes: [],
      evidenceEdges: [],
      ...base,
    };
  }
  // M20: backend node_id/edge_id는 단일 클러스터 스코프에서만 유일하다. 여러 클러스터를
  // union할 때 동일 id(예: 두 클러스터의 `default/kubernetes`)가 충돌해 노드가 하나로
  // 합쳐지거나 React duplicate-key가 나므로, cluster_id로 한정한 합성 id를 만든다.
  // 같은 endpoint 안에서 node·edge·root를 동일 접두로 변환하므로 from/to 참조는 그대로 일치한다.
  const cid = endpoint.cluster.cluster_id;
  const qualify = (rawId: string): string => `${cid}\u0000${rawId}`;
  const nodes: RelationNodeView[] = endpoint.nodes.map((node) => ({
    id: qualify(node.node_id),
    clusterId: node.identity.cluster_id,
    name: node.identity.name,
    kind: node.identity.kind,
    resourceType: node.identity.resource_type,
    namespace: node.identity.namespace,
    category: node.category,
    status: node.status,
    health: healthOf(node.health),
    applicationIds: [...node.application_ids],
    serviceKey: serviceKeyOf(
      node.identity.cluster_id,
      node.identity.namespace,
      node.identity.name,
    ),
  }));
  const edges: RelationEdgeView[] = endpoint.edges.map((edge) => ({
    id: qualify(edge.edge_id),
    from: qualify(edge.from_node_id),
    to: qualify(edge.to_node_id),
    kind: edge.kind,
  }));
  // 서비스 호출 관계도는 서비스/워크로드 단위로 정규화한다. 계약이 노출하는 raw pod·
  // endpoint·event 노드는 서비스 관계의 잡음이라 제외한다(계약 nodes 배열에서만 선택,
  // 이름으로 서비스를 지어내지 않음). 걸러진 노드를 참조하는 edge·root도 함께 정리한다.
  const SERVICE_CATEGORIES = new Set<RelationNodeCategory>(["service", "workload"]);
  const serviceNodes = nodes.filter((node) => SERVICE_CATEGORIES.has(node.category));
  const keptIds = new Set(serviceNodes.map((node) => node.id));
  const serviceEdges = edges.filter((edge) => keptIds.has(edge.from) && keptIds.has(edge.to));
  const serviceRoots = endpoint.root_node_ids.map(qualify).filter((id) => keptIds.has(id));
  return {
    status: "ready",
    nodes: serviceNodes,
    edges: serviceEdges,
    rootIds: serviceRoots,
    evidenceNodes: nodes,
    evidenceEdges: edges,
    ...base,
  };
}

function isAbortError(error: unknown): boolean {
  return typeof error === "object" && error !== null && "name" in error
    && (error as { name?: unknown }).name === "AbortError";
}

/**
 * Reads one relation-topology snapshot for the scoped clusters. A scope change
 * aborts the obsolete request so a stale response can never overwrite the new
 * selection. Never seeds state synchronously — only `.then/.catch` mutate.
 */
export function useRelationTopology(
  clusterIds: readonly string[],
  applicationIds: readonly string[] = [],
): RelationTopologyView {
  const [view, setView] = useState<RelationTopologyView>(LOADING);
  const clusterKey = clusterIds.join(",");
  const applicationKey = applicationIds.join(",");
  useEffect(() => {
    const ids = clusterKey ? clusterKey.split(",") : [];
    const applications = applicationKey ? applicationKey.split(",") : [];
    const controller = new AbortController();
    // `GET /api/topology`는 그래프 클러스터가 정확히 1개일 때만 유효하다(len != 1 → 422).
    // 다중/전체 클러스터 스코프는 클러스터별로 개별 조회한 뒤 클라이언트에서 union한다.
    if (ids.length === 0) {
      void Promise.resolve().then(() => {
        if (!controller.signal.aborted) setView({ ...LOADING, status: "unavailable" });
      });
      return () => controller.abort();
    }
    if (ids.length === 1) {
      void loadSharedRelationTopology({ clusters: ids, applications })
        .then((endpoint) => {
          if (controller.signal.aborted) return;
          setView(toRelationTopologyView(endpoint));
        })
        .catch((cause: unknown) => {
          if (controller.signal.aborted || isAbortError(cause)) return;
          setView((prev) => ({ ...prev, status: "error" }));
        });
      return () => controller.abort();
    }
    void Promise.all(ids.map((id) => (
      loadSharedRelationTopology({ clusters: [id], applications })
        .then(toRelationTopologyView)
        .catch((cause: unknown): RelationTopologyView | null => {
          if (isAbortError(cause)) return null;
          return null;
        })
    )))
      .then((views) => {
        if (controller.signal.aborted) return;
        const ready = views.filter(
          (candidate): candidate is RelationTopologyView => candidate !== null && candidate.status === "ready",
        );
        if (ready.length === 0) {
          setView({ ...LOADING, status: "unavailable" });
          return;
        }
        const nodeMap = new Map<string, RelationNodeView>();
        const edgeMap = new Map<string, RelationEdgeView>();
        const evidenceNodeMap = new Map<string, RelationNodeView>();
        const evidenceEdgeMap = new Map<string, RelationEdgeView>();
        const rootIds: string[] = [];
        const reasons = new Set<string>();
        let truncated = false;
        let omittedNodeCount = 0;
        let omittedEdgeCount = 0;
        for (const snapshot of ready) {
          for (const node of snapshot.nodes) nodeMap.set(node.id, node);
          for (const edge of snapshot.edges) edgeMap.set(edge.id, edge);
          for (const node of snapshot.evidenceNodes) evidenceNodeMap.set(node.id, node);
          for (const edge of snapshot.evidenceEdges) evidenceEdgeMap.set(edge.id, edge);
          rootIds.push(...snapshot.rootIds);
          snapshot.partialReasonCodes.forEach((code) => reasons.add(code));
          truncated = truncated || snapshot.truncated;
          omittedNodeCount += snapshot.omittedNodeCount;
          omittedEdgeCount += snapshot.omittedEdgeCount;
        }
        setView({
          status: "ready",
          nodes: [...nodeMap.values()],
          edges: [...edgeMap.values()],
          rootIds: [...new Set(rootIds)],
          evidenceNodes: [...evidenceNodeMap.values()],
          evidenceEdges: [...evidenceEdgeMap.values()],
          truncated,
          omittedNodeCount,
          omittedEdgeCount,
          partialReasonCodes: [...reasons],
          clusterId: null,
        });
      });
    return () => controller.abort();
  }, [applicationKey, clusterKey]);
  return view;
}
