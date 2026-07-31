"""Safe Helm chart-source normalization and fail-closed provider selection."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import ipaddress
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from functools import cmp_to_key
from typing import Any
from urllib.parse import parse_qsl, quote, urlencode, urlsplit, urlunsplit

import httpx
import yaml
from yaml.events import AliasEvent
from yaml.nodes import MappingNode, Node

from packages.config.settings import env
from packages.contracts.helm.catalog import (
    HELM_CHART_CATALOG_PAGE_MAX,
    HelmChartCatalogObservation,
    HelmChartDetail,
    HelmChartInstallUnavailable,
    HelmChartSummary,
    HelmChartValuesSchemaUnavailable,
)
from packages.contracts.helm.sources import (
    HELM_CHART_PROVIDER_MAX_CHARTS,
    HELM_CHART_VERSION_PAGE_MAX,
    HelmChartSource,
    HelmChartVersion,
    HelmChartVersionObservation,
    HelmChartVersionResolution,
    HelmRepositoryRefreshResult,
)
from packages.security.outbound_url import (
    HostResolver,
    UnsafeOutboundUrlError,
    resolve_host_addresses,
    validate_outbound_url,
    validate_outbound_url_syntax,
)

INVALID_HELM_CHART_SOURCE = "invalid Helm chart source"
HELM_CHART_PROVIDER_TIMEOUT_SECONDS = 5.0
HELM_CHART_PROVIDER_MAX_RESPONSE_BYTES = 2 * 1024 * 1024
HELM_CHART_PROVIDER_MAX_VERSION_ENTRIES = 5_000
HELM_CHART_PROVIDER_MAX_DOCUMENT_DEPTH = 64
HELM_CHART_PROVIDER_MAX_STRUCTURE_TOKENS = 50_000
HELM_CHART_SOURCE_ALLOWED_HOSTS_ENV = "HELM_CHART_SOURCE_ALLOWED_HOSTS"
HELM_CHART_SOURCE_CREDENTIAL_PROVIDERS = {
    "repository": "helm_repository",
    "oci": "helm_oci",
}
_CHART_NAME = re.compile(r"[A-Za-z0-9](?:[A-Za-z0-9._-]{0,510}[A-Za-z0-9])?")
_SEMVER = re.compile(
    r"^v?(0|[1-9]\d*)\.(0|[1-9]\d*)\.(0|[1-9]\d*)"
    r"(?:-([0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*))?"
    r"(?:\+[0-9A-Za-z-]+(?:\.[0-9A-Za-z-]+)*)?$"
)
_YAML_MERGE_TAG = "tag:yaml.org,2002:merge"


@dataclass(frozen=True)
class HelmProviderCredential:
    kind: str
    token: str | None = None
    username: str | None = None
    password: str | None = None


@dataclass(frozen=True)
class _ProviderResponse:
    status_code: int
    headers: Mapping[str, str]
    content: bytes


@dataclass(frozen=True)
class _PinnedDestination:
    url: str
    host_header: str
    sni_hostname: str


class _HelmProviderFailure(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class HelmRepositoryRefreshError(RuntimeError):
    def __init__(self, reason_code: str) -> None:
        super().__init__(reason_code)
        self.reason_code = reason_code


class _BoundedSafeLoader(yaml.SafeLoader):
    """Reject YAML graph reuse and bound composition and construction work."""

    def __init__(self, stream: bytes) -> None:
        super().__init__(stream)
        self._document_depth = 0
        self._structure_tokens = 0
        self._constructed_nodes = 0

    def compose_node(self, parent: Any, index: Any) -> Any:
        self._document_depth += 1
        self._structure_tokens += 1
        try:
            if (
                self._document_depth > HELM_CHART_PROVIDER_MAX_DOCUMENT_DEPTH
                or self._structure_tokens > HELM_CHART_PROVIDER_MAX_STRUCTURE_TOKENS
            ):
                raise yaml.YAMLError("Helm chart source document exceeds structural limits")
            if self.check_event(AliasEvent):
                raise yaml.YAMLError("Helm chart source aliases are not supported")
            return super().compose_node(parent, index)
        finally:
            self._document_depth -= 1

    def compose_mapping_node(self, anchor: str | None) -> MappingNode:
        node = super().compose_mapping_node(anchor)
        if any(key_node.tag == _YAML_MERGE_TAG for key_node, _value_node in node.value):
            raise yaml.YAMLError("Helm chart source merge keys are not supported")
        return node

    def construct_object(self, node: Node, deep: bool = False) -> Any:
        self._constructed_nodes += 1
        if self._constructed_nodes > HELM_CHART_PROVIDER_MAX_STRUCTURE_TOKENS:
            raise yaml.YAMLError("Helm chart source document exceeds construction limits")
        return super().construct_object(node, deep=deep)


class HelmChartVersionProvider:
    """Bounded repository/OCI metadata reader with no raw-response projection."""

    def __init__(
        self,
        *,
        timeout_seconds: float = HELM_CHART_PROVIDER_TIMEOUT_SECONDS,
        transport: httpx.AsyncBaseTransport | None = None,
        resolver: HostResolver | None = None,
        allowed_hosts: str | None = None,
    ) -> None:
        self.timeout_seconds = timeout_seconds
        self.transport = transport
        self.resolver = resolver or resolve_host_addresses
        self.allowed_hosts = allowed_hosts
        self._repository_index_tasks: dict[
            tuple[str, str, str],
            asyncio.Task[Mapping[Any, Any]],
        ] = {}

    async def fetch_versions(
        self,
        source: HelmChartSource,
        chart_name: str,
        *,
        credential: HelmProviderCredential | None = None,
    ) -> HelmChartVersionObservation:
        observed_at = datetime.now(UTC).isoformat()
        if not _CHART_NAME.fullmatch(chart_name):
            return _unavailable_observation(
                source,
                chart_name,
                observed_at,
                "helm_chart_name_invalid",
            )
        try:
            if source.provider == "repository":
                versions, truncated, reasons = await self._repository_versions(
                    source,
                    chart_name,
                    credential,
                )
            else:
                versions, truncated, reasons = await self._oci_versions(
                    source,
                    chart_name,
                    credential,
                )
        except _HelmProviderFailure as exc:
            return _unavailable_observation(
                source,
                chart_name,
                observed_at,
                exc.reason_code,
            )
        availability = "partial" if reasons else "available"
        return HelmChartVersionObservation(
            source=source,
            chart_name=chart_name,
            availability=availability,
            versions=versions,
            observed_at=observed_at,
            truncated=truncated,
            reason_codes=reasons,
        )

    async def search_catalog(
        self,
        source: HelmChartSource,
        *,
        query: str,
        all_versions: bool,
        limit: int,
        credential: HelmProviderCredential | None = None,
    ) -> HelmChartCatalogObservation:
        """Search one exact registered source without synthesizing chart identities."""

        effective_limit = min(max(int(limit), 1), HELM_CHART_CATALOG_PAGE_MAX)
        normalized_query = query.strip().casefold()
        if len(normalized_query) > 200:
            return _unavailable_catalog(source, "helm_chart_catalog_query_invalid")
        if source.provider == "oci":
            if not normalized_query or _CHART_NAME.fullmatch(query.strip()) is None:
                return _unavailable_catalog(
                    source,
                    "helm_oci_catalog_requires_exact_query",
                )
            return await self._oci_catalog(
                source,
                query.strip(),
                all_versions=all_versions,
                limit=effective_limit,
                credential=credential,
            )
        try:
            entries = await self._repository_entries(source, credential)
        except _HelmProviderFailure as exc:
            return _unavailable_catalog(source, exc.reason_code)
        return await self._repository_catalog(
            source,
            entries,
            query=normalized_query,
            all_versions=all_versions,
            limit=effective_limit,
        )

    async def get_chart_detail(
        self,
        source: HelmChartSource,
        chart_name: str,
        *,
        version: str | None,
        credential: HelmProviderCredential | None = None,
    ) -> HelmChartDetail:
        """Resolve one source/chart/version identity with explicit unavailable content."""

        observation = await self.fetch_versions(
            source,
            chart_name,
            credential=credential,
        )
        unavailable_values = HelmChartValuesSchemaUnavailable(
            reason_code="helm_chart_values_schema_unavailable"
        )
        unavailable_install = HelmChartInstallUnavailable(
            reason_code="helm_chart_install_recipe_unavailable"
        )
        if observation.availability == "unavailable":
            return HelmChartDetail(
                availability="unavailable",
                chart=None,
                versions=(),
                values_schema=unavailable_values,
                install=unavailable_install,
                observed_at=observation.observed_at,
                reason_codes=observation.reason_codes,
            )
        selected = _selected_chart_version(observation.versions, version)
        if selected is None:
            return HelmChartDetail(
                availability="unavailable",
                chart=None,
                versions=(),
                values_schema=unavailable_values,
                install=unavailable_install,
                observed_at=observation.observed_at,
                reason_codes=("helm_chart_source_chart_not_found",),
            )
        description = None
        if source.provider == "repository":
            try:
                entries = await self._repository_entries(source, credential)
                description = _repository_description(entries, chart_name, selected.version)
            except _HelmProviderFailure:
                description = None
        return HelmChartDetail(
            availability=observation.availability,
            chart=HelmChartSummary(
                source=source,
                name=chart_name,
                version=selected.version,
                app_version=selected.app_version,
                description=description,
                deprecated=selected.deprecated,
            ),
            versions=observation.versions,
            values_schema=unavailable_values,
            install=unavailable_install,
            observed_at=observation.observed_at,
            truncated=observation.truncated,
            reason_codes=observation.reason_codes,
        )

    async def _repository_catalog(
        self,
        source: HelmChartSource,
        entries: Mapping[Any, Any],
        *,
        query: str,
        all_versions: bool,
        limit: int,
    ) -> HelmChartCatalogObservation:
        observed_at = datetime.now(UTC).isoformat()
        items: list[HelmChartSummary] = []
        total = 0
        invalid_entries = False
        source_truncated = len(entries) > HELM_CHART_PROVIDER_MAX_CHARTS
        chart_names = sorted(
            str(name)
            for name in tuple(entries.keys())[:HELM_CHART_PROVIDER_MAX_CHARTS]
            if isinstance(name, str) and _CHART_NAME.fullmatch(name)
        )
        for chart_name in chart_names:
            raw_versions = entries.get(chart_name)
            if not _catalog_query_matches(query, chart_name, raw_versions):
                continue
            try:
                versions, versions_truncated, invalid_reasons = await _parse_repository_versions(
                    raw_versions
                )
            except _HelmProviderFailure:
                invalid_entries = True
                continue
            selected_versions = versions if all_versions else versions[:1]
            total += len(selected_versions)
            for selected in selected_versions:
                if len(items) >= limit:
                    continue
                items.append(
                    HelmChartSummary(
                        source=source,
                        name=chart_name,
                        version=selected.version,
                        app_version=selected.app_version,
                        description=_repository_description(
                            entries,
                            chart_name,
                            selected.version,
                        ),
                        deprecated=selected.deprecated,
                    )
                )
            source_truncated = source_truncated or versions_truncated
            invalid_entries = invalid_entries or bool(invalid_reasons)
        truncated = source_truncated or total > len(items)
        reasons: list[str] = []
        if invalid_entries:
            reasons.append("helm_chart_catalog_invalid_entries")
        if truncated:
            reasons.append("helm_chart_catalog_truncated")
        return HelmChartCatalogObservation(
            source=source,
            availability="partial" if reasons else "available",
            items=tuple(items),
            total=total,
            observed_at=observed_at,
            truncated=truncated,
            reason_codes=tuple(reasons),
        )

    async def _oci_catalog(
        self,
        source: HelmChartSource,
        chart_name: str,
        *,
        all_versions: bool,
        limit: int,
        credential: HelmProviderCredential | None,
    ) -> HelmChartCatalogObservation:
        observation = await self.fetch_versions(
            source,
            chart_name,
            credential=credential,
        )
        if observation.availability == "unavailable":
            return HelmChartCatalogObservation(
                source=source,
                availability="unavailable",
                items=(),
                total=0,
                observed_at=observation.observed_at,
                reason_codes=observation.reason_codes,
            )
        selected_versions = observation.versions if all_versions else observation.versions[:1]
        items = tuple(
            HelmChartSummary(
                source=source,
                name=chart_name,
                version=item.version,
                app_version=item.app_version,
                description=None,
                deprecated=item.deprecated,
            )
            for item in selected_versions[:limit]
        )
        truncated = observation.truncated or len(selected_versions) > len(items)
        reasons = set(observation.reason_codes)
        if truncated:
            reasons.add("helm_chart_catalog_truncated")
        return HelmChartCatalogObservation(
            source=source,
            availability="partial" if reasons else "available",
            items=items,
            total=len(selected_versions),
            observed_at=observation.observed_at,
            truncated=truncated,
            reason_codes=tuple(sorted(reasons)),
        )

    async def refresh_repository(
        self,
        source: HelmChartSource,
        *,
        credential: HelmProviderCredential | None = None,
    ) -> HelmRepositoryRefreshResult:
        """Invalidate one exact source cache and fetch its index under existing bounds."""

        if source.provider != "repository" or source.status != "active":
            raise _HelmProviderFailure("helm_chart_source_provider_not_supported")
        headers = _authorization_headers(credential)
        fingerprint = hashlib.sha256(headers.get("Authorization", "").encode("utf-8")).hexdigest()
        self._repository_index_tasks.pop(
            (source.source_id, source.reference, fingerprint),
            None,
        )
        try:
            entries = await self._repository_entries(source, credential)
        except _HelmProviderFailure as exc:
            raise HelmRepositoryRefreshError(exc.reason_code) from exc
        count = sum(
            1
            for name, versions in entries.items()
            if isinstance(name, str) and name.strip() and isinstance(versions, list)
        )
        if count > HELM_CHART_PROVIDER_MAX_CHARTS:
            raise HelmRepositoryRefreshError("helm_chart_source_response_too_large")
        return HelmRepositoryRefreshResult(
            source_id=source.source_id,
            chart_count=count,
            observed_at=datetime.now(UTC).isoformat(),
        )

    async def _repository_versions(
        self,
        source: HelmChartSource,
        chart_name: str,
        credential: HelmProviderCredential | None,
    ) -> tuple[tuple[HelmChartVersion, ...], bool, tuple[str, ...]]:
        entries = await self._repository_entries(source, credential)
        raw_versions = entries.get(chart_name)
        if raw_versions is None:
            raise _HelmProviderFailure("helm_chart_source_chart_not_found")
        return await _parse_repository_versions(raw_versions)

    async def _repository_entries(
        self,
        source: HelmChartSource,
        credential: HelmProviderCredential | None,
    ) -> Mapping[Any, Any]:
        headers = _authorization_headers(credential)
        credential_fingerprint = hashlib.sha256(
            headers.get("Authorization", "").encode("utf-8")
        ).hexdigest()
        key = (source.source_id, source.reference, credential_fingerprint)
        task = self._repository_index_tasks.get(key)
        if task is None:
            task = asyncio.create_task(self._load_repository_entries(source.reference, headers))
            self._repository_index_tasks[key] = task
        return await asyncio.shield(task)

    async def _load_repository_entries(
        self,
        reference: str,
        headers: Mapping[str, str],
    ) -> Mapping[Any, Any]:
        url = f"{reference.rstrip('/')}/index.yaml"
        response = await self._request(url, headers=headers)
        _require_success(response)
        payload = await asyncio.to_thread(_load_bounded_yaml, response.content)
        if not isinstance(payload, Mapping):
            raise _HelmProviderFailure("helm_chart_source_invalid_response")
        entries = payload.get("entries")
        if not isinstance(entries, Mapping):
            raise _HelmProviderFailure("helm_chart_source_invalid_response")
        return entries

    async def _oci_versions(
        self,
        source: HelmChartSource,
        chart_name: str,
        credential: HelmProviderCredential | None,
    ) -> tuple[tuple[HelmChartVersion, ...], bool, tuple[str, ...]]:
        parsed = urlsplit(source.reference)
        repository = "/".join(
            part for part in (parsed.path.strip("/"), quote(chart_name, safe="._-")) if part
        )
        path = f"/v2/{repository}/tags/list"
        query = urlencode({"n": str(HELM_CHART_VERSION_PAGE_MAX + 1)})
        url = urlunsplit(("https", parsed.netloc, path, query, ""))
        response = await self._request(url, headers=_authorization_headers(credential))
        if response.status_code == 401:
            response = await self._retry_oci_bearer_challenge(
                url,
                response,
                credential,
            )
        _require_success(response)
        payload = await asyncio.to_thread(_load_bounded_json, response.content)
        if not isinstance(payload, Mapping):
            raise _HelmProviderFailure("helm_chart_source_invalid_response")
        tags = payload.get("tags")
        if tags is None:
            raise _HelmProviderFailure("helm_chart_source_chart_not_found")
        if not isinstance(tags, list):
            raise _HelmProviderFailure("helm_chart_source_invalid_response")
        source_truncated = len(tags) > HELM_CHART_PROVIDER_MAX_VERSION_ENTRIES
        unique = {
            str(tag).strip()
            for tag in tags[:HELM_CHART_PROVIDER_MAX_VERSION_ENTRIES]
            if isinstance(tag, str)
            and len(str(tag).strip()) <= 256
            and _semver(str(tag).strip()) is not None
        }
        if not unique:
            raise _HelmProviderFailure("helm_chart_source_chart_not_found")
        versions = await asyncio.to_thread(
            _sort_chart_versions,
            [HelmChartVersion(version=version) for version in unique],
        )
        truncated = (
            source_truncated
            or len(versions) > HELM_CHART_VERSION_PAGE_MAX
            or "next" in str(response.headers.get("link", "")).casefold()
        )
        reasons = ("helm_chart_versions_truncated",) if truncated else ()
        return (
            tuple(versions[:HELM_CHART_VERSION_PAGE_MAX]),
            truncated,
            reasons,
        )

    async def _retry_oci_bearer_challenge(
        self,
        url: str,
        response: _ProviderResponse,
        credential: HelmProviderCredential | None,
    ) -> _ProviderResponse:
        challenge = _bearer_challenge(response.headers.get("www-authenticate", ""))
        if challenge is None or (credential is not None and credential.kind != "basic"):
            return response
        realm, params = challenge
        token_query = urlencode(
            [(key, value) for key, value in params.items() if key in {"service", "scope"}]
        )
        token_url = f"{realm}{'&' if '?' in realm else '?'}{token_query}" if token_query else realm
        token_response = await self._request(
            token_url,
            headers=_authorization_headers(credential),
        )
        _require_success(token_response)
        payload = await asyncio.to_thread(_load_bounded_json, token_response.content)
        if not isinstance(payload, Mapping):
            raise _HelmProviderFailure("helm_chart_source_invalid_response")
        token = str(payload.get("token") or payload.get("access_token") or "").strip()
        if not token or len(token) > 16_384:
            raise _HelmProviderFailure("helm_chart_source_invalid_response")
        return await self._request(url, headers={"Authorization": f"Bearer {token}"})

    async def _request(
        self,
        url: str,
        *,
        headers: Mapping[str, str],
    ) -> _ProviderResponse:
        try:
            async with asyncio.timeout(self.timeout_seconds):
                destination = await self._pinned_destination(url)
                request_headers = dict(headers)
                request_headers["Host"] = destination.host_header
                request_headers["Accept-Encoding"] = "identity"
                async with httpx.AsyncClient(
                    timeout=self.timeout_seconds,
                    transport=self.transport,
                    follow_redirects=False,
                    trust_env=False,
                ) as client:
                    async with client.stream(
                        "GET",
                        destination.url,
                        headers=request_headers,
                        extensions={"sni_hostname": destination.sni_hostname},
                    ) as response:
                        safe_headers = {
                            "content-encoding": response.headers.get("content-encoding", ""),
                            "content-length": response.headers.get("content-length", ""),
                            "link": response.headers.get("link", ""),
                            "www-authenticate": response.headers.get("www-authenticate", ""),
                        }
                        if response.status_code >= 300:
                            return _ProviderResponse(
                                status_code=response.status_code,
                                headers=safe_headers,
                                content=b"",
                            )
                        encoding = safe_headers["content-encoding"].strip().casefold()
                        if encoding and encoding != "identity":
                            raise _HelmProviderFailure("helm_chart_source_invalid_response")
                        declared = _content_length(safe_headers["content-length"])
                        if (
                            declared is not None
                            and declared > HELM_CHART_PROVIDER_MAX_RESPONSE_BYTES
                        ):
                            raise _HelmProviderFailure("helm_chart_source_response_too_large")
                        body = bytearray()
                        async for chunk in response.aiter_bytes():
                            if len(chunk) > HELM_CHART_PROVIDER_MAX_RESPONSE_BYTES - len(body):
                                raise _HelmProviderFailure("helm_chart_source_response_too_large")
                            body.extend(chunk)
                        return _ProviderResponse(
                            status_code=response.status_code,
                            headers=safe_headers,
                            content=bytes(body),
                        )
        except TimeoutError as exc:
            raise _HelmProviderFailure("helm_chart_source_timeout") from exc
        except httpx.TimeoutException as exc:
            raise _HelmProviderFailure("helm_chart_source_timeout") from exc
        except httpx.TransportError as exc:
            raise _HelmProviderFailure("helm_chart_source_transport_error") from exc
        except (UnicodeError, httpx.InvalidURL) as exc:
            raise _HelmProviderFailure("helm_chart_source_transport_error") from exc

    async def _pinned_destination(self, url: str) -> _PinnedDestination:
        allowed_hosts = (
            self.allowed_hosts
            if self.allowed_hosts is not None
            else env(HELM_CHART_SOURCE_ALLOWED_HOSTS_ENV, "").strip()
        )
        try:
            hostname = validate_outbound_url_syntax(
                url,
                allowed_hosts=allowed_hosts,
            )
            try:
                literal = ipaddress.ip_address(hostname)
            except ValueError:
                literal = None
            addresses = (
                (str(literal),) if literal is not None else tuple(await self.resolver(hostname))
            )
            if not addresses:
                raise UnsafeOutboundUrlError("unsafe outbound URL")

            async def pinned_resolver(_hostname: str) -> tuple[str, ...]:
                return addresses

            await validate_outbound_url(
                url,
                resolver=pinned_resolver,
                allowed_hosts=allowed_hosts,
            )
            pinned_ip = ipaddress.ip_address(addresses[0])
        except UnsafeOutboundUrlError as exc:
            raise _HelmProviderFailure("helm_chart_source_unsafe_destination") from exc
        except (OSError, ValueError) as exc:
            raise _HelmProviderFailure("helm_chart_source_transport_error") from exc

        parsed = urlsplit(url)
        port = parsed.port
        pinned_netloc = _host_authority(str(pinned_ip), port, include_default_port=False)
        host_header = _host_authority(hostname, port, include_default_port=True)
        return _PinnedDestination(
            url=urlunsplit((parsed.scheme, pinned_netloc, parsed.path, parsed.query, "")),
            host_header=host_header,
            sni_hostname=hostname,
        )


async def _parse_repository_versions(
    raw_versions: object,
) -> tuple[tuple[HelmChartVersion, ...], bool, tuple[str, ...]]:
    if not isinstance(raw_versions, list):
        raise _HelmProviderFailure("helm_chart_source_invalid_response")
    versions: list[HelmChartVersion] = []
    invalid_entries = False
    seen: set[str] = set()
    source_truncated = len(raw_versions) > HELM_CHART_PROVIDER_MAX_VERSION_ENTRIES
    for raw in raw_versions[:HELM_CHART_PROVIDER_MAX_VERSION_ENTRIES]:
        if not isinstance(raw, Mapping):
            invalid_entries = True
            continue
        version = str(raw.get("version") or "").strip()
        if version in seen or _semver(version) is None:
            invalid_entries = True
            continue
        seen.add(version)
        app_version = str(raw.get("appVersion") or "").strip() or None
        if app_version is not None and len(app_version) > 256:
            app_version = None
            invalid_entries = True
        deprecated = raw.get("deprecated", False)
        if not isinstance(deprecated, bool):
            deprecated = False
            invalid_entries = True
        versions.append(
            HelmChartVersion(
                version=version,
                app_version=app_version,
                deprecated=deprecated,
            )
        )
    if not versions:
        reason = (
            "helm_chart_source_chart_not_found"
            if not raw_versions
            else "helm_chart_source_invalid_response"
        )
        raise _HelmProviderFailure(reason)
    ordered = await asyncio.to_thread(_sort_chart_versions, versions)
    truncated = source_truncated or len(ordered) > HELM_CHART_VERSION_PAGE_MAX
    reasons: list[str] = []
    if invalid_entries:
        reasons.append("helm_chart_versions_invalid_entries")
    if truncated:
        reasons.append("helm_chart_versions_truncated")
    return (
        tuple(ordered[:HELM_CHART_VERSION_PAGE_MAX]),
        truncated,
        tuple(reasons),
    )


def _catalog_query_matches(query: str, chart_name: str, raw_versions: object) -> bool:
    if not query:
        return True
    if query in chart_name.casefold():
        return True
    if not isinstance(raw_versions, list):
        return False
    return any(
        query in str(raw.get("description") or "")[:4096].casefold()
        for raw in raw_versions[:HELM_CHART_PROVIDER_MAX_VERSION_ENTRIES]
        if isinstance(raw, Mapping)
    )


def _repository_description(
    entries: Mapping[Any, Any],
    chart_name: str,
    version: str,
) -> str | None:
    raw_versions = entries.get(chart_name)
    if not isinstance(raw_versions, list):
        return None
    for raw in raw_versions[:HELM_CHART_PROVIDER_MAX_VERSION_ENTRIES]:
        if not isinstance(raw, Mapping) or str(raw.get("version") or "").strip() != version:
            continue
        description = str(raw.get("description") or "").strip()
        return description[:4096] or None
    return None


def _selected_chart_version(
    versions: Sequence[HelmChartVersion],
    requested: str | None,
) -> HelmChartVersion | None:
    if not versions:
        return None
    if requested is None or requested == "latest":
        return versions[0]
    normalized = requested.strip().replace("_", "+")
    return next((item for item in versions if item.version == normalized), None)


def _unavailable_catalog(
    source: HelmChartSource,
    reason_code: str,
) -> HelmChartCatalogObservation:
    return HelmChartCatalogObservation(
        source=source,
        availability="unavailable",
        items=(),
        total=0,
        observed_at=datetime.now(UTC).isoformat(),
        reason_codes=(reason_code,),
    )


def _host_authority(
    hostname: str,
    port: int | None,
    *,
    include_default_port: bool,
) -> str:
    try:
        literal = ipaddress.ip_address(hostname)
    except ValueError:
        canonical_host = hostname
    else:
        canonical_host = f"[{literal}]" if literal.version == 6 else str(literal)
    if port is not None and (include_default_port or port != 443):
        return f"{canonical_host}:{port}"
    return canonical_host


def normalize_helm_chart_source_reference(provider: str, reference: str) -> str:
    """Return a secret-free canonical repository/OCI reference."""

    try:
        parsed = urlsplit(reference)
        allowed_hosts = env(HELM_CHART_SOURCE_ALLOWED_HOSTS_ENV, "").strip()
        if parsed.query or parsed.fragment:
            raise ValueError(INVALID_HELM_CHART_SOURCE)
        if provider == "repository":
            hostname = validate_outbound_url_syntax(
                reference,
                allowed_hosts=allowed_hosts,
            )
            path = parsed.path.rstrip("/")
            return urlunsplit(("https", _normalized_netloc(parsed, hostname), path, "", ""))
        if provider == "oci":
            if parsed.scheme != "oci" or not parsed.netloc or not parsed.path.strip("/"):
                raise ValueError(INVALID_HELM_CHART_SOURCE)
            hostname = validate_outbound_url_syntax(
                urlunsplit(("https", parsed.netloc, parsed.path, "", "")),
                allowed_hosts=allowed_hosts,
            )
            path = f"/{parsed.path.strip('/')}"
            return urlunsplit(("oci", _normalized_netloc(parsed, hostname), path, "", ""))
    except (UnsafeOutboundUrlError, ValueError) as exc:
        raise ValueError(INVALID_HELM_CHART_SOURCE) from exc
    raise ValueError(INVALID_HELM_CHART_SOURCE)


def helm_chart_source_id(workspace_id: str, provider: str, canonical_ref: str) -> str:
    digest = hashlib.sha256(f"{workspace_id}|{provider}|{canonical_ref}".encode()).hexdigest()
    return f"helm-source-{digest[:32]}"


def helm_chart_credential_provider(provider: str) -> str:
    try:
        return HELM_CHART_SOURCE_CREDENTIAL_PROVIDERS[provider]
    except KeyError as exc:
        raise ValueError(INVALID_HELM_CHART_SOURCE) from exc


def helm_chart_credential_scope(source_id: str) -> str:
    return f"helm-chart-source/{source_id}"


def helm_chart_source_from_row(row: Mapping[str, Any]) -> HelmChartSource:
    """Project a persistence row without workspace or credential material."""

    return HelmChartSource(
        source_id=str(row["source_id"]),
        provider=str(row["provider"]),
        name=str(row["name"]),
        reference=str(row["canonical_ref"]),
        status=str(row["status"]),
        credentials_configured=bool(row.get("credential_ref")),
        observed_at=_iso_or_none(row.get("updated_at")),
    )


def resolve_helm_chart_versions(
    observations: Sequence[HelmChartVersionObservation],
) -> HelmChartVersionResolution:
    """Select exactly one provider result and never combine version catalogs."""

    if not observations:
        return HelmChartVersionResolution(
            availability="unavailable",
            reason_codes=("helm_chart_source_unavailable",),
        )
    identities = [
        (item.source.source_id, item.source.provider, item.source.reference)
        for item in observations
    ]
    if len(set(identities)) != len(identities):
        return HelmChartVersionResolution(
            availability="unavailable",
            reason_codes=("helm_chart_source_duplicate_observation",),
        )
    if len(observations) != 1:
        return HelmChartVersionResolution(
            availability="unavailable",
            reason_codes=("helm_chart_source_ambiguous",),
        )
    observation = observations[0]
    return HelmChartVersionResolution(
        availability=observation.availability,
        source=observation.source,
        versions=observation.versions,
        observed_at=observation.observed_at,
        truncated=observation.truncated,
        reason_codes=observation.reason_codes,
    )


def resolve_helm_release_versions(
    current_version: str,
    observations: Sequence[HelmChartVersionObservation],
) -> HelmChartVersionResolution:
    """Resolve one release source without unioning same-named chart catalogs."""

    normalized_current = current_version.strip().replace("_", "+")
    if _semver(normalized_current) is None:
        return HelmChartVersionResolution(
            availability="unavailable",
            reason_codes=("helm_release_chart_version_invalid",),
        )
    if not observations:
        return HelmChartVersionResolution(
            availability="unavailable",
            reason_codes=("helm_chart_source_unavailable",),
        )
    identities = tuple(
        (item.source.source_id, item.source.provider, item.source.reference)
        for item in observations
    )
    if len(set(identities)) != len(identities):
        return HelmChartVersionResolution(
            availability="unavailable",
            reason_codes=("helm_chart_source_duplicate_observation",),
        )
    candidates = tuple(item for item in observations if item.versions)
    current_matches = tuple(
        item
        for item in candidates
        if any(
            compare_helm_chart_versions(version.version, normalized_current) == 0
            for version in item.versions
        )
    )
    selected: HelmChartVersionObservation | None = None
    if len(current_matches) == 1:
        selected = current_matches[0]
    elif len(current_matches) > 1:
        return HelmChartVersionResolution(
            availability="unavailable",
            reason_codes=("helm_chart_source_ambiguous",),
        )
    elif candidates:
        return HelmChartVersionResolution(
            availability="unavailable",
            reason_codes=("helm_chart_source_current_version_not_found",),
        )
    if selected is None:
        reasons = tuple(
            sorted(
                {reason for observation in observations for reason in observation.reason_codes}
                or {"helm_chart_source_unavailable"}
            )
        )
        return HelmChartVersionResolution(
            availability="unavailable",
            reason_codes=reasons,
        )
    incomplete_others = tuple(
        item for item in observations if item is not selected and item.availability != "available"
    )
    if incomplete_others:
        return HelmChartVersionResolution(
            availability="unavailable",
            reason_codes=tuple(
                sorted(
                    {
                        "helm_chart_source_observation_incomplete",
                        *(reason for item in incomplete_others for reason in item.reason_codes),
                    }
                )
            ),
        )
    return HelmChartVersionResolution(
        availability=selected.availability,
        source=selected.source,
        versions=selected.versions,
        observed_at=selected.observed_at,
        truncated=selected.truncated,
        reason_codes=selected.reason_codes,
    )


def compare_helm_chart_versions(left: str, right: str) -> int:
    """Compare public chart-version strings using the provider's SemVer rules."""

    return _compare_chart_versions(
        HelmChartVersion(version=left.replace("_", "+")),
        HelmChartVersion(version=right.replace("_", "+")),
    )


