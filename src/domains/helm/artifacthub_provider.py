"""Safe fixed-host ArtifactHub metadata provider."""

from __future__ import annotations

import re
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal
from urllib.parse import quote, urlencode

import httpx

from domains.helm.source_provider import (
    HelmChartVersionProvider,
    HostResolver,
    _HelmProviderFailure,
    _load_bounded_json,
)
from packages.contracts.helm.artifacthub import (
    ARTIFACTHUB_PAGE_MAX,
    ARTIFACTHUB_VERSION_MAX,
    ArtifactHubChart,
    ArtifactHubChartDetail,
    ArtifactHubChartVersion,
    ArtifactHubRepository,
    ArtifactHubSearchPage,
)

ARTIFACTHUB_BASE_URL = "https://artifacthub.io/api/v1"
_IDENTITY = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$")


class ArtifactHubProvider:
    """Project only bounded chart metadata; results never become executable recipes."""

    def __init__(
        self,
        *,
        timeout_seconds: float = 5.0,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: HostResolver | None = None,
    ) -> None:
        self.transport = HelmChartVersionProvider(
            timeout_seconds=timeout_seconds,
            transport=transport,
            resolver=resolver,
            allowed_hosts="artifacthub.io",
        )

    async def search(
        self,
        *,
        query: str,
        offset: int,
        limit: int,
        sort: Literal["relevance", "stars", "last_updated"],
        official: bool,
        verified: bool,
    ) -> ArtifactHubSearchPage:
        normalized_query = query.strip()
        if not normalized_query or len(normalized_query) > 200:
            raise RuntimeError("artifacthub_query_invalid")
        if offset < 0 or not 1 <= limit <= ARTIFACTHUB_PAGE_MAX:
            raise RuntimeError("artifacthub_pagination_invalid")
        params: dict[str, str] = {
            "kind": "0",
            "ts_query_web": normalized_query,
            "offset": str(offset),
            "limit": str(limit),
        }
        if sort != "relevance":
            params["sort"] = sort
        if official:
            params["official"] = "true"
        if verified:
            params["verified_publisher"] = "true"
        payload = await self._json(f"{ARTIFACTHUB_BASE_URL}/packages/search?{urlencode(params)}")
        raw_items = payload.get("packages")
        if not isinstance(raw_items, list):
            raise RuntimeError("artifacthub_invalid_response")
        items: list[ArtifactHubChart] = []
        for raw in raw_items[:limit]:
            if not isinstance(raw, Mapping):
                continue
            try:
                items.append(_chart(raw))
            except (TypeError, ValueError):
                continue
        raw_total = payload.get("total")
        total = raw_total if isinstance(raw_total, int) and raw_total >= 0 else len(items)
        total = max(total, offset + len(items))
        return ArtifactHubSearchPage(
            items=tuple(items),
            total=total,
            offset=offset,
            limit=limit,
            has_more=offset + len(items) < total,
            observed_at=datetime.now(UTC).isoformat(),
        )

    async def chart(
        self,
        repository: str,
        chart: str,
        version: str | None = None,
    ) -> ArtifactHubChartDetail:
        identities = (repository, chart, *((version,) if version is not None else ()))
        if any(not _IDENTITY.fullmatch(item) for item in identities):
            raise RuntimeError("artifacthub_chart_identity_invalid")
        path = "/".join(quote(item, safe="") for item in identities)
        payload = await self._json(f"{ARTIFACTHUB_BASE_URL}/packages/helm/{path}")
        try:
            selected = _chart(payload)
        except (TypeError, ValueError) as exc:
            raise RuntimeError("artifacthub_invalid_response") from exc
        raw_versions = payload.get("available_versions")
        versions: list[ArtifactHubChartVersion] = []
        if raw_versions is not None and not isinstance(raw_versions, list):
            raise RuntimeError("artifacthub_invalid_response")
        for raw in (raw_versions or ())[:ARTIFACTHUB_VERSION_MAX]:
            if not isinstance(raw, Mapping):
                continue
            version_value = _text(raw.get("version"), 256)
            if version_value is None:
                continue
            versions.append(
                ArtifactHubChartVersion(
                    version=version_value,
                    app_version=_text(raw.get("app_version"), 256),
                )
            )
        readme = _text(payload.get("readme"), 262_144)
        return ArtifactHubChartDetail(
            chart=selected,
            readme=readme,
            available_versions=tuple(versions),
            versions_truncated=len(raw_versions or ()) > ARTIFACTHUB_VERSION_MAX,
            observed_at=datetime.now(UTC).isoformat(),
        )

    async def _json(self, url: str) -> Mapping[str, object]:
        try:
            response = await self.transport._request(url, headers={})
        except _HelmProviderFailure as exc:
            raise RuntimeError(exc.reason_code.replace("helm_chart_source", "artifacthub")) from exc
        if response.status_code == 404:
            raise RuntimeError("artifacthub_chart_not_found")
        if response.status_code >= 300:
            raise RuntimeError("artifacthub_upstream_error")
        try:
            payload = _load_bounded_json(response.content)
        except _HelmProviderFailure as exc:
            raise RuntimeError("artifacthub_invalid_response") from exc
        if not isinstance(payload, Mapping):
            raise RuntimeError("artifacthub_invalid_response")
        return payload


def _chart(raw: Mapping[str, object]) -> ArtifactHubChart:
    repository_value = raw.get("repository")
    if not isinstance(repository_value, Mapping):
        raise ValueError("missing ArtifactHub repository")
    package_id = _text(raw.get("package_id"), 253)
    name = _text(raw.get("name"), 253)
    version = _text(raw.get("version"), 256)
    repository_name = _text(repository_value.get("name"), 253)
    repository_url = _text(repository_value.get("url"), 2048)
    if None in {package_id, name, version, repository_name, repository_url}:
        raise ValueError("incomplete ArtifactHub chart")
    stars_value = raw.get("stars")
    stars = stars_value if isinstance(stars_value, int) and stars_value >= 0 else 0
    return ArtifactHubChart(
        package_id=package_id,
        name=name,
        version=version,
        app_version=_text(raw.get("app_version"), 256),
        description=_text(raw.get("description"), 4096),
        stars=stars,
        deprecated=raw.get("deprecated") is True,
        signed=raw.get("signed") is True,
        repository=ArtifactHubRepository(
            name=repository_name,
            url=repository_url,
            official=repository_value.get("official") is True,
            verified_publisher=repository_value.get("verified_publisher") is True,
        ),
    )


def _text(value: object, limit: int) -> str | None:
    if not isinstance(value, str):
        return None
    normalized = value.strip()
    return normalized[:limit] if normalized else None
