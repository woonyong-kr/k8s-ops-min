"""Prometheus live configuration across browser, management plane, and target agent."""

from __future__ import annotations

import json
import uuid
from typing import Any
from urllib.parse import urlsplit

from fastapi import APIRouter, Depends, HTTPException, Query, status

from domains.identity.dependencies import (
    ClusterAgentIdentity,
    require_cluster_access,
    require_cluster_agent,
    require_session,
)
from domains.integrations.events import PrometheusIntegrationConfiguredBody
from domains.target.evidence_policy import default_agent_policy
from packages.contracts.auth import Actor
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.requests import AgentPolicy, EvidenceProviderPolicy
from packages.contracts.identity import (
    DEFAULT_WORKSPACE_ID,
    Permission,
)
from packages.contracts.integrations import (
    AgentPrometheusIntegrationEnvelope,
    AgentPrometheusIntegrationStatus,
    PrometheusIntegrationStatus,
    PrometheusIntegrationUpdateRequest,
)
from packages.contracts.parity import CommandReceipt, OperationEvent
from packages.runtime.dependencies import get_db, get_events, get_operation_events
from packages.security.credentials import (
    CredentialEncryptionError,
    agent_envelope_context,
    decrypt_credential,
    encrypt_credential,
    seal_agent_payload,
)

router = APIRouter()
agent_router = APIRouter()

PROMETHEUS_CREDENTIAL_PROVIDER = "prometheus"
PROMETHEUS_POLICY_PROVIDER = "metrics"
PROMETHEUS_OPERATION_PREFIX = "prometheus-integration"
PROMETHEUS_ACCESS_DENIED = "prometheus integration access denied"
PROMETHEUS_NOT_CONFIGURED = "prometheus integration is not configured"
PROMETHEUS_REVISION_CONFLICT = "prometheus integration revision is not current"
PROMETHEUS_CREDENTIAL_UNAVAILABLE = "prometheus integration credential is unavailable"


def prometheus_credential_scope(cluster_id: str) -> str:
    return f"cluster:{cluster_id}"


def _workspace_id(current: Any) -> str:
    return str(getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID))


def _metadata(row: dict[str, Any]) -> dict[str, Any]:
    value = row.get("metadata")
    if not isinstance(value, dict):
        value = row.get("metadata_")
    return dict(value) if isinstance(value, dict) else {}


def _stored_integration(
    db: Any,
    workspace_id: str,
    cluster_id: str,
    *,
    conn: Any | None = None,
) -> dict[str, Any] | None:
    row = db.get_workspace_credential(
        workspace_id,
        PROMETHEUS_CREDENTIAL_PROVIDER,
        prometheus_credential_scope(cluster_id),
        **({"conn": conn} if conn is not None else {}),
    )
    if not isinstance(row, dict):
        return None
    if (
        str(row.get("workspace_id") or "") != workspace_id
        or str(row.get("provider") or "") != PROMETHEUS_CREDENTIAL_PROVIDER
        or str(row.get("scope") or "") != prometheus_credential_scope(cluster_id)
        or str(row.get("status") or "active") != "active"
    ):
        return None
    return row


def _status_from_row(cluster_id: str, row: dict[str, Any] | None) -> PrometheusIntegrationStatus:
    if row is None:
        return PrometheusIntegrationStatus(cluster_id=cluster_id, state="unconfigured")
    metadata = _metadata(row)
    header_keys = metadata.get("header_keys")
    return PrometheusIntegrationStatus(
        cluster_id=cluster_id,
        revision=str(metadata.get("revision") or "") or None,
        operation_id=str(metadata.get("operation_id") or "") or None,
        address=str(metadata.get("address") or "") or None,
        header_keys=sorted(str(key) for key in header_keys)
        if isinstance(header_keys, list)
        else [],
        state=str(metadata.get("state") or "pending"),
        error_code=str(metadata.get("error_code") or "") or None,
    )


def _stored_headers(row: dict[str, Any] | None) -> dict[str, str]:
    if row is None:
        return {}
    try:
        secret = json.loads(decrypt_credential(str(row.get("encrypted_value") or "")))
    except (CredentialEncryptionError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail=PROMETHEUS_CREDENTIAL_UNAVAILABLE) from exc
    headers = secret.get("headers") if isinstance(secret, dict) else None
    if not isinstance(headers, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in headers.items()
    ):
        raise HTTPException(status_code=409, detail=PROMETHEUS_CREDENTIAL_UNAVAILABLE)
    return dict(headers)