def _normalized_netloc(parsed: Any, hostname: str) -> str:
    return f"{hostname}:{parsed.port}" if parsed.port not in {None, 443} else hostname


def _iso_or_none(value: object) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _authorization_headers(
    credential: HelmProviderCredential | None,
) -> dict[str, str]:
    if credential is None:
        return {}
    if credential.kind == "bearer" and credential.token:
        return {"Authorization": f"Bearer {credential.token}"}
    if credential.kind == "basic" and credential.username is not None and credential.password:
        raw = f"{credential.username}:{credential.password}".encode()
        encoded = base64.b64encode(raw).decode("ascii")
        return {"Authorization": f"Basic {encoded}"}
    raise _HelmProviderFailure("helm_chart_source_credential_unavailable")


def _require_success(response: _ProviderResponse) -> None:
    if response.status_code in {401, 403}:
        raise _HelmProviderFailure("helm_chart_source_permission_denied")
    if response.status_code == 404:
        raise _HelmProviderFailure("helm_chart_source_chart_not_found")
    if response.status_code >= 300:
        raise _HelmProviderFailure("helm_chart_source_upstream_error")


def _unavailable_observation(
    source: HelmChartSource,
    chart_name: str,
    observed_at: str,
    reason_code: str,
) -> HelmChartVersionObservation:
    return HelmChartVersionObservation(
        source=source,
        chart_name=chart_name,
        availability="unavailable",
        versions=(),
        observed_at=observed_at,
        reason_codes=(reason_code,),
    )


