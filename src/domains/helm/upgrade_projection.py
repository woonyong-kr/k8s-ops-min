"""Pure Helm release upgrade projections from authorized provider evidence."""

from __future__ import annotations

from domains.helm.source_provider import compare_helm_chart_versions
from packages.config.refresh_policies import integral_refresh_after_seconds
from packages.contracts.helm.releases import (
    HelmReleaseUpgradeInfo,
    HelmReleaseVersionList,
)
from packages.contracts.helm.sources import HelmChartVersionResolution


def helm_release_upgrade_info(
    *,
    chart_name: str | None,
    current_version: str | None,
    resolution: HelmChartVersionResolution,
) -> HelmReleaseUpgradeInfo:
    if (
        resolution.availability == "unavailable"
        or resolution.source is None
        or not resolution.versions
        or not chart_name
        or not current_version
    ):
        return HelmReleaseUpgradeInfo(
            availability="unavailable",
            chart_name=chart_name,
            current_version=current_version,
            reason_codes=resolution.reason_codes or ("helm_chart_identity_unavailable",),
            refresh_after_seconds=integral_refresh_after_seconds("helm_detail"),
        )
    latest = resolution.versions[0].version
    return HelmReleaseUpgradeInfo(
        availability=resolution.availability,
        chart_name=chart_name,
        current_version=current_version,
        latest_version=latest,
        update_available=compare_helm_chart_versions(latest, current_version) > 0,
        source=resolution.source,
        observed_at=resolution.observed_at,
        reason_codes=resolution.reason_codes,
        refresh_after_seconds=integral_refresh_after_seconds("helm_detail"),
    )


def helm_release_version_list(
    *,
    chart_name: str | None,
    current_version: str | None,
    resolution: HelmChartVersionResolution,
) -> HelmReleaseVersionList:
    if (
        resolution.availability == "unavailable"
        or resolution.source is None
        or not resolution.versions
        or not chart_name
        or not current_version
    ):
        return HelmReleaseVersionList(
            availability="unavailable",
            chart_name=chart_name,
            current_version=current_version,
            reason_codes=resolution.reason_codes or ("helm_chart_identity_unavailable",),
            refresh_after_seconds=integral_refresh_after_seconds("helm_detail"),
        )
    return HelmReleaseVersionList(
        availability=resolution.availability,
        chart_name=chart_name,
        current_version=current_version,
        source=resolution.source,
        versions=resolution.versions,
        observed_at=resolution.observed_at,
        truncated=resolution.truncated,
        reason_codes=resolution.reason_codes,
        refresh_after_seconds=integral_refresh_after_seconds("helm_detail"),
    )
