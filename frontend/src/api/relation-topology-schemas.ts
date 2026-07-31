import { z } from "zod";

import {
  filterCountCompletenessSchema,
  filterResultCountsSchema,
  filterSnapshotMetaSchema,
  inventoryResourceClusterIdentitySchema,
  rfc3339TimestampSchema,
} from "./resource-filter-schemas";

const nullableTimestampSchema = rfc3339TimestampSchema.nullable();
const relationKinds = ["owns", "runs_on", "selects", "routes_to"] as const;

export const relationTopologyNodeSchema = z.strictObject({
  node_id: z.string().min(1),
  category: z.enum([
    "workload",
    "pod",
    "node",
    "service",
    "endpoint",
    "event",
    "other",
  ]),
  identity: z.strictObject({
    version: z.literal("v1"),
    cluster_id: z.string().min(1),
    resource_type: z.string().min(1),
    api_version: z.string(),
    kind: z.string().min(1),
    namespace: z.string().min(1).nullable(),
    name: z.string().min(1),
    uid: z.string().min(1).nullable(),
  }),
  status: z.string(),
  health: z.string(),
  observed_at: nullableTimestampSchema,
  deleted_at: nullableTimestampSchema,
  application_ids: z.array(z.string().min(1)),
  application_binding_completeness: filterCountCompletenessSchema,
}).superRefine((node, context) => {
  if (new Set(node.application_ids).size === node.application_ids.length) return;
  context.addIssue({
    code: "custom",
    message: "topology node application identities must be unique",
    path: ["application_ids"],
  });
});

export const relationTopologyEdgeSchema = z.strictObject({
  edge_id: z.string().min(1),
  from_node_id: z.string().min(1),
  to_node_id: z.string().min(1),
  kind: z.enum(relationKinds),
  plane: z.enum([
    "ownership",
    "placement",
    "network_configured",
    "network_effective",
  ]),
  direction: z.literal("directed"),
  state: z.enum(["active", "historical"]),
  evidence: z.strictObject({
    type: z.enum([
      "owner_reference",
      "node_assignment",
      "selector_match",
      "service_name_label",
    ]),
    authority: z.enum(["authoritative", "derived"]),
    observed_at: nullableTimestampSchema,
  }),
}).superRefine((edge, context) => {
  const expected = {
    owns: { plane: "ownership", type: "owner_reference", authority: "authoritative" },
    runs_on: { plane: "placement", type: "node_assignment", authority: "authoritative" },
    selects: { plane: null, type: "selector_match", authority: "derived" },
    routes_to: {
      plane: "network_effective",
      type: "service_name_label",
      authority: "authoritative",
    },
  } as const;
  const requirement = expected[edge.kind];
  if (edge.kind === "selects") {
    if (["ownership", "network_configured"].includes(edge.plane)) return;
    context.addIssue({
      code: "custom",
      message: "selector topology relation plane is invalid",
      path: ["plane"],
    });
    return;
  }
  if (edge.plane === requirement.plane && edge.evidence.type === requirement.type &&
      edge.evidence.authority === requirement.authority) return;
  context.addIssue({
    code: "custom",
    message: "topology relation evidence does not match its kind",
  });
});

