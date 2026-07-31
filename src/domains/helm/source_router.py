"""Workspace RBAC and external-provider boundary for Helm chart sources."""

from __future__ import annotations

import asyncio
import json
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from fastapi.exceptions import RequestValidationError
from fastapi.routing import APIRoute
from starlette.requests import Request
from starlette.responses import JSONResponse, Response

from domains.catalog.install import helm_upgrade_target, matching_helm_recipe
from domains.helm.artifacthub_provider import ArtifactHubProvider
from domains.helm.events import HelmChartSourceDeletedBody, HelmChartSourceRefreshedBody
from domains.helm.repository import (
    HelmChartSourceConflict,
    HelmChartSourceIdentityConflict,
    HelmChartSourceNotFound,
)
from domains.helm.source_provider import (
    HelmChartVersionProvider,
    HelmProviderCredential,
    HelmRepositoryRefreshError,
    helm_chart_credential_provider,
    helm_chart_credential_scope,
    helm_chart_source_from_row,
    helm_chart_source_id,
    normalize_helm_chart_source_reference,
)
from domains.identity.dependencies import (
    require_admin_session,
    require_resource_access,
    require_session,
)
from packages.contracts.auth import Actor
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.responses import AcceptedResponse
from packages.contracts.helm.artifacthub import (
    ARTIFACTHUB_PAGE_MAX,
    ArtifactHubChartDetail,
    ArtifactHubSearchPage,
)
from packages.contracts.helm.catalog import (
    HELM_CHART_CATALOG_PAGE_MAX,
    HELM_CHART_CATALOG_TOTAL_MAX,
    HelmChartCatalogObservation,
    HelmChartCatalogPage,
    HelmChartDetail,
    HelmChartInstallAvailable,
    HelmChartInstallUnavailable,
    HelmChartValuesSchemaAvailable,
    HelmChartValuesSchemaUnavailable,
)
from packages.contracts.helm.sources import (
    HELM_CHART_SOURCE_PAGE_MAX,
    HelmChartSource,
    HelmChartSourceCredentialInput,
    HelmChartSourceDeleteRequest,
    HelmChartSourcePage,
    HelmChartSourceRegisterRequest,
    HelmChartVersionObservation,
    HelmRepositoryRefreshAccepted,
)
from packages.contracts.identity import (
    DEFAULT_WORKSPACE_ID,
    AccessResourceType,
    Permission,
    ServiceRole,
)
from packages.events.context import event_workspace
from packages.runtime.dependencies import get_db, get_events
from packages.security.credentials import (
    CredentialEncryptionError,
    credential_ref,
    decrypt_credential,
    encrypt_credential,
    parse_credential_ref,
)
from packages.storage.engine import unit_of_work_or_null


class RedactedValidationRoute(APIRoute):
    """Keep validation structure while removing all rejected request values."""

    def get_route_handler(self):
        original_handler = super().get_route_handler()

        async def redacted_handler(request: Request) -> Response:
            try:
                return await original_handler(request)
            except RequestValidationError as exc:
                errors = [
                    {key: value for key, value in error.items() if key in {"type", "loc", "msg"}}
                    for error in exc.errors()
                ]
                return JSONResponse(status_code=422, content={"detail": errors})

        return redacted_handler


router = APIRouter(route_class=RedactedValidationRoute)

HELM_CHART_SOURCE_RESOURCE_TYPE = AccessResourceType.HELM_CHART_SOURCE.value
HELM_CHART_SOURCE_NOT_FOUND = "Helm chart source not found"
HELM_CHART_SOURCE_CONFLICT = "Helm chart source already exists"
HELM_CHART_CREDENTIAL_UNAVAILABLE = "helm_chart_source_credential_unavailable"
_artifacthub_provider = ArtifactHubProvider()


def get_helm_chart_version_provider() -> HelmChartVersionProvider:
    return HelmChartVersionProvider()


def get_artifacthub_provider() -> ArtifactHubProvider:
    return _artifacthub_provider


