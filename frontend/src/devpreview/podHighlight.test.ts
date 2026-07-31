import { describe, expect, it } from "vitest";

import type {
  RelationEdgeView,
  RelationNodeView,
} from "./relationTopologyFeed";
import {
  podHighlightIdentity,
  resolveHighlightedPodIdentities,
} from "./podHighlight";

function node(
  id: string,
  category: RelationNodeView["category"],
  kind: string,
  namespace: string,
  name: string,
  applicationIds: string[] = [],
): RelationNodeView {
  return {
    id,
    clusterId: "management-server",
    name,
    kind,
    resourceType: kind.toLowerCase(),
    namespace,
    category,
    status: "Running",
    health: "ok",
    applicationIds,
    serviceKey: `management-server/${namespace}/${name}`,
  };
}

function edge(
  id: string,
  from: string,
  to: string,
  kind: RelationEdgeView["kind"],
): RelationEdgeView {
  return { id, from, to, kind };
}

const expectedPod = podHighlightIdentity(
  "management-server",
  "management",
  "api-gateway-7d8f",
);

// 호버 대상과 이름이 비슷한 파드를 혼동하지 않는지 exact identity 계약으로 고정한다.
describe("resolveHighlightedPodIdentities", () => {
  it("uses exact Pod identities resolved from inventory evidence", () => {
    const result = resolveHighlightedPodIdentities(
      {
        type: "pods",
        pods: [{
          clusterId: "management-server",
          namespace: "management",
          name: "api-gateway-7d8f",
        }],
      },
      [],
      [],
    );

    expect([...result]).toEqual([expectedPod]);
  });

  it("highlights only Pods selected by the exact hovered Service", () => {
    const nodes = [
      node("svc", "service", "Service", "management", "api-gateway"),
      node("pod", "pod", "Pod", "management", "api-gateway-7d8f"),
      node("similar", "pod", "Pod", "management", "api-gateway-shadow"),
    ];
    const result = resolveHighlightedPodIdentities(
      {
        type: "service",
        clusterId: "management-server",
        namespace: "management",
        name: "api-gateway",
      },
      nodes,
      [edge("select", "svc", "pod", "selects")],
    );

    expect([...result]).toEqual([expectedPod]);
  });

  it("follows observed workload ownership and selector edges for Config references", () => {
    const nodes = [
      node("deployment", "workload", "Deployment", "management", "api-gateway"),
      node("replicaset", "workload", "ReplicaSet", "management", "api-gateway-7d8f"),
      node("pod", "pod", "Pod", "management", "api-gateway-7d8f"),
    ];
    const result = resolveHighlightedPodIdentities(
      {
        type: "workloads",
        clusterId: "management-server",
        workloads: [{
          kind: "Deployment",
          namespace: "management",
          name: "api-gateway",
        }],
      },
      nodes,
      [
        edge("owns-rs", "deployment", "replicaset", "owns"),
        edge("owns-pod", "replicaset", "pod", "owns"),
      ],
    );

    expect([...result]).toEqual([expectedPod]);
  });

  it("follows ownership edges from an application-bound workload for Repository hover", () => {
    const result = resolveHighlightedPodIdentities(
      {
        type: "applications",
        clusterId: "management-server",
        applicationIds: ["application-1"],
      },
      [
        node(
          "deployment",
          "workload",
          "Deployment",
          "management",
          "api-gateway",
          ["application-1"],
        ),
        node("replicaset", "workload", "ReplicaSet", "management", "api-gateway-7d8f"),
        node("pod", "pod", "Pod", "management", "api-gateway-7d8f"),
        node("other", "pod", "Pod", "management", "other-7d8f", ["application-2"]),
      ],
      [
        edge("owns-rs", "deployment", "replicaset", "owns"),
        edge("owns-pod", "replicaset", "pod", "owns"),
      ],
    );

    expect([...result]).toEqual([expectedPod]);
  });

  it("also supports an application binding attached directly to a Pod", () => {
    const result = resolveHighlightedPodIdentities(
      {
        type: "applications",
        clusterId: "management-server",
        applicationIds: ["application-1"],
      },
      [
        node("pod", "pod", "Pod", "management", "api-gateway-7d8f", ["application-1"]),
      ],
      [],
    );

    expect([...result]).toEqual([expectedPod]);
  });

  it("does not guess a relation from a matching Pod-name prefix", () => {
    const result = resolveHighlightedPodIdentities(
      {
        type: "service",
        clusterId: "management-server",
        namespace: "management",
        name: "api-gateway",
      },
      [
        node("svc", "service", "Service", "management", "api-gateway"),
        node("pod", "pod", "Pod", "management", "api-gateway-7d8f"),
      ],
      [],
    );

    expect(result.size).toBe(0);
  });
});