export const relationTopologySchema = z.strictObject({
  view: z.literal("relations"),
  availability: z.enum(["available", "unavailable"]),
  refresh_after_seconds: z.number().int().min(1).max(60),
  graph_revision: z.string().min(1),
  cluster_projection_revision: z.number().int().nonnegative(),
  cluster: inventoryResourceClusterIdentitySchema,
  nodes: z.array(relationTopologyNodeSchema).max(200),
  edges: z.array(relationTopologyEdgeSchema).max(1_000),
  root_node_ids: z.array(z.string().min(1)),
  counts: filterResultCountsSchema,
  node_count: z.number().int().nonnegative(),
  edge_count: z.number().int().nonnegative(),
  omitted_node_count: z.number().int().nonnegative(),
  omitted_edge_count: z.number().int().nonnegative(),
  node_limit: z.number().int().min(1).max(200),
  edge_limit: z.number().int().min(1).max(2_000),
  truncated: z.boolean(),
  relation_completeness: filterCountCompletenessSchema,
  partial_reason_codes: z.array(z.string().min(1)),
  snapshot: filterSnapshotMetaSchema,
}).superRefine((topology, context) => {
  const nodeIds = new Set<string>();
  topology.nodes.forEach((node, index) => {
    if (node.identity.cluster_id !== topology.cluster.cluster_id) {
      context.addIssue({
        code: "custom",
        message: "topology nodes must belong to the scoped cluster",
        path: ["nodes", index, "identity", "cluster_id"],
      });
    }
    if (nodeIds.has(node.node_id)) {
      context.addIssue({
        code: "custom",
        message: "topology node identities must be unique",
        path: ["nodes", index, "node_id"],
      });
    }
    nodeIds.add(node.node_id);
  });
  const edgeIds = new Set<string>();
  topology.edges.forEach((edge, index) => {
    if (edgeIds.has(edge.edge_id)) {
      context.addIssue({
        code: "custom",
        message: "topology edge identities must be unique",
        path: ["edges", index, "edge_id"],
      });
    }
    if (!nodeIds.has(edge.from_node_id) || !nodeIds.has(edge.to_node_id)) {
      context.addIssue({
        code: "custom",
        message: "topology edge endpoints must reference returned nodes",
        path: ["edges", index],
      });
    }
    edgeIds.add(edge.edge_id);
  });
  if (topology.node_count !== topology.nodes.length || topology.edge_count !== topology.edges.length) {
    context.addIssue({
      code: "custom",
      message: "topology counts must match returned graph records",
    });
  }
  if (topology.nodes.length > topology.node_limit || topology.edges.length > topology.edge_limit) {
    context.addIssue({
      code: "custom",
      message: "topology records must respect their budgets",
    });
  }
  if (topology.root_node_ids.some((nodeId) => !nodeIds.has(nodeId))) {
    context.addIssue({
      code: "custom",
      message: "topology roots must reference returned nodes",
      path: ["root_node_ids"],
    });
  }
  if ((topology.omitted_node_count > 0 || topology.omitted_edge_count > 0) && !topology.truncated) {
    context.addIssue({
      code: "custom",
      message: "omitted topology records require truncated=true",
      path: ["truncated"],
    });
  }
  if (topology.relation_completeness === "exact" && topology.partial_reason_codes.length > 0) {
    context.addIssue({
      code: "custom",
      message: "exact topology cannot carry partial reasons",
      path: ["partial_reason_codes"],
    });
  }
  if (topology.relation_completeness === "partial" && topology.partial_reason_codes.length === 0) {
    context.addIssue({
      code: "custom",
      message: "partial topology requires evidence reasons",
      path: ["partial_reason_codes"],
    });
  }
  const unavailable = topology.availability === "unavailable";
  if (unavailable && (
    topology.relation_completeness !== "unavailable" ||
    topology.nodes.length > 0 ||
    topology.edges.length > 0 ||
    topology.root_node_ids.length > 0 ||
    topology.counts.filtered_count !== null ||
    topology.counts.unfiltered_count !== null ||
    topology.counts.filtered_count_completeness !== "unavailable" ||
    topology.counts.unfiltered_count_completeness !== "unavailable"
  )) {
    context.addIssue({
      code: "custom",
      message: "unavailable topology cannot claim graph evidence",
    });
  }
  if (!unavailable && topology.relation_completeness === "unavailable") {
    context.addIssue({
      code: "custom",
      message: "available topology requires an evidence completeness state",
    });
  }
});

export type RelationTopologyEndpoint = z.infer<typeof relationTopologySchema>;
export type RelationTopologyEndpointNode = z.infer<typeof relationTopologyNodeSchema>;
export type RelationTopologyEndpointEdge = z.infer<typeof relationTopologyEdgeSchema>;
