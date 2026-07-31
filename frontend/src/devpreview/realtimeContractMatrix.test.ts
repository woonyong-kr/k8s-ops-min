import { describe, expect, it } from "vitest";

import {
  DYNAMIC_API_ROUTE_EXPANSIONS,
  REALTIME_CONTRACT_MATRIX,
  SHELL_API_MODULES,
  SHELL_DATA_CONTRACTS,
  SURFACE_API_MODULES,
  SURFACE_IDS,
} from "./realtimeContractMatrix";
import { CHECKS_REFRESH_MS } from "./checksFeed";
import { CONNECTION_STATUS_POLL_MS } from "./connectFeed";
import {
  DEPLOY_LIST_POLL_MS,
} from "./deployFeed";
import {
  APPLICATION_DETAIL_POLL_MS,
  HELM_DETAIL_MAX_POLL_MS,
} from "./deployDetailFeed";
import { PHYSICAL_TOPOLOGY_RECONCILE_MS } from "./inventoryTopologyFeed";
import { CLUSTER_SUMMARY_FALLBACK_MS } from "./clusterSummaryFeed";
import { RESOURCE_MANIFEST_COMMAND_POLL_MS } from "./resourceManifestEditor";
import { RCA_DETAIL_REFRESH_MS } from "./rcaDetailFeed";
import { RCA_ISSUES_REFRESH_MS } from "./rcaIssuesFeed";
import { TRAFFIC_REFRESH_MS } from "./trafficTelemetryFeed";

function maximumRefreshMs(id: string): number | null {
  const matches = REALTIME_CONTRACT_MATRIX.flatMap(({ feeds }) => feeds)
    .filter((feed) => feed.id === id);
  expect(matches, id).toHaveLength(1);
  return matches[0]?.maximumRefreshMs ?? null;
}