@dataclass(frozen=True)
class _SemVer:
    major: int
    minor: int
    patch: int
    prerelease: tuple[str, ...]


def _semver(value: str) -> _SemVer | None:
    if not value or len(value) > 256:
        return None
    match = _SEMVER.fullmatch(value)
    if match is None:
        return None
    prerelease = tuple((match.group(4) or "").split(".")) if match.group(4) else ()
    if any(
        identifier.isdigit() and len(identifier) > 1 and identifier.startswith("0")
        for identifier in prerelease
    ):
        return None
    try:
        return _SemVer(
            major=int(match.group(1)),
            minor=int(match.group(2)),
            patch=int(match.group(3)),
            prerelease=prerelease,
        )
    except ValueError:
        return None


def _compare_chart_versions(left: HelmChartVersion, right: HelmChartVersion) -> int:
    left_version = _semver(left.version)
    right_version = _semver(right.version)
    if left_version is None or right_version is None:
        return (left.version > right.version) - (left.version < right.version)
    left_core = (left_version.major, left_version.minor, left_version.patch)
    right_core = (right_version.major, right_version.minor, right_version.patch)
    if left_core != right_core:
        return (left_core > right_core) - (left_core < right_core)
    if not left_version.prerelease and not right_version.prerelease:
        return 0
    if not left_version.prerelease:
        return 1
    if not right_version.prerelease:
        return -1
    for left_item, right_item in zip(
        left_version.prerelease,
        right_version.prerelease,
        strict=False,
    ):
        if left_item == right_item:
            continue
        left_numeric = left_item.isdigit()
        right_numeric = right_item.isdigit()
        if left_numeric and right_numeric:
            return (int(left_item) > int(right_item)) - (int(left_item) < int(right_item))
        if left_numeric != right_numeric:
            return -1 if left_numeric else 1
        return (left_item > right_item) - (left_item < right_item)
    return (len(left_version.prerelease) > len(right_version.prerelease)) - (
        len(left_version.prerelease) < len(right_version.prerelease)
    )