@router.get(
    gateway_routes.HELM_CHARTS_PATH,
    response_model=HelmChartCatalogPage,
)
async def search_helm_charts(
    query: str = Query(default="", max_length=200),
    source_id: str | None = Query(
        default=None,
        min_length=1,
        max_length=80,
        pattern=r"^[a-z0-9-]+$",
    ),
    provider_filter: str | None = Query(
        default=None,
        alias="provider",
        pattern=r"^(repository|oci)$",
    ),
    all_versions: bool = Query(default=False, alias="allVersions"),
    limit: int = Query(default=20, ge=1, le=HELM_CHART_CATALOG_PAGE_MAX),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    provider: HelmChartVersionProvider = Depends(get_helm_chart_version_provider),
) -> HelmChartCatalogPage:
    workspace_id = _workspace_id(current)
    rows, source_scope_truncated = await _authorized_chart_source_rows(
        db,
        current,
        workspace_id,
        source_id,
    )
    if provider_filter is not None:
        rows = tuple(row for row in rows if str(row.get("provider") or "") == provider_filter)

    semaphore = asyncio.Semaphore(8)

    async def observe(row: dict[str, Any]) -> HelmChartCatalogObservation:
        source = helm_chart_source_from_row(row)
        try:
            credential = await asyncio.to_thread(
                load_helm_provider_credential,
                db,
                workspace_id,
                row,
            )
        except CredentialEncryptionError:
            return HelmChartCatalogObservation(
                source=source,
                availability="unavailable",
                items=(),
                total=0,
                reason_codes=(HELM_CHART_CREDENTIAL_UNAVAILABLE,),
            )
        async with semaphore:
            return await provider.search_catalog(
                source,
                query=query.strip(),
                all_versions=all_versions,
                limit=limit,
                credential=credential,
            )

    observations = tuple(await asyncio.gather(*(observe(dict(row)) for row in rows)))
    return _chart_catalog_page(
        observations,
        query=query.strip(),
        source_id=source_id,
        provider_filter=provider_filter,
        all_versions=all_versions,
        limit=limit,
        source_scope_truncated=source_scope_truncated,
    )


@router.get(
    gateway_routes.HELM_CHART_PATH,
    response_model=HelmChartDetail,
)
async def get_helm_chart_detail(
    source_id: str = Path(min_length=1, max_length=80, pattern=r"^[a-z0-9-]+$"),
    chart_name: str = Path(
        min_length=1,
        max_length=512,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$",
    ),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    provider: HelmChartVersionProvider = Depends(get_helm_chart_version_provider),
) -> HelmChartDetail:
    return await _get_helm_chart_detail(
        source_id,
        chart_name,
        None,
        current=current,
        db=db,
        provider=provider,
    )


@router.get(
    gateway_routes.HELM_CHART_VERSION_PATH,
    response_model=HelmChartDetail,
)
async def get_helm_chart_version_detail(
    source_id: str = Path(min_length=1, max_length=80, pattern=r"^[a-z0-9-]+$"),
    chart_name: str = Path(
        min_length=1,
        max_length=512,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$",
    ),
    version: str = Path(min_length=1, max_length=256),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    provider: HelmChartVersionProvider = Depends(get_helm_chart_version_provider),
) -> HelmChartDetail:
    return await _get_helm_chart_detail(
        source_id,
        chart_name,
        version,
        current=current,
        db=db,
        provider=provider,
    )


async def _get_helm_chart_detail(
    source_id: str,
    chart_name: str,
    version: str | None,
    *,
    current: Any,
    db: Any,
    provider: HelmChartVersionProvider,
) -> HelmChartDetail:
    workspace_id = _workspace_id(current)
    await asyncio.to_thread(
        require_resource_access,
        db,
        current,
        workspace_id,
        HELM_CHART_SOURCE_RESOURCE_TYPE,
        source_id,
        Permission.CATALOG_READ.value,
    )
    row = await asyncio.to_thread(
        db.get_helm_chart_source_record,
        workspace_id=workspace_id,
        source_id=source_id,
    )
    if row is None or str(row.get("status") or "") != "active":
        raise HTTPException(status_code=404, detail=HELM_CHART_SOURCE_NOT_FOUND)
    source = helm_chart_source_from_row(row)
    try:
        credential = await asyncio.to_thread(
            load_helm_provider_credential,
            db,
            workspace_id,
            row,
        )
    except CredentialEncryptionError:
        return HelmChartDetail(
            availability="unavailable",
            values_schema=HelmChartValuesSchemaUnavailable(
                reason_code=HELM_CHART_CREDENTIAL_UNAVAILABLE
            ),
            install=HelmChartInstallUnavailable(
                reason_code="helm_chart_install_recipe_unavailable"
            ),
            reason_codes=(HELM_CHART_CREDENTIAL_UNAVAILABLE,),
        )
    detail = await provider.get_chart_detail(
        source,
        chart_name,
        version=version,
        credential=credential,
    )
    if source.provider != "oci" or detail.chart is None:
        return detail
    recipe = matching_helm_recipe(
        source_reference=source.reference,
        chart_name=detail.chart.name,
        chart_version=detail.chart.version,
    )
    target = helm_upgrade_target(recipe) if recipe is not None else None
    if recipe is None or target is None:
        return detail
    return detail.model_copy(
        update={
            "values_schema": HelmChartValuesSchemaAvailable(schema=recipe.values_schema),
            "install": HelmChartInstallAvailable(target=target),
        }
    )