def _url_origin(value: str) -> tuple[str, str, int | None]:
    parsed = urlsplit(value)
    default_port = 443 if parsed.scheme.casefold() == "https" else 80
    return parsed.scheme.casefold(), (parsed.hostname or "").casefold(), parsed.port or default_port


@router.get(
    gateway_routes.PROMETHEUS_INTEGRATION_PATH,
    response_model=PrometheusIntegrationStatus,
)
async def get_prometheus_integration(
    cluster_id: str = Query(min_length=1, max_length=253),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> PrometheusIntegrationStatus:
    workspace_id = _workspace_id(current)
    require_cluster_access(
        db,
        current,
        workspace_id,
        cluster_id,
        Permission.CLUSTER_READ.value,
        detail=PROMETHEUS_ACCESS_DENIED,
    )
    return _status_from_row(cluster_id, _stored_integration(db, workspace_id, cluster_id))


@router.put(
    gateway_routes.PROMETHEUS_INTEGRATION_PATH,
    response_model=PrometheusIntegrationStatus,
    status_code=status.HTTP_202_ACCEPTED,
)
async def update_prometheus_integration(
    payload: PrometheusIntegrationUpdateRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    events: Any = Depends(get_events),
    operation_events: Any = Depends(get_operation_events),
) -> PrometheusIntegrationStatus:
    workspace_id = _workspace_id(current)
    require_cluster_access(
        db,
        current,
        workspace_id,
        payload.cluster_id,
        Permission.CLUSTER_POLICY_MANAGE.value,
        detail=PROMETHEUS_ACCESS_DENIED,
    )
    registration = db.get_cluster_registration(workspace_id, payload.cluster_id)
    if not isinstance(registration, dict):
        raise HTTPException(status_code=404, detail="cluster registration not found")

    revision = str(uuid.uuid4())
    operation_id = f"{PROMETHEUS_OPERATION_PREFIX}-{uuid.uuid4()}"
    staged: list[OperationEvent] = []
    effective_headers_box: list[dict[str, str]] = []

    def stage(conn: Any, _event: Any) -> None:
        db.lock_cluster_policy_for_update(
            workspace_id,
            payload.cluster_id,
            conn=conn,
        )
        db.lock_workspace_credential_scope(
            workspace_id,
            PROMETHEUS_CREDENTIAL_PROVIDER,
            prometheus_credential_scope(payload.cluster_id),
            conn=conn,
        )
        existing_integration = _stored_integration(
            db,
            workspace_id,
            payload.cluster_id,
            conn=conn,
        )
        existing_metadata = _metadata(existing_integration) if existing_integration else {}
        if payload.headers is None and existing_integration is not None:
            existing_address = str(_metadata(existing_integration).get("address") or "")
            if existing_address and _url_origin(existing_address) != _url_origin(
                payload.prometheus_url
            ):
                raise HTTPException(
                    status_code=409,
                    detail="prometheus headers must be re-entered when the URL origin changes",
                )
        effective_headers = (
            _stored_headers(existing_integration) if payload.headers is None else payload.headers
        )
        encrypted = encrypt_credential(
            json.dumps(
                {"headers": effective_headers},
                ensure_ascii=True,
                separators=(",", ":"),
                sort_keys=True,
            )
        )
        existing = db.get_cluster_policy(workspace_id, payload.cluster_id, conn=conn)
        base_policy = (
            AgentPolicy.model_validate(existing)
            if isinstance(existing, dict)
            else default_agent_policy(cluster_id=payload.cluster_id)
        )
        providers = dict(base_policy.evidence.providers)
        provider = providers.get(PROMETHEUS_POLICY_PROVIDER, EvidenceProviderPolicy())
        providers[PROMETHEUS_POLICY_PROVIDER] = provider.model_copy(
            update={
                "configuration_revision": revision,
                "configuration_operation_id": operation_id,
            }
        )
        next_policy = base_policy.model_copy(
            update={
                "generation": base_policy.generation + 1,
                "evidence": base_policy.evidence.model_copy(update={"providers": providers}),
            }
        )
        metadata = {
            "cluster_id": payload.cluster_id,
            "revision": revision,
            "operation_id": operation_id,
            "address": payload.prometheus_url,
            "header_keys": list(effective_headers),
            "state": "pending",
            "error_code": None,
        }
        previous_operation_id = str(existing_metadata.get("operation_id") or "")
        if (
            str(existing_metadata.get("state") or "") == "pending"
            and previous_operation_id
            and previous_operation_id != operation_id
        ):
            superseded = db.stage_integration_operation_event(
                conn,
                workspace_id=workspace_id,
                operation_id=previous_operation_id,
                cluster_id=payload.cluster_id,
                kind="cancelled",
                payload={
                    "cluster_id": payload.cluster_id,
                    "status": "cancelled",
                    "state": "superseded",
                    "revision": str(existing_metadata.get("revision") or ""),
                    "superseded_by": operation_id,
                },
            )
            if superseded is not None:
                staged.append(superseded)
        db.upsert_workspace_credential(
            {
                "workspace_id": workspace_id,
                "provider": PROMETHEUS_CREDENTIAL_PROVIDER,
                "scope": prometheus_credential_scope(payload.cluster_id),
                "encrypted_value": encrypted,
                "metadata": metadata,
            },
            conn=conn,
        )
        db.upsert_cluster_policy(
            workspace_id,
            payload.cluster_id,
            next_policy.model_dump(),
            conn=conn,
        )
        operation_event = db.stage_integration_operation_event(
            conn,
            workspace_id=workspace_id,
            operation_id=operation_id,
            cluster_id=payload.cluster_id,
            payload={
                "status": "queued",
                "state": "pending",
                "revision": revision,
                "address": payload.prometheus_url,
                "header_keys": list(effective_headers),
            },
        )
        if operation_event is not None:
            staged.append(operation_event)
        effective_headers_box.append(dict(effective_headers))

    try:
        accepted = await events.accept_body(
            PrometheusIntegrationConfiguredBody(
                workspace_id=workspace_id,
                cluster_id=payload.cluster_id,
                revision=revision,
                operation_id=operation_id,
                address=payload.prometheus_url,
                submitted_header_keys=list(payload.headers or {}),
                preserve_stored_headers=payload.headers is None,
            ),
            actor=Actor(str(current.user_id), tuple(current.roles)),
            transactional_stage=stage,
        )
    except CredentialEncryptionError as exc:
        raise HTTPException(
            status_code=503,
            detail="prometheus credential encryption unavailable",
        ) from exc
    except ValueError as exc:
        raise HTTPException(status_code=409, detail="integration configuration conflict") from exc

    announce = getattr(operation_events, "announce", None)
    if callable(announce):
        for operation_event in staged:
            await announce(operation_event, workspace_id=workspace_id)
    event_id = str(accepted.event.event_id)
    correlation_id = str(accepted.event.correlation_id)
    receipt = CommandReceipt(
        command_id=operation_id,
        event_id=event_id,
        audit_event_id=event_id,
        correlation_id=correlation_id,
        status="queued",
    )
    effective_headers = effective_headers_box[0] if effective_headers_box else {}
    return PrometheusIntegrationStatus(
        cluster_id=payload.cluster_id,
        revision=revision,
        operation_id=operation_id,
        address=payload.prometheus_url,
        header_keys=list(effective_headers),
        state="pending",
        receipt=receipt,
    )


@agent_router.get(
    gateway_routes.AGENT_PROMETHEUS_INTEGRATION_PATH,
    response_model=AgentPrometheusIntegrationEnvelope,
)
async def agent_prometheus_integration(
    revision: str = Query(min_length=1, max_length=120),
    identity: ClusterAgentIdentity = Depends(require_cluster_agent),
    db: Any = Depends(get_db),
) -> AgentPrometheusIntegrationEnvelope:
    row = _stored_integration(db, identity.workspace_id, identity.cluster_id)
    if row is None:
        raise HTTPException(status_code=404, detail=PROMETHEUS_NOT_CONFIGURED)
    metadata = _metadata(row)
    if str(metadata.get("revision") or "") != revision:
        raise HTTPException(status_code=409, detail=PROMETHEUS_REVISION_CONFLICT)
    try:
        secret = json.loads(decrypt_credential(str(row.get("encrypted_value") or "")))
    except (CredentialEncryptionError, json.JSONDecodeError) as exc:
        raise HTTPException(status_code=409, detail=PROMETHEUS_CREDENTIAL_UNAVAILABLE) from exc
    headers = secret.get("headers") if isinstance(secret, dict) else None
    if not isinstance(headers, dict) or not all(
        isinstance(key, str) and isinstance(value, str) for key, value in headers.items()
    ):
        raise HTTPException(status_code=409, detail=PROMETHEUS_CREDENTIAL_UNAVAILABLE)
    private_registration_getter = getattr(
        db,
        "get_cluster_registration_install_credentials",
        None,
    )
    registration = (
        private_registration_getter(identity.workspace_id, identity.cluster_id)
        if callable(private_registration_getter)
        else None
    )
    public_key = (
        str(registration.get("agent_envelope_public_key") or "")
        if isinstance(registration, dict)
        else ""
    )
    operation_id = str(metadata.get("operation_id") or "")
    try:
        context = agent_envelope_context(
            identity.workspace_id,
            identity.cluster_id,
            revision,
            operation_id,
            str(metadata.get("address") or ""),
        )
        sealed_headers = seal_agent_payload({"headers": dict(headers)}, public_key, context)
    except CredentialEncryptionError as exc:
        raise HTTPException(
            status_code=409,
            detail="agent envelope identity is unavailable; reconnect the cluster",
        ) from exc
    return AgentPrometheusIntegrationEnvelope(
        cluster_id=identity.cluster_id,
        revision=revision,
        operation_id=operation_id,
        address=str(metadata.get("address") or ""),
        sealed_headers=sealed_headers,
    )


@agent_router.post(gateway_routes.AGENT_PROMETHEUS_INTEGRATION_STATUS_PATH)
async def report_agent_prometheus_integration_status(
    payload: AgentPrometheusIntegrationStatus,
    identity: ClusterAgentIdentity = Depends(require_cluster_agent),
    db: Any = Depends(get_db),
    operation_events: Any = Depends(get_operation_events),
) -> dict[str, bool]:
    event_kind = (
        "completed"
        if payload.state == "connected"
        else "progress"
        if payload.state == "retrying"
        else "failed"
    )
    stored_state = "pending" if payload.state == "retrying" else payload.state
    staged: list[OperationEvent] = []
    with db.connection() as conn:
        db.lock_workspace_credential_scope(
            identity.workspace_id,
            PROMETHEUS_CREDENTIAL_PROVIDER,
            prometheus_credential_scope(identity.cluster_id),
            conn=conn,
        )
        row = _stored_integration(
            db,
            identity.workspace_id,
            identity.cluster_id,
            conn=conn,
        )
        if row is None:
            raise HTTPException(status_code=404, detail=PROMETHEUS_NOT_CONFIGURED)
        metadata = _metadata(row)
        if (
            str(metadata.get("revision") or "") != payload.revision
            or str(metadata.get("operation_id") or "") != payload.operation_id
        ):
            raise HTTPException(status_code=409, detail=PROMETHEUS_REVISION_CONFLICT)
        current_state = str(metadata.get("state") or "pending")
        current_error = str(metadata.get("error_code") or "") or None
        if current_state in {"connected", "failed"}:
            if current_state == payload.state and current_error == payload.error_code:
                return {"accepted": True}
            raise HTTPException(status_code=409, detail=PROMETHEUS_REVISION_CONFLICT)
        updated = db.update_workspace_credential_metadata(
            workspace_id=identity.workspace_id,
            provider=PROMETHEUS_CREDENTIAL_PROVIDER,
            scope=prometheus_credential_scope(identity.cluster_id),
            expected_revision=payload.revision,
            expected_state="pending",
            metadata={
                "state": stored_state,
                "error_code": payload.error_code,
            },
            conn=conn,
        )
        if updated is None:
            raise HTTPException(status_code=409, detail=PROMETHEUS_REVISION_CONFLICT)
        operation_event = db.stage_integration_operation_event(
            conn,
            workspace_id=identity.workspace_id,
            operation_id=payload.operation_id,
            cluster_id=identity.cluster_id,
            kind=event_kind,
            payload={
                "cluster_id": identity.cluster_id,
                "status": event_kind,
                "state": payload.state,
                "revision": payload.revision,
                "address": str(metadata.get("address") or ""),
                "error_code": payload.error_code,
            },
        )
        if operation_event is not None:
            staged.append(operation_event)
    announce = getattr(operation_events, "announce", None)
    if staged and callable(announce):
        await announce(staged[0], workspace_id=identity.workspace_id)
    return {"accepted": True}


router.include_router(agent_router)
