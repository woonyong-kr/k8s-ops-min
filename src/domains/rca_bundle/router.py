"""RemediationBundle read API — 세션 workspace와 report cluster 권한으로 fail-closed."""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter, Depends, HTTPException

from domains.identity.dependencies import require_cluster_access, require_session
from domains.rca.repository import RcaRepository
from domains.rca_bundle.serializer import remediation_bundle_response
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.responses import RemediationBundleResponse
from packages.contracts.identity import Permission
from packages.runtime.dependencies import get_db

HTTP_NOT_FOUND = 404
HTTP_FORBIDDEN = 403
REMEDIATION_BUNDLE_NOT_FOUND = "Remediation bundle not found"

router = APIRouter()


@router.get(
    gateway_routes.RCA_BUNDLE_PATH,
    response_model=RemediationBundleResponse,
)
async def get_remediation_bundle(
    correlation_id: str,
    current: Any = Depends(require_session),
    db: RcaRepository = Depends(get_db),
) -> RemediationBundleResponse:
    workspace_id = getattr(current, "workspace_id", None)
    if not isinstance(workspace_id, str) or not workspace_id:
        _raise_not_found()

    reports = await asyncio.to_thread(
        db.list_rca_report_records,
        workspace_id,
        correlation_id=correlation_id,
        limit=1,
    )
    if not reports:
        _raise_not_found()
    report = reports[0]
    if report.get("workspace_id") != workspace_id or report.get("correlation_id") != correlation_id:
        _raise_not_found()

    cluster_id = report.get("cluster_id")
    if not isinstance(cluster_id, str) or not cluster_id:
        _raise_not_found()
    try:
        require_cluster_access(
            db,
            current,
            workspace_id,
            cluster_id,
            Permission.RCA_READ.value,
            detail=REMEDIATION_BUNDLE_NOT_FOUND,
        )
    except HTTPException as exc:
        if exc.status_code == HTTP_FORBIDDEN:
            raise HTTPException(
                status_code=HTTP_NOT_FOUND,
                detail=REMEDIATION_BUNDLE_NOT_FOUND,
            ) from exc
        raise

    recovery = await asyncio.to_thread(
        db.get_recovery_plan_by_correlation,
        correlation_id,
        workspace_id,
    )
    return remediation_bundle_response(report, recovery)


def _raise_not_found() -> None:
    raise HTTPException(
        status_code=HTTP_NOT_FOUND,
        detail=REMEDIATION_BUNDLE_NOT_FOUND,
    )