@router.get(
    gateway_routes.HELM_ARTIFACTHUB_SEARCH_PATH,
    response_model=ArtifactHubSearchPage,
)
async def search_artifacthub_charts(
    q: str = Query(min_length=1, max_length=200),
    offset: int = Query(default=0, ge=0, le=100_000),
    limit: int = Query(default=20, ge=1, le=ARTIFACTHUB_PAGE_MAX),
    sort: str = Query(default="relevance", pattern=r"^(relevance|stars|last_updated)$"),
    official: bool = Query(default=False),
    verified: bool = Query(default=False),
    current: Any = Depends(require_session),
    provider: ArtifactHubProvider = Depends(get_artifacthub_provider),
) -> ArtifactHubSearchPage:
    _ = current
    try:
        return await provider.search(
            query=q,
            offset=offset,
            limit=limit,
            sort=sort,  # type: ignore[arg-type]
            official=official,
            verified=verified,
        )
    except RuntimeError as exc:
        raise _artifacthub_http_error(exc) from exc


@router.get(
    gateway_routes.HELM_ARTIFACTHUB_CHART_PATH,
    response_model=ArtifactHubChartDetail,
)
async def get_artifacthub_chart(
    repository: str = Path(min_length=1, max_length=253),
    chart: str = Path(min_length=1, max_length=253),
    current: Any = Depends(require_session),
    provider: ArtifactHubProvider = Depends(get_artifacthub_provider),
) -> ArtifactHubChartDetail:
    _ = current
    return await _artifacthub_chart(provider, repository, chart, None)


@router.get(
    gateway_routes.HELM_ARTIFACTHUB_CHART_VERSION_PATH,
    response_model=ArtifactHubChartDetail,
)
async def get_artifacthub_chart_version(
    repository: str = Path(min_length=1, max_length=253),
    chart: str = Path(min_length=1, max_length=253),
    version: str = Path(min_length=1, max_length=256),
    current: Any = Depends(require_session),
    provider: ArtifactHubProvider = Depends(get_artifacthub_provider),
) -> ArtifactHubChartDetail:
    _ = current
    return await _artifacthub_chart(provider, repository, chart, version)


async def _artifacthub_chart(
    provider: ArtifactHubProvider,
    repository: str,
    chart: str,
    version: str | None,
) -> ArtifactHubChartDetail:
    try:
        return await provider.chart(repository, chart, version)
    except RuntimeError as exc:
        raise _artifacthub_http_error(exc) from exc


def _artifacthub_http_error(error: RuntimeError) -> HTTPException:
    code = str(error)
    if code == "artifacthub_chart_not_found":
        return HTTPException(status_code=404, detail=code)
    if code in {
        "artifacthub_query_invalid",
        "artifacthub_pagination_invalid",
        "artifacthub_chart_identity_invalid",
    }:
        return HTTPException(status_code=422, detail=code)
    return HTTPException(status_code=502, detail=code)