describe("realtime contract matrix", () => {
  it("maps every top-level product surface exactly once", () => {
    expect(REALTIME_CONTRACT_MATRIX.map(({ id }) => id)).toEqual(SURFACE_IDS);
    expect(new Set(REALTIME_CONTRACT_MATRIX.map(({ id }) => id)).size).toBe(
      SURFACE_IDS.length,
    );
  });

  it("declares a canonical source, authorization, fields, and fallback for every feed", () => {
    const feeds = [
      ...SHELL_DATA_CONTRACTS.map((feed) => ({ surface: "shell", ...feed })),
      ...REALTIME_CONTRACT_MATRIX.flatMap((surface) =>
        surface.feeds.map((feed) => ({ surface: surface.id, ...feed }))),
    ];
    expect(feeds.length).toBeGreaterThanOrEqual(60);
    expect(new Set(feeds.map(({ id }) => id)).size).toBe(feeds.length);
    for (const feed of feeds) {
      expect(feed.id, `${feed.surface} feed id`).toMatch(/^[a-z0-9-]+$/u);
      expect(feed.endpoint, `${feed.surface}/${feed.id} endpoint`).toMatch(/^\/[a-z0-9{}/_-]+$/u);
      expect(feed.fieldGroups.length, `${feed.surface}/${feed.id} fields`).toBeGreaterThan(0);
      if (feed.transport === "bounded-poll") {
        expect(feed.maximumRefreshMs, `${feed.surface}/${feed.id} poll bound`).toBeGreaterThanOrEqual(1_000);
        expect(feed.maximumRefreshMs, `${feed.surface}/${feed.id} poll bound`).toBeLessThanOrEqual(60_000);
      } else {
        expect(feed.maximumRefreshMs === null || feed.transport === "sse-snapshot").toBe(true);
      }
    }
  });

  it("assigns every runtime API module to exactly one or more owned surfaces", () => {
    expect(Object.keys(SURFACE_API_MODULES)).toEqual(SURFACE_IDS);
    const modules = [...SHELL_API_MODULES, ...Object.values(SURFACE_API_MODULES).flat()];
    expect(modules).not.toContain("client");
    expect(modules.some((module) => module.endsWith("-schemas"))).toBe(false);
    expect(new Set(modules).size).toBeGreaterThanOrEqual(40);
  });

  it("expands every dynamic adapter route only to declared concrete feeds", () => {
    const endpoints = new Set([
      ...SHELL_DATA_CONTRACTS.map(({ endpoint }) => endpoint),
      ...REALTIME_CONTRACT_MATRIX.flatMap(({ feeds }) => feeds.map(({ endpoint }) => endpoint)),
    ]);
    for (const [template, expansions] of Object.entries(DYNAMIC_API_ROUTE_EXPANSIONS)) {
      expect(template, "dynamic route template").toMatch(/^\/[a-z0-9{}/_-]+$/u);
      expect(expansions.length, template).toBeGreaterThan(0);
      expect(new Set(expansions).size, template).toBe(expansions.length);
      for (const endpoint of expansions) expect(endpoints.has(endpoint), endpoint).toBe(true);
    }
  });

  it("requires every continuously changing surface to have push or a bounded fallback", () => {
    const continuouslyChanging = new Set([
      "fleet-summary",
      "home-issues",
      "home-applications",
      "home-timeline",
      "home-cluster-node-summary",
      "resource-live",
      "resource-node-summary",
      "physical-topology",
      "traffic",
      "cluster-registration",
      "repository-registration",
      "applications",
      "workflow-runs",
      "helm-releases",
      "rca-issues",
      "rca-detail",
      "timeline-events",
      "checks-overview",
      "alert-events",
    ]);
    const mapped = REALTIME_CONTRACT_MATRIX.flatMap(({ feeds }) => feeds);
    for (const id of continuouslyChanging) {
      const feed = mapped.find((candidate) => candidate.id === id);
      expect(feed, id).toBeDefined();
      expect(
        feed?.transport.startsWith("sse")
        || feed?.transport.startsWith("websocket")
        || feed?.transport === "bounded-poll",
        `${id} must not degrade to a one-shot read`,
      ).toBe(true);
    }
  });

  it("keeps documented poll bounds equal to the executable feed constants", () => {
    expect(maximumRefreshMs("home-issues")).toBe(RCA_ISSUES_REFRESH_MS);
    expect(maximumRefreshMs("traffic")).toBe(TRAFFIC_REFRESH_MS);
    expect(maximumRefreshMs("cluster-registration")).toBe(CONNECTION_STATUS_POLL_MS);
    expect(maximumRefreshMs("repository-registration")).toBe(CONNECTION_STATUS_POLL_MS);
    expect(maximumRefreshMs("applications")).toBe(DEPLOY_LIST_POLL_MS);
    expect(maximumRefreshMs("resource-repositories")).toBe(DEPLOY_LIST_POLL_MS);
    expect(maximumRefreshMs("workflow-runs")).toBe(DEPLOY_LIST_POLL_MS);
    expect(maximumRefreshMs("rca-detail")).toBe(RCA_DETAIL_REFRESH_MS);
    expect(maximumRefreshMs("physical-topology")).toBe(PHYSICAL_TOPOLOGY_RECONCILE_MS);
    expect(maximumRefreshMs("checks-overview")).toBe(CHECKS_REFRESH_MS);
    expect(maximumRefreshMs("home-cluster-node-summary")).toBe(CLUSTER_SUMMARY_FALLBACK_MS);
    expect(maximumRefreshMs("resource-node-summary")).toBe(CLUSTER_SUMMARY_FALLBACK_MS);
    expect(maximumRefreshMs("application-detail")).toBe(APPLICATION_DETAIL_POLL_MS);
    expect(maximumRefreshMs("application-deployments")).toBe(APPLICATION_DETAIL_POLL_MS);
    expect(maximumRefreshMs("application-gitops-detail")).toBe(APPLICATION_DETAIL_POLL_MS);
    expect(maximumRefreshMs("application-drift")).toBe(APPLICATION_DETAIL_POLL_MS);
    expect(maximumRefreshMs("application-change-events")).toBe(APPLICATION_DETAIL_POLL_MS);
    expect(maximumRefreshMs("helm-release-detail")).toBe(HELM_DETAIL_MAX_POLL_MS);
    expect(maximumRefreshMs("resource-command-status")).toBe(RESOURCE_MANIFEST_COMMAND_POLL_MS);
  });
});
