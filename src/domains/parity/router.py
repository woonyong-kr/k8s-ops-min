"""Authenticated API for the generated reference-feature contract catalog."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends

from domains.identity.dependencies import require_session
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.reference_feature_catalog import (
    ReferenceFeatureContractCatalog,
    load_feature_contract_catalog,
)

router = APIRouter()


@router.get(
    gateway_routes.FEATURE_CONTRACTS_PATH,
    response_model=ReferenceFeatureContractCatalog,
)
async def list_feature_contracts(
    _current: Any = Depends(require_session),
) -> ReferenceFeatureContractCatalog:
    """Return the single generated catalog used to map every reference feature."""
    return load_feature_contract_catalog()