@router.post(
    gateway_routes.HELM_REPOSITORY_UPDATE_PATH,
    response_model=HelmRepositoryRefreshAccepted,
)
async def update_helm_repository(
    name: str = Path(
        min_length=1,
        max_length=120,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$",
    ),
    current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    provider: HelmChartVersionProvider = Depends(get_helm_chart_version_provider),
) -> HelmRepositoryRefreshAccepted:
    workspace_id = _workspace_id(current)
    source_ids = await accessible_helm_chart_source_ids(
        db,
        current,
        workspace_id,
        Permission.CONFIG_UPDATE.value,
    )
    batch = await asyncio.to_thread(
        db.list_helm_chart_source_records,
        workspace_id=workspace_id,
        source_ids=source_ids,
        limit=HELM_CHART_SOURCE_PAGE_MAX,
    )
    matches = tuple(
        row
        for row in batch.rows
        if str(row.get("name") or "") == name
        and str(row.get("provider") or "") == "repository"
        and str(row.get("status") or "") == "active"
    )
    if len(matches) != 1:
        raise HTTPException(status_code=404, detail=HELM_CHART_SOURCE_NOT_FOUND)
    row = dict(matches[0])
    source = helm_chart_source_from_row(row)
    try:
        credential = await asyncio.to_thread(
            load_helm_provider_credential,
            db,
            workspace_id,
            row,
        )
        refreshed = await provider.refresh_repository(source, credential=credential)
    except CredentialEncryptionError as exc:
        raise HTTPException(status_code=503, detail=HELM_CHART_CREDENTIAL_UNAVAILABLE) from exc
    except HelmRepositoryRefreshError as exc:
        raise HTTPException(status_code=502, detail=exc.reason_code) from exc
    with event_workspace(workspace_id):
        accepted = await events.accept_body(
            HelmChartSourceRefreshedBody(
                workspace_id=workspace_id,
                source_id=refreshed.source_id,
                name=source.name,
                chart_count=refreshed.chart_count,
                observed_at=refreshed.observed_at,
            ),
            actor=Actor(str(current.user_id), tuple(current.roles)),
        )
    return HelmRepositoryRefreshAccepted(
        **refreshed.model_dump(mode="json"),
        event_id=str(accepted.event.event_id),
        correlation_id=str(accepted.event.correlation_id),
    )


