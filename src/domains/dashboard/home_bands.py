"""Home band compositions over existing evidence-first domain contracts."""

from __future__ import annotations

from collections import Counter
from collections.abc import Mapping
from typing import Any

from packages.contracts.checks.observations import ChecksOverviewResponse
from packages.contracts.cost.observations import CostOverviewResponse
from packages.contracts.gateway.responses import (
    HomeAuditFindingSummary,
    HomeExploreSummary,
    HomeGitOpsControllerSummary,
    HomeInsightCoverage,
    HomeNetworkPolicyCoverageSummary,
    HomePostureSummary,
    HomeProviderAvailabilitySummary,
    HomeTopologyPreviewSummary,
)
from packages.contracts.gitops.overview import GitOpsOverviewResponse
from packages.contracts.traffic.observations import TrafficOverviewResponse

TOPOLOGY_PROJECTION_UNAVAILABLE = "topology_projection_unavailable"
NETWORK_POLICY_COVERAGE_UNAVAILABLE = "network_policy_coverage_not_reported"


def compose_home_topology_preview(
    graph: Mapping[str, Any] | None,
    *,
    observed_at: str | None,
) -> HomeTopologyPreviewSummary:
    """Project bounded Resources graph counts without copying the graph reducer."""

    relation_completeness = str((graph or {}).get("relation_completeness") or "unavailable")
    reasons = _reason_codes((graph or {}).get("partial_reason_codes"))
    if relation_completeness == "unavailable":
        return HomeTopologyPreviewSummary(
            coverage=HomeInsightCoverage(
                availability="unavailable",
                observed_at=observed_at,
                reason_codes=reasons or (TOPOLOGY_PROJECTION_UNAVAILABLE,),
            ),
            relation_completeness="unavailable",
        )
    if relation_completeness not in {"exact", "partial"}:
        raise ValueError("Home topology relation completeness is invalid")
    availability = "partial" if relation_completeness == "partial" else "available"
    if availability == "partial" and not reasons:
        reasons = ("topology_relation_incomplete",)
    return HomeTopologyPreviewSummary(
        coverage=HomeInsightCoverage(
            availability=availability,
            observed_at=observed_at,
            reason_codes=reasons,
        ),
        node_count=_non_negative_count(graph, "node_count"),
        edge_count=_non_negative_count(graph, "edge_count"),
        omitted_node_count=_non_negative_count(graph, "omitted_node_count"),
        omitted_edge_count=_non_negative_count(graph, "omitted_edge_count"),
        relation_completeness=relation_completeness,
    )


def compose_home_explore_summary(
    *,
    traffic: TrafficOverviewResponse,
    cost: CostOverviewResponse,
) -> HomeExploreSummary:
    """Reuse Traffic and Cost availability; absent collectors never become zero cards."""

    return HomeExploreSummary(
        traffic=HomeProviderAvailabilitySummary(
            coverage=HomeInsightCoverage(
                availability=traffic.observation.availability,
                observed_at=traffic.observation.observed_at,
                reason_codes=traffic.observation.reason_codes,
            )
        ),
        cost=HomeProviderAvailabilitySummary(
            coverage=HomeInsightCoverage(
                availability=cost.observation.availability,
                observed_at=cost.observation.observed_at,
                reason_codes=cost.observation.reason_codes,
            )
        ),
    )


def compose_home_posture_summary(
    *,
    checks: ChecksOverviewResponse,
    gitops: GitOpsOverviewResponse,
) -> HomePostureSummary:
    """Reuse outbound-Agent Checks and immutable GitOps inventory projections."""

    return HomePostureSummary(
        network_policy=HomeNetworkPolicyCoverageSummary(
            coverage=HomeInsightCoverage(
                availability="unavailable",
                reason_codes=(NETWORK_POLICY_COVERAGE_UNAVAILABLE,),
            )
        ),
        gitops=_gitops_summary(gitops),
        audit=_audit_summary(checks),
    )


def _gitops_summary(response: GitOpsOverviewResponse) -> HomeGitOpsControllerSummary:
    controller_rows = tuple(row for row in response.items if row.authority == "controller")
    coverage_state = response.coverage.state
    if coverage_state == "unavailable":
        return HomeGitOpsControllerSummary(
            coverage=HomeInsightCoverage(
                availability="unavailable",
                observed_at=response.observed_at,
                reason_codes=response.coverage.reason_codes,
            )
        )
    availability = "partial" if coverage_state == "partial" else "available"
    return HomeGitOpsControllerSummary(
        coverage=HomeInsightCoverage(
            availability=availability,
            observed_at=response.observed_at,
            reason_codes=response.coverage.reason_codes,
        ),
        controller_count=len(controller_rows),
        provider_counts=dict(Counter(row.provider for row in controller_rows)),
        health_counts=dict(
            Counter(row.health for row in controller_rows if row.health is not None)
        ),
    )


def _audit_summary(response: ChecksOverviewResponse) -> HomeAuditFindingSummary:
    result = response.result_set
    if result.availability == "unavailable":
        return HomeAuditFindingSummary(
            coverage=HomeInsightCoverage(
                availability="unavailable",
                observed_at=response.scope_coverage.observed_at,
                reason_codes=result.reason_codes,
            )
        )
    findings = result.checks
    return HomeAuditFindingSummary(
        coverage=HomeInsightCoverage(
            availability=result.availability,
            observed_at=result.evaluated_at,
            reason_codes=result.reason_codes,
        ),
        total_check_count=result.total_check_count,
        total_finding_count=result.total_finding_count,
        severity_counts=dict(Counter(finding.severity for finding in findings)),
    )


def _reason_codes(value: object) -> tuple[str, ...]:
    if not isinstance(value, (list, tuple, set, frozenset)):
        return ()
    return tuple(sorted({str(item).strip() for item in value if str(item).strip()}))


def _non_negative_count(source: Mapping[str, Any], key: str) -> int:
    value = source.get(key)
    if isinstance(value, bool) or not isinstance(value, int) or value < 0:
        raise ValueError(f"Home topology {key} is invalid")
    return value
