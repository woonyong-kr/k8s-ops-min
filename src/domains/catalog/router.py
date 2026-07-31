"""서비스 카탈로그 API 라우트."""

from __future__ import annotations

import hashlib
import json
import re
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, status

from domains.catalog.install import (
    CatalogHelmInstallPayload,
    CatalogInstallValidationError,
    CatalogRecipeUnsupported,
    server_helm_recipe,
    validate_catalog_values,
    validate_install_names,
)
from domains.command.policy import (
    DEFAULT_COMMAND_HEARTBEAT_INTERVAL_SECONDS,
    DEFAULT_COMMAND_LEASE_SECONDS,
    DEFAULT_COMMAND_RETRY_DELAY_SECONDS,
    DEFAULT_COMMAND_RETRY_MAX_ATTEMPTS,
)
from domains.identity.dependencies import require_cluster_access, require_session
from domains.target.management_guard import (
    cluster_role_from_policy,
    is_management_registration,
    is_management_role,
    management_readonly_detail,
)
from domains.target.router import (
    AGENT_STATUS_ONLINE,
    cluster_connection_status,
)
from packages.config.constants import Command, CommandStatus, Sandbox
from packages.config.control import control_namespace_allowed
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.requests import CatalogInstallRequest
from packages.contracts.gateway.responses import (
    CatalogInstallAcceptedResponse,
    CatalogItemListResponse,
    CatalogItemResponse,
)
from packages.contracts.identity import DEFAULT_WORKSPACE_ID, Permission
from packages.runtime.dependencies import get_db

router = APIRouter()
HTTP_NOT_FOUND = 404
CATALOG_ITEM_NOT_FOUND = "catalog item not found"
CATALOG_INSTALL_COMMAND_PRIORITY = 100
CATALOG_INSTALL_VALIDATION_ERROR = "catalog_install_validation_error"
CATALOG_RECIPE_UNSUPPORTED = "catalog_recipe_unsupported"
IDEMPOTENCY_KEY_REUSED = "idempotency_key_reused"
IDEMPOTENCY_KEY_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._:-]{7,127}$")
CLUSTER_NOT_CONNECTED = {
    "code": "cluster_not_connected",
    "detail": "대상 클러스터 Agent가 연결되어 있지 않습니다.",
}
CATALOG_INSTALL_RUNNER_UNAVAILABLE = {
    "code": "catalog_install_runner_unavailable",
    "detail": "연결된 대상 Agent가 catalog command를 실행할 수 없습니다.",
}


def catalog_item_or_404(db: Any, item_id: str) -> dict[str, Any]:
    item = db.get_catalog_item(item_id)
    if item is None:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail=CATALOG_ITEM_NOT_FOUND)
    return item


def catalog_version_or_default(item: dict[str, Any], version: str | None) -> dict[str, Any]:
    desired_version = version or str(item["default_version"])
    for candidate in item.get("versions", []):
        if candidate["version"] == desired_version:
            return dict(candidate)
    raise HTTPException(status_code=HTTP_NOT_FOUND, detail="catalog item version not found")


def install_error(code: str, detail: str) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail={"code": code, "detail": detail},
    )


def ensure_target_cluster_ready(db: Any, workspace_id: str, cluster_id: str) -> None:
    registration = db.get_cluster_registration(workspace_id, cluster_id)
    policy = db.get_cluster_policy(workspace_id, cluster_id)
    if is_management_registration(registration) or is_management_role(
        cluster_role_from_policy(policy)
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST, detail=management_readonly_detail()
        )

    agents = db.list_cluster_agent_statuses(workspace_id, cluster_id)
    online_agents = [
        agent for agent in agents if cluster_connection_status(agent) == AGENT_STATUS_ONLINE
    ]
    if not online_agents:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=CLUSTER_NOT_CONNECTED)
    required_capabilities = {
        "command_receiver",
        Command.CATALOG_HELM_INSTALL_CAPABILITY,
    }
    if not any(
        isinstance(agent.get("capabilities"), list)
        and required_capabilities <= set(agent["capabilities"])
        for agent in online_agents
    ):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=CATALOG_INSTALL_RUNNER_UNAVAILABLE,
        )


