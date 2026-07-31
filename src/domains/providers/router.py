from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from domains.identity.dependencies import require_admin_session
from domains.providers.catalog import (
    catalog_body,
    cluster_registration_discovery,
    validate_provider_selection,
)
from packages.contracts.gateway.requests import ProviderSelectionRequest
from packages.contracts.gateway.responses import (
    ProviderCatalogResponse,
    ProviderClusterDiscoveryResponse,
    ProviderValidationResponse,
)
from packages.contracts.gateway.routes import (
    PROVIDERS_CATALOG_PATH,
    PROVIDERS_CLUSTER_DISCOVERY_PATH,
    PROVIDERS_VALIDATE_PATH,
)

router = APIRouter()


@router.get(PROVIDERS_CATALOG_PATH, response_model=ProviderCatalogResponse)
async def provider_catalog(
    _current: Any = Depends(require_admin_session),
) -> ProviderCatalogResponse:
    return ProviderCatalogResponse(providers=catalog_body())


@router.get(
    PROVIDERS_CLUSTER_DISCOVERY_PATH,
    response_model=ProviderClusterDiscoveryResponse,
)
async def provider_cluster_discovery(
    _current: Any = Depends(require_admin_session),
) -> ProviderClusterDiscoveryResponse:
    return ProviderClusterDiscoveryResponse(**cluster_registration_discovery())


@router.post(PROVIDERS_VALIDATE_PATH, response_model=ProviderValidationResponse)
async def provider_selection_validate(
    payload: ProviderSelectionRequest,
    _current: Any = Depends(require_admin_session),
) -> ProviderValidationResponse:
    result = validate_provider_selection(
        {
            "source": payload.source_provider,
            "deploy": payload.deploy_provider,
            "cloud": payload.cloud_provider,
            "secret": payload.secret_provider,
        },
        credential_refs=payload.credential_refs,
        capabilities=tuple(payload.capabilities),
    )
    return ProviderValidationResponse(**result)