def _sort_chart_versions(
    versions: Sequence[HelmChartVersion],
) -> list[HelmChartVersion]:
    return sorted(
        versions,
        key=cmp_to_key(_compare_chart_versions),
        reverse=True,
    )


def _content_length(raw: str) -> int | None:
    try:
        value = int(raw)
    except (TypeError, ValueError):
        return None
    return max(value, 0)


def _load_bounded_yaml(content: bytes) -> Any:
    try:
        return yaml.load(content, Loader=_BoundedSafeLoader)
    except (UnicodeError, ValueError, RecursionError, yaml.YAMLError) as exc:
        raise _HelmProviderFailure("helm_chart_source_invalid_response") from exc


def _load_bounded_json(content: bytes) -> Any:
    try:
        _validate_json_structure(content)
        return json.loads(content)
    except _HelmProviderFailure:
        raise
    except (UnicodeError, ValueError, RecursionError) as exc:
        raise _HelmProviderFailure("helm_chart_source_invalid_response") from exc


def _validate_json_structure(content: bytes) -> None:
    depth = 0
    structure_tokens = 0
    in_string = False
    escaped = False
    for value in content:
        if in_string:
            if escaped:
                escaped = False
            elif value == ord("\\"):
                escaped = True
            elif value == ord('"'):
                in_string = False
            continue
        if value == ord('"'):
            in_string = True
            continue
        if value in {ord("{"), ord("[")}:
            depth += 1
            structure_tokens += 1
            if depth > HELM_CHART_PROVIDER_MAX_DOCUMENT_DEPTH:
                raise _HelmProviderFailure("helm_chart_source_invalid_response")
        elif value in {ord("}"), ord("]")}:
            depth -= 1
        elif value == ord(","):
            structure_tokens += 1
        if structure_tokens > HELM_CHART_PROVIDER_MAX_STRUCTURE_TOKENS:
            raise _HelmProviderFailure("helm_chart_source_invalid_response")


def _bearer_challenge(raw: str) -> tuple[str, dict[str, str]] | None:
    if not raw.casefold().startswith("bearer "):
        return None
    values: dict[str, str] = {}
    for key, value in parse_qsl(raw[7:].replace(",", "&"), keep_blank_values=False):
        values[key.strip().casefold()] = value.strip().strip('"')
    realm = values.pop("realm", "")
    try:
        parsed = urlsplit(realm)
    except ValueError:
        return None
    if parsed.scheme != "https" or not parsed.netloc or parsed.fragment:
        return None
    return realm, values