def canonical_hash(value: dict[str, Any]) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True)
    return hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def catalog_install_plan(
    *,
    idempotency_key: str,
    request_fingerprint: str,
    workspace_id: str,
    requested_by: str,
    cluster_id: str,
    payload: CatalogHelmInstallPayload,
) -> dict[str, Any]:
    identity_hash = canonical_hash(
        {
            "idempotency_key": idempotency_key,
            "requested_by": requested_by,
            "workspace_id": workspace_id,
        }
    )
    command_id = f"cmd-catalog-{identity_hash[:24]}"
    correlation_id = f"corr-catalog-{identity_hash[:24]}"
    return {
        "command_id": command_id,
        "idempotency_key": idempotency_key,
        "request_fingerprint": request_fingerprint,
        "cluster_id": cluster_id,
        "action": Command.CATALOG_HELM_INSTALL_ACTION,
        "namespace": payload.namespace,
        "diff": {
            "resource": f"catalog/{payload.catalog_item_id}",
            "namespace": payload.namespace,
            "risk": Sandbox.RISK_TAG.value,
            "basis": {
                "application_name": payload.application_name,
                "catalog_version": payload.catalog_version,
                "release_name": payload.release_name,
            },
        },
        "payload": payload.model_dump(exclude_none=True),
        "steps": ["target Agent Helm install"],
        "lease": {
            "lease_seconds": DEFAULT_COMMAND_LEASE_SECONDS,
            "heartbeat_interval_seconds": DEFAULT_COMMAND_HEARTBEAT_INTERVAL_SECONDS,
        },
        "retry_policy": {
            "max_attempts": DEFAULT_COMMAND_RETRY_MAX_ATTEMPTS,
            "retry_delay_seconds": DEFAULT_COMMAND_RETRY_DELAY_SECONDS,
        },
        "routing_constraint": {
            "channel": "agent",
            "cluster_id": cluster_id,
            "workspace_id": workspace_id,
            "required_capability": Command.CATALOG_HELM_INSTALL_CAPABILITY,
        },
        "workspace_id": workspace_id,
        "priority": CATALOG_INSTALL_COMMAND_PRIORITY,
        "requested_by": requested_by,
        "reason": f"install catalog item {payload.catalog_item_id}",
        "correlation_id": correlation_id,
    }


@router.get(gateway_routes.CATALOG_ITEMS_PATH, response_model=CatalogItemListResponse)
async def list_catalog_items(
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> CatalogItemListResponse:
    _ = current
    return CatalogItemListResponse(items=db.list_catalog_items())


@router.get(gateway_routes.CATALOG_ITEM_PATH, response_model=CatalogItemResponse)
async def get_catalog_item(
    item_id: str,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> CatalogItemResponse:
    _ = current
    return CatalogItemResponse(item=catalog_item_or_404(db, item_id))


@router.post(
    gateway_routes.CATALOG_ITEM_INSTALLS_PATH,
    response_model=CatalogInstallAcceptedResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def install_catalog_item(
    item_id: str,
    payload: CatalogInstallRequest,
    idempotency_key: Annotated[
        str,
        Header(alias="Idempotency-Key", min_length=8, max_length=128),
    ],
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> CatalogInstallAcceptedResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    item = catalog_item_or_404(db, item_id)
    version = catalog_version_or_default(item, payload.version)
    require_cluster_access(
        db,
        current,
        workspace_id,
        payload.cluster_id,
        Permission.DEPLOY_RUN.value,
    )
    ensure_target_cluster_ready(db, workspace_id, payload.cluster_id)

    if IDEMPOTENCY_KEY_PATTERN.fullmatch(idempotency_key) is None:
        raise install_error(CATALOG_INSTALL_VALIDATION_ERROR, "invalid Idempotency-Key")

    try:
        recipe = server_helm_recipe(str(item["item_id"]), str(version["version"]))
        release_name = payload.release_name or payload.application_name
        validate_install_names(
            application_name=payload.application_name,
            namespace=payload.namespace,
            release_name=release_name,
        )
        if payload.namespace != Sandbox.NAMESPACE or not control_namespace_allowed(
            payload.namespace
        ):
            raise CatalogInstallValidationError(
                "catalog installs are limited to the sandbox control namespace"
            )
        values = validate_catalog_values(recipe.values_schema, payload.values)
    except CatalogRecipeUnsupported as exc:
        raise install_error(CATALOG_RECIPE_UNSUPPORTED, str(exc)) from exc
    except CatalogInstallValidationError as exc:
        raise install_error(CATALOG_INSTALL_VALIDATION_ERROR, str(exc)) from exc

    command_payload = CatalogHelmInstallPayload(
        catalog_item_id=recipe.item_id,
        catalog_version=recipe.version,
        namespace=payload.namespace,
        application_name=payload.application_name,
        release_name=release_name,
        values=values,
    )
    request_fingerprint = canonical_hash(
        {
            "cluster_id": payload.cluster_id,
            "payload": command_payload.model_dump(exclude_none=True),
            "recipe_digest": recipe.chart_digest,
            "recipe_fixed_values": recipe.fixed_values,
        }
    )
    plan = catalog_install_plan(
        idempotency_key=idempotency_key,
        request_fingerprint=request_fingerprint,
        workspace_id=workspace_id,
        requested_by=str(current.user_id),
        cluster_id=payload.cluster_id,
        payload=command_payload,
    )
    inserted = db.queue_agent_command(str(plan["correlation_id"]), plan, CommandStatus.QUEUED)
    command_status = CommandStatus.QUEUED
    correlation_id = str(plan["correlation_id"])
    if not inserted:
        existing = await db.get_agent_command(str(plan["command_id"]), workspace_id)
        existing_plan = existing.get("payload") if isinstance(existing, dict) else None
        if (
            not isinstance(existing_plan, dict)
            or existing_plan.get("request_fingerprint") != request_fingerprint
        ):
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={
                    "code": IDEMPOTENCY_KEY_REUSED,
                    "detail": "Idempotency-Key가 다른 설치 요청에 이미 사용되었습니다.",
                },
            )
        correlation_id = str(existing.get("correlation_id") or correlation_id)
        command_status = str(existing.get("status") or command_status)

    return CatalogInstallAcceptedResponse(
        accepted=True,
        command_id=str(plan["command_id"]),
        correlation_id=correlation_id,
        status=command_status,
    )