@router.get(
    gateway_routes.HELM_CHART_SOURCES_PATH,
    response_model=HelmChartSourcePage,
)
async def list_helm_chart_sources(
    limit: int = Query(default=50, ge=1, le=HELM_CHART_SOURCE_PAGE_MAX),
    cursor: str | None = Query(default=None, min_length=1, max_length=4096),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> HelmChartSourcePage:
    workspace_id = _workspace_id(current)
    source_ids = await accessible_helm_chart_source_ids(
        db,
        current,
        workspace_id,
        Permission.CATALOG_READ.value,
    )
    delete_ids: set[str] | None = set()
    if ServiceRole.SERVICE_ADMIN.value in tuple(getattr(current, "roles", ()) or ()):
        delete_ids = await accessible_helm_chart_source_ids(
            db,
            current,
            workspace_id,
            Permission.CONFIG_UPDATE.value,
        )
    page = await asyncio.to_thread(
        db.list_helm_chart_sources,
        workspace_id=workspace_id,
        limit=limit,
        cursor=cursor,
        source_ids=source_ids,
    )
    return page.model_copy(
        update={
            "items": tuple(
                source.model_copy(
                    update={
                        "actions": (
                            ("refresh", "delete")
                            if source.provider == "repository"
                            else ("delete",)
                        )
                        if delete_ids is None or source.source_id in delete_ids
                        else ()
                    }
                )
                for source in page.items
            )
        }
    )


@router.post(
    gateway_routes.HELM_CHART_SOURCES_PATH,
    response_model=HelmChartSource,
    status_code=201,
)
async def register_helm_chart_source(
    payload: HelmChartSourceRegisterRequest,
    current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
) -> HelmChartSource:
    workspace_id = _workspace_id(current)
    try:
        return await asyncio.to_thread(
            _register_chart_source,
            db,
            workspace_id,
            payload,
        )
    except HelmChartSourceConflict as exc:
        raise HTTPException(status_code=409, detail=HELM_CHART_SOURCE_CONFLICT) from exc
    except CredentialEncryptionError as exc:
        raise HTTPException(status_code=503, detail=HELM_CHART_CREDENTIAL_UNAVAILABLE) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete(
    gateway_routes.HELM_CHART_SOURCE_PATH,
    response_model=AcceptedResponse,
)
async def delete_helm_chart_source(
    payload: HelmChartSourceDeleteRequest,
    source_id: str = Path(min_length=1, max_length=80, pattern=r"^[a-z0-9-]+$"),
    current: Any = Depends(require_admin_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
) -> AcceptedResponse:
    """Atomically delete one exact source, its credential, and durable audit event."""

    workspace_id = _workspace_id(current)
    await asyncio.to_thread(
        require_resource_access,
        db,
        current,
        workspace_id,
        HELM_CHART_SOURCE_RESOURCE_TYPE,
        source_id,
        Permission.CONFIG_UPDATE.value,
    )
    try:
        canonical_reference = normalize_helm_chart_source_reference(
            payload.provider,
            payload.reference,
        )

        def stage(_conn: Any, _event: Any) -> None:
            _delete_chart_source(
                db,
                workspace_id=workspace_id,
                source_id=source_id,
                expected_provider=payload.provider,
                expected_name=payload.name,
                expected_reference=canonical_reference,
            )

        with event_workspace(workspace_id):
            accepted = await events.accept_body(
                HelmChartSourceDeletedBody(
                    workspace_id=workspace_id,
                    source_id=source_id,
                    provider=payload.provider,
                    name=payload.name,
                    reference=canonical_reference,
                ),
                actor=Actor(str(current.user_id), tuple(current.roles)),
                transactional_stage=stage,
            )
    except HelmChartSourceNotFound as exc:
        raise HTTPException(status_code=404, detail=HELM_CHART_SOURCE_NOT_FOUND) from exc
    except HelmChartSourceIdentityConflict as exc:
        raise HTTPException(status_code=409, detail="Helm chart source identity changed") from exc
    except CredentialEncryptionError as exc:
        raise HTTPException(status_code=503, detail=HELM_CHART_CREDENTIAL_UNAVAILABLE) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    return AcceptedResponse(
        accepted=True,
        event_id=str(accepted.event.event_id),
        correlation_id=str(accepted.event.correlation_id),
    )


@router.get(
    gateway_routes.HELM_CHART_SOURCE_VERSIONS_PATH,
    response_model=HelmChartVersionObservation,
)
async def get_helm_chart_source_versions(
    source_id: str = Path(min_length=1, max_length=80, pattern=r"^[a-z0-9-]+$"),
    chart_name: str = Path(
        min_length=1,
        max_length=512,
        pattern=r"^[A-Za-z0-9](?:[A-Za-z0-9._-]*[A-Za-z0-9])?$",
    ),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    provider: HelmChartVersionProvider = Depends(get_helm_chart_version_provider),
) -> HelmChartVersionObservation:
    workspace_id = _workspace_id(current)
    await asyncio.to_thread(
        require_resource_access,
        db,
        current,
        workspace_id,
        HELM_CHART_SOURCE_RESOURCE_TYPE,
        source_id,
        Permission.CATALOG_READ.value,
    )
    row = await asyncio.to_thread(
        db.get_helm_chart_source_record,
        workspace_id=workspace_id,
        source_id=source_id,
    )
    if row is None or str(row.get("status") or "") != "active":
        raise HTTPException(status_code=404, detail=HELM_CHART_SOURCE_NOT_FOUND)
    source = helm_chart_source_from_row(row)
    try:
        credential = await asyncio.to_thread(
            load_helm_provider_credential,
            db,
            workspace_id,
            row,
        )
    except CredentialEncryptionError:
        return HelmChartVersionObservation(
            source=source,
            chart_name=chart_name,
            availability="unavailable",
            versions=(),
            reason_codes=(HELM_CHART_CREDENTIAL_UNAVAILABLE,),
        )
    return await provider.fetch_versions(
        source,
        chart_name,
        credential=credential,
    )


async def _authorized_chart_source_rows(
    db: Any,
    current: Any,
    workspace_id: str,
    source_id: str | None,
) -> tuple[tuple[dict[str, Any], ...], bool]:
    if source_id is not None:
        await asyncio.to_thread(
            require_resource_access,
            db,
            current,
            workspace_id,
            HELM_CHART_SOURCE_RESOURCE_TYPE,
            source_id,
            Permission.CATALOG_READ.value,
        )
        row = await asyncio.to_thread(
            db.get_helm_chart_source_record,
            workspace_id=workspace_id,
            source_id=source_id,
        )
        if row is None or str(row.get("status") or "") != "active":
            raise HTTPException(status_code=404, detail=HELM_CHART_SOURCE_NOT_FOUND)
        return (dict(row),), False
    source_ids = await accessible_helm_chart_source_ids(
        db,
        current,
        workspace_id,
        Permission.CATALOG_READ.value,
    )
    batch = await asyncio.to_thread(
        db.list_helm_chart_source_records,
        workspace_id=workspace_id,
        source_ids=source_ids,
        limit=HELM_CHART_SOURCE_PAGE_MAX,
    )
    return tuple(dict(row) for row in batch.rows), bool(batch.truncated)


def _chart_catalog_page(
    observations: tuple[HelmChartCatalogObservation, ...],
    *,
    query: str,
    source_id: str | None,
    provider_filter: str | None,
    all_versions: bool,
    limit: int,
    source_scope_truncated: bool,
) -> HelmChartCatalogPage:
    ordered = tuple(
        item
        for observation in sorted(
            observations,
            key=lambda value: (value.source.name.casefold(), value.source.source_id),
        )
        for item in observation.items
    )
    total = min(
        sum(observation.total for observation in observations),
        HELM_CHART_CATALOG_TOTAL_MAX,
    )
    items = ordered[:limit]
    reasons = {reason for observation in observations for reason in observation.reason_codes}
    truncated = (
        source_scope_truncated
        or any(observation.truncated for observation in observations)
        or total > len(items)
    )
    if source_scope_truncated:
        reasons.add("helm_chart_source_scope_truncated")
    if truncated:
        reasons.add("helm_chart_catalog_truncated")
    available_count = sum(observation.availability != "unavailable" for observation in observations)
    if observations and available_count == 0:
        availability = "unavailable"
        items = ()
        total = 0
    elif reasons:
        availability = "partial"
    else:
        availability = "available"
    observed_values = tuple(
        observation.observed_at
        for observation in observations
        if observation.observed_at is not None
    )
    return HelmChartCatalogPage(
        availability=availability,
        items=items,
        total=total,
        limit=limit,
        query=query,
        source_id=source_id,
        provider=provider_filter,
        all_versions=all_versions,
        observed_at=max(observed_values) if observed_values else None,
        truncated=truncated,
        reason_codes=tuple(sorted(reasons)),
    )


def _workspace_id(current: Any) -> str:
    return str(getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID) or DEFAULT_WORKSPACE_ID)


async def accessible_helm_chart_source_ids(
    db: Any,
    current: Any,
    workspace_id: str,
    permission: str,
) -> set[str] | None:
    accessible = getattr(db, "accessible_resource_ids", None)
    if not callable(accessible):
        return set()
    result = await asyncio.to_thread(
        accessible,
        str(getattr(current, "user_id", "")),
        workspace_id,
        HELM_CHART_SOURCE_RESOURCE_TYPE,
        permission,
    )
    return None if result is None else {str(value) for value in result}


def _delete_chart_source(
    db: Any,
    *,
    workspace_id: str,
    source_id: str,
    expected_provider: str,
    expected_name: str,
    expected_reference: str,
) -> None:
    deleted = db.delete_helm_chart_source(
        workspace_id=workspace_id,
        source_id=source_id,
        expected_provider=expected_provider,
        expected_name=expected_name,
        expected_reference=expected_reference,
    )
    ref = str(deleted.get("credential_ref") or "")
    if not ref:
        return
    provider, scope = parse_credential_ref(ref)
    expected_credential_provider = helm_chart_credential_provider(expected_provider)
    expected_scope = helm_chart_credential_scope(source_id)
    if provider != expected_credential_provider or scope != expected_scope:
        raise CredentialEncryptionError(HELM_CHART_CREDENTIAL_UNAVAILABLE)
    delete_credential = getattr(db, "delete_workspace_credential", None)
    if not callable(delete_credential) or not delete_credential(workspace_id, provider, scope):
        raise CredentialEncryptionError(HELM_CHART_CREDENTIAL_UNAVAILABLE)


def _credential_payload(
    credential: HelmChartSourceCredentialInput,
) -> dict[str, str]:
    if credential.kind == "bearer" and credential.token is not None:
        return {
            "kind": "bearer",
            "token": credential.token.get_secret_value(),
        }
    if (
        credential.kind == "basic"
        and credential.username is not None
        and credential.password is not None
    ):
        return {
            "kind": "basic",
            "username": credential.username,
            "password": credential.password.get_secret_value(),
        }
    raise CredentialEncryptionError(HELM_CHART_CREDENTIAL_UNAVAILABLE)


def _register_chart_source(
    db: Any,
    workspace_id: str,
    payload: HelmChartSourceRegisterRequest,
) -> HelmChartSource:
    canonical_ref = normalize_helm_chart_source_reference(
        payload.provider,
        payload.reference,
    )
    source_id = helm_chart_source_id(workspace_id, payload.provider, canonical_ref)
    stored_credential_ref = None
    with unit_of_work_or_null(db):
        if payload.credential is not None:
            provider = helm_chart_credential_provider(payload.provider)
            scope = helm_chart_credential_scope(source_id)
            stored_credential_ref = credential_ref(provider, scope)
            encrypted = encrypt_credential(
                json.dumps(
                    _credential_payload(payload.credential),
                    ensure_ascii=True,
                    separators=(",", ":"),
                    sort_keys=True,
                )
            )
            locker = getattr(db, "lock_workspace_credential_scope", None)
            if callable(locker):
                locker(workspace_id, provider, scope)
            stored = db.upsert_workspace_credential(
                {
                    "workspace_id": workspace_id,
                    "provider": provider,
                    "scope": scope,
                    "encrypted_value": encrypted,
                    "metadata": {
                        "source_id": source_id,
                        "credential_kind": payload.credential.kind,
                    },
                }
            )
            _require_exact_credential_binding(
                stored,
                workspace_id=workspace_id,
                provider=provider,
                scope=scope,
            )
        return db.register_helm_chart_source(
            workspace_id=workspace_id,
            provider=payload.provider,
            name=payload.name,
            reference=canonical_ref,
            credential_ref=stored_credential_ref,
            access_policy={},
        )


def load_helm_provider_credential(
    db: Any,
    workspace_id: str,
    row: dict[str, Any],
) -> HelmProviderCredential | None:
    ref = str(row.get("credential_ref") or "")
    if not ref:
        return None
    provider, scope = parse_credential_ref(ref)
    expected_provider = helm_chart_credential_provider(str(row.get("provider") or ""))
    expected_scope = helm_chart_credential_scope(str(row.get("source_id") or ""))
    if provider != expected_provider or scope != expected_scope:
        raise CredentialEncryptionError(HELM_CHART_CREDENTIAL_UNAVAILABLE)
    getter = getattr(db, "get_workspace_credential", None)
    stored = getter(workspace_id, provider, scope) if callable(getter) else None
    if (
        not isinstance(stored, dict)
        or str(stored.get("workspace_id") or "") != workspace_id
        or str(stored.get("provider") or "") != provider
        or str(stored.get("scope") or "") != scope
        or str(stored.get("status") or "active") != "active"
    ):
        raise CredentialEncryptionError(HELM_CHART_CREDENTIAL_UNAVAILABLE)
    try:
        payload = json.loads(decrypt_credential(str(stored.get("encrypted_value") or "")))
    except (CredentialEncryptionError, json.JSONDecodeError) as exc:
        raise CredentialEncryptionError(HELM_CHART_CREDENTIAL_UNAVAILABLE) from exc
    if not isinstance(payload, dict):
        raise CredentialEncryptionError(HELM_CHART_CREDENTIAL_UNAVAILABLE)
    kind = str(payload.get("kind") or "")
    if kind == "bearer":
        token = str(payload.get("token") or "")
        if token:
            return HelmProviderCredential(kind=kind, token=token)
    if kind == "basic":
        username = str(payload.get("username") or "")
        password = str(payload.get("password") or "")
        if username and password:
            return HelmProviderCredential(
                kind=kind,
                username=username,
                password=password,
            )
    raise CredentialEncryptionError(HELM_CHART_CREDENTIAL_UNAVAILABLE)


def _require_exact_credential_binding(
    stored: object,
    *,
    workspace_id: str,
    provider: str,
    scope: str,
) -> None:
    if (
        not isinstance(stored, dict)
        or str(stored.get("workspace_id") or "") != workspace_id
        or str(stored.get("provider") or "") != provider
        or str(stored.get("scope") or "") != scope
        or str(stored.get("status") or "active") != "active"
    ):
        raise CredentialEncryptionError(HELM_CHART_CREDENTIAL_UNAVAILABLE)
