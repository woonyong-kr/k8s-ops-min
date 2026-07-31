import type {
  RelationEdgeView,
  RelationNodeView,
} from "./relationTopologyFeed";

export interface WorkloadHighlightIdentity {
  kind: string;
  namespace: string;
  name: string;
}

export interface PodHighlightIdentity {
  clusterId: string;
  namespace: string | null;
  name: string;
}

export type PodHighlightTarget =
  | {
      type: "pods";
      pods: PodHighlightIdentity[];
    }
  | {
      type: "service";
      clusterId: string;
      namespace: string;
      name: string;
    }
  | {
      type: "workloads";
      clusterId: string;
      workloads: WorkloadHighlightIdentity[];
    }
  | {
      type: "applications";
      clusterId: string;
      applicationIds: string[];
    };

export function podHighlightIdentity(
  clusterId: string,
  namespace: string | null,
  name: string,
): string {
  return `${clusterId}\u0000${namespace ?? ""}\u0000${name}`;
}

function isPod(node: RelationNodeView): boolean {
  return node.category === "pod" || node.resourceType.toLowerCase() === "pod";
}

function exactIdentity(
  node: RelationNodeView,
  clusterId: string,
  namespace: string,
  name: string,
): boolean {
  return node.clusterId === clusterId
    && (node.namespace ?? "") === namespace
    && node.name === name;
}

function podIdentities(nodes: Iterable<RelationNodeView>): Set<string> {
  return new Set(
    [...nodes]
      .filter(isPod)
      .map((node) => podHighlightIdentity(node.clusterId, node.namespace, node.name)),
  );
}

/**
 * Resolves only relationships explicitly returned by the relation-topology API.
 * It never falls back to Pod-name prefixes or other inferred naming conventions.
 */
export function resolveHighlightedPodIdentities(
  target: PodHighlightTarget | null,
  nodes: readonly RelationNodeView[],
  edges: readonly RelationEdgeView[],
): Set<string> {
  if (target === null) return new Set();

  if (target.type === "pods") {
    return new Set(
      target.pods.map((pod) => (
        podHighlightIdentity(pod.clusterId, pod.namespace, pod.name)
      )),
    );
  }

  const nodesById = new Map(nodes.map((node) => [node.id, node]));
  const outgoing = new Map<string, RelationEdgeView[]>();
  for (const edge of edges) {
    const current = outgoing.get(edge.from) ?? [];
    current.push(edge);
    outgoing.set(edge.from, current);
  }

  const descendantPods = (seedIds: readonly string[]): Set<string> => {
    const visited = new Set<string>();
    const queue = [...seedIds];
    const matchedPods: RelationNodeView[] = [];

    while (queue.length > 0) {
      const nodeId = queue.shift();
      if (nodeId === undefined || visited.has(nodeId)) continue;
      visited.add(nodeId);
      const node = nodesById.get(nodeId);
      if (node && isPod(node)) {
        matchedPods.push(node);
        continue;
      }
      for (const edge of outgoing.get(nodeId) ?? []) {
        if (edge.kind === "owns" || edge.kind === "selects") queue.push(edge.to);
      }
    }

    return podIdentities(matchedPods);
  };

  if (target.type === "applications") {
    const applicationIds = new Set(target.applicationIds);
    if (applicationIds.size === 0) return new Set();
    const applicationBoundNodeIds = nodes
      .filter((node) => (
        node.clusterId === target.clusterId
        && node.applicationIds.some((applicationId) => applicationIds.has(applicationId))
      ))
      .map((node) => node.id);
    return descendantPods(applicationBoundNodeIds);
  }

  if (target.type === "service") {
    const serviceIds = new Set(
      nodes
        .filter((node) => (
          node.category === "service"
          && exactIdentity(node, target.clusterId, target.namespace, target.name)
        ))
        .map((node) => node.id),
    );
    return podIdentities(
      edges
        .filter((edge) => edge.kind === "selects" && serviceIds.has(edge.from))
        .map((edge) => nodesById.get(edge.to))
        .filter((node): node is RelationNodeView => node !== undefined),
    );
  }

  const workloadIds = nodes
    .filter((node) => target.workloads.some((workload) => (
      node.category === "workload"
      && node.kind.toLowerCase() === workload.kind.toLowerCase()
      && exactIdentity(node, target.clusterId, workload.namespace, workload.name)
    )))
    .map((node) => node.id);
  return descendantPods(workloadIds);
}
