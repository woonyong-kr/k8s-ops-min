"""Authenticated HTTP and resumable SSE boundary for durable investigations."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator, Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Depends, Header, HTTPException, Path, Query, Request, status
from fastapi.responses import StreamingResponse

from domains.ai.context_facade import get_context_chat_llm
from domains.diagnose.engine import (
    DIAGNOSE_AGENT_ID,
    DIAGNOSE_AGENT_LABEL,
    DIAGNOSE_DISCLOSURE_REVISION,
    ContextDiagnoseEngine,
)
from domains.diagnose.repository import DiagnoseRepository
from domains.diagnose.service import DiagnoseService
from domains.diagnose.stream import DiagnoseStreamOverflow, InMemoryDiagnoseEventStream
from domains.identity.dependencies import require_cluster_access, require_session
from domains.target.connectivity import (
    AGENT_STATUS_ONLINE,
    AGENT_STATUS_STALE,
    cluster_connection_status,
)
from packages.contracts.diagnose import (
    DiagnoseAgentSelection,
    DiagnoseCapabilities,
    DiagnoseConsentGrant,
    DiagnoseConsentRequest,
    DiagnoseEvent,
    DiagnoseHistoryClearResult,
    DiagnoseResourceRunRequest,
    DiagnoseRun,
    DiagnoseRunCreateRequest,
    DiagnoseRunLaunchResult,
    DiagnoseRunList,
    DiagnoseTarget,
    DiagnoseTurnRequest,
)
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.identity import DEFAULT_WORKSPACE_ID, Permission
from packages.contracts.parity import ClusterScope, ResourceRef
from packages.runtime.dependencies import get_db

router = APIRouter()

SSE_MEDIA_TYPE = "text/event-stream"
SSE_HEARTBEAT_SECONDS = 15.0
TERMINAL_STATUSES = frozenset({"completed", "failed", "stopped", "stale", "unavailable"})
DEFAULT_AGENT = DiagnoseAgentSelection(
    agent_id=DIAGNOSE_AGENT_ID,
    isolated=True,
    effort="medium",
)


@router.get(
    gateway_routes.DIAGNOSE_CAPABILITIES_PATH,
    response_model=DiagnoseCapabilities,
)
async def get_diagnose_capabilities(
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> DiagnoseCapabilities:
    workspace_id = _workspace_id(current)
    repository = _repository(db)
    consented = await repository.has_consent(
        workspace_id=workspace_id,
        requested_by=current.user_id,
        agent_id=DIAGNOSE_AGENT_ID,
        disclosure_revision=DIAGNOSE_DISCLOSURE_REVISION,
        surface="browser",
    )
    return DiagnoseCapabilities(
        enabled=True,
        agent=DEFAULT_AGENT,
        label=DIAGNOSE_AGENT_LABEL,
        disclosure_revision=DIAGNOSE_DISCLOSURE_REVISION,
        consented=consented,
    )


@router.post(
    gateway_routes.DIAGNOSE_CONSENTS_PATH,
    response_model=DiagnoseConsentGrant,
    status_code=status.HTTP_201_CREATED,
)
async def grant_diagnose_consent(
    payload: DiagnoseConsentRequest,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> DiagnoseConsentGrant:
    workspace_id = _workspace_id(current)
    if payload.scope.workspace_id != workspace_id:
        raise HTTPException(status_code=403, detail="Diagnose workspace access denied")
    require_cluster_access(
        db,
        current,
        workspace_id,
        payload.scope.cluster_id,
        Permission.INVENTORY_READ.value,
    )
    if (
        payload.agent_id != DIAGNOSE_AGENT_ID
        or payload.disclosure_revision != DIAGNOSE_DISCLOSURE_REVISION
    ):
        raise HTTPException(status_code=422, detail="unsupported Diagnose disclosure")
    await _repository(db).record_consent(
        workspace_id=workspace_id,
        requested_by=current.user_id,
        agent_id=payload.agent_id,
        disclosure_revision=payload.disclosure_revision,
        surface=payload.surface,
    )
    return DiagnoseConsentGrant(**payload.model_dump())


@router.post(
    gateway_routes.DIAGNOSE_RUNS_PATH,
    response_model=DiagnoseRunLaunchResult,
    status_code=status.HTTP_202_ACCEPTED,
)
async def create_diagnose_run(
    payload: DiagnoseResourceRunRequest,
    request: Request,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    llm: Any | None = Depends(get_context_chat_llm),
) -> DiagnoseRunLaunchResult:
    workspace_id = _workspace_id(current)
    require_cluster_access(
        db,
        current,
        workspace_id,
        payload.cluster_id,
        Permission.INVENTORY_READ.value,
    )
    repository = _repository(db)
    consented = await repository.has_consent(
        workspace_id=workspace_id,
        requested_by=current.user_id,
        agent_id=payload.agent.agent_id,
        disclosure_revision=payload.disclosure_revision,
        surface="browser",
    )
    if not consented:
        raise HTTPException(status_code=409, detail="Diagnose disclosure consent is required")
    if payload.disclosure_revision != DIAGNOSE_DISCLOSURE_REVISION:
        raise HTTPException(status_code=409, detail="Diagnose disclosure revision is stale")

    target = _resolve_target(db, workspace_id=workspace_id, payload=payload)
    stream = _stream(request)
    engine = ContextDiagnoseEngine(
        db=db,
        current=current,
        repository=repository,
        stream=stream,
        llm=llm,
    )
    return await DiagnoseService(
        repository=repository,
        stream=stream,
        engine=engine,
    ).create_run(
        DiagnoseRunCreateRequest(target=target, agent=payload.agent),
        requested_by=current.user_id,
    )


@router.get(gateway_routes.DIAGNOSE_RUNS_PATH, response_model=DiagnoseRunList)
async def list_diagnose_runs(
    limit: int = Query(default=20, ge=1, le=100),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> DiagnoseRunList:
    return await _repository(db).list_runs(
        workspace_id=_workspace_id(current),
        requested_by=current.user_id,
        limit=limit,
    )


@router.get(gateway_routes.DIAGNOSE_RUN_PATH, response_model=DiagnoseRun)
async def get_diagnose_run(
    run_id: str = Path(min_length=1, max_length=160),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> DiagnoseRun:
    run = await _owned_run(db, current=current, run_id=run_id)
    _require_run_access(db, current=current, run=run)
    return run


@router.post(gateway_routes.DIAGNOSE_RUN_TURNS_PATH, response_model=DiagnoseRun)
async def add_diagnose_turn(
    payload: DiagnoseTurnRequest,
    request: Request,
    run_id: str = Path(min_length=1, max_length=160),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
    llm: Any | None = Depends(get_context_chat_llm),
) -> DiagnoseRun:
    run = await _owned_run(db, current=current, run_id=run_id)
    _require_run_access(db, current=current, run=run)
    repository = _repository(db)
    stream = _stream(request)
    transition = await DiagnoseService(
        repository=repository,
        stream=stream,
        engine=ContextDiagnoseEngine(
            db=db,
            current=current,
            repository=repository,
            stream=stream,
            llm=llm,
        ),
    ).add_turn(run, payload.question)
    if not transition.changed:
        raise HTTPException(
            status_code=409,
            detail=f"Diagnose run cannot accept a turn while {transition.run.status}",
        )
    return transition.run


@router.post(gateway_routes.DIAGNOSE_RUN_STOP_PATH, response_model=DiagnoseRun)
async def stop_diagnose_run(
    request: Request,
    run_id: str = Path(min_length=1, max_length=160),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> DiagnoseRun:
    run = await _owned_run(db, current=current, run_id=run_id)
    _require_run_access(db, current=current, run=run)
    transition = await DiagnoseService(
        repository=_repository(db),
        stream=_stream(request),
    ).stop_run(run)
    if not transition.changed and transition.run.status not in TERMINAL_STATUSES:
        raise HTTPException(status_code=409, detail="Diagnose run could not be stopped")
    return transition.run


@router.delete(
    gateway_routes.DIAGNOSE_HISTORY_PATH,
    response_model=DiagnoseHistoryClearResult,
)
async def clear_diagnose_history(
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> DiagnoseHistoryClearResult:
    deleted = await _repository(db).clear_finished(
        workspace_id=_workspace_id(current),
        requested_by=current.user_id,
    )
    return DiagnoseHistoryClearResult(deleted_runs=deleted)


@router.get(gateway_routes.DIAGNOSE_RUN_EVENTS_PATH)
async def stream_diagnose_events(
    request: Request,
    run_id: str = Path(min_length=1, max_length=160),
    after: int | None = Query(default=None, ge=0),
    last_event_id: Annotated[str | None, Header(alias="Last-Event-ID")] = None,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> StreamingResponse:
    cursor = _resolve_cursor(after, last_event_id)
    run = await _owned_run(db, current=current, run_id=run_id)
    _require_run_access(db, current=current, run=run)
    return StreamingResponse(
        _event_frames(
            request=request,
            repository=_repository(db),
            stream=_stream(request),
            run=run,
            cursor=cursor,
        ),
        media_type=SSE_MEDIA_TYPE,
        headers={
            "Cache-Control": "no-cache, no-transform",
            "X-Accel-Buffering": "no",
        },
    )


async def _event_frames(
    *,
    request: Request,
    repository: DiagnoseRepository,
    stream: InMemoryDiagnoseEventStream,
    run: DiagnoseRun,
    cursor: int,
) -> AsyncIterator[str]:
    subscription = await stream.subscribe(scope=run.target.scope, run_id=run.run_id)
    try:
        while not await request.is_disconnected():
            replay = await repository.replay(
                scope=run.target.scope,
                run_id=run.run_id,
                after_sequence=cursor,
            )
            if replay.state == "resync_required":
                payload = {
                    "run_id": run.run_id,
                    "requested_after_sequence": cursor,
                    "earliest_available_sequence": replay.earliest_available_sequence,
                    "high_water_sequence": replay.high_water_sequence,
                }
                yield f"event: resync_required\ndata: {json.dumps(payload, separators=(',', ':'))}\n\n"
                return
            for event in replay.events:
                cursor = event.sequence
                yield _sse_frame(event)

            current = await repository.get_run(scope=run.target.scope, run_id=run.run_id)
            if current is None or (
                current.status in TERMINAL_STATUSES and cursor >= replay.high_water_sequence
            ):
                return

            try:
                event = await asyncio.wait_for(
                    subscription.next(),
                    timeout=SSE_HEARTBEAT_SECONDS,
                )
            except TimeoutError:
                yield ": heartbeat\n\n"
                continue
            except DiagnoseStreamOverflow:
                continue
            if event.sequence <= cursor:
                continue
            # Always replay from PostgreSQL instead of trusting a best-effort wake event.
    finally:
        await subscription.close()


def _resolve_target(
    db: Any,
    *,
    workspace_id: str,
    payload: DiagnoseResourceRunRequest,
) -> DiagnoseTarget:
    reader = getattr(db, "get_inventory_resource_by_api_version", None)
    if not callable(reader):
        raise HTTPException(status_code=503, detail="exact inventory identity is unavailable")
    expected_api_version = _canonical_api_version(payload.api_group, payload.api_version)
    resource = reader(
        workspace_id=workspace_id,
        cluster_id=payload.cluster_id,
        resource_type=payload.resource_type,
        api_version=expected_api_version,
        kind=payload.kind,
        namespace=payload.namespace,
        name=payload.name,
    )
    if not isinstance(resource, Mapping):
        raise HTTPException(status_code=404, detail="Diagnose resource was not found")
    actual_uid = _required_text(resource.get("uid"))
    if not actual_uid or actual_uid != payload.uid:
        raise HTTPException(status_code=409, detail="Diagnose resource identity changed")
    actual_api_version = _required_text(resource.get("api_version"))
    if actual_api_version != expected_api_version:
        raise HTTPException(status_code=409, detail="Diagnose resource API identity changed")
    return DiagnoseTarget(
        scope=ClusterScope(
            workspace_id=workspace_id,
            cluster_id=payload.cluster_id,
            namespaces=(payload.namespace,) if payload.namespace else (),
            freshness=_scope_freshness(db, workspace_id, payload.cluster_id),
        ),
        resource=ResourceRef(
            api_group=payload.api_group,
            version=payload.api_version,
            kind=payload.kind,
            namespace=payload.namespace,
            name=payload.name,
            uid=actual_uid,
        ),
    )


async def _owned_run(db: Any, *, current: Any, run_id: str) -> DiagnoseRun:
    run = await _repository(db).get_user_run(
        workspace_id=_workspace_id(current),
        requested_by=current.user_id,
        run_id=run_id,
    )
    if run is None:
        raise HTTPException(status_code=404, detail="Diagnose run not found")
    return run


def _require_run_access(db: Any, *, current: Any, run: DiagnoseRun) -> None:
    require_cluster_access(
        db,
        current,
        run.target.scope.workspace_id,
        run.target.scope.cluster_id,
        Permission.INVENTORY_READ.value,
    )


def _repository(db: Any) -> DiagnoseRepository:
    if not isinstance(db, DiagnoseRepository):
        required = (
            "create_or_get_active",
            "get_user_run",
            "list_runs",
            "transition",
            "replay",
            "append_event",
            "clear_finished",
            "has_consent",
            "record_consent",
        )
        if not all(callable(getattr(db, name, None)) for name in required):
            raise HTTPException(status_code=503, detail="Diagnose persistence is unavailable")
    return db


def _stream(request: Request) -> InMemoryDiagnoseEventStream:
    stream = getattr(request.app.state, "diagnose_events", None)
    if not isinstance(stream, InMemoryDiagnoseEventStream):
        raise HTTPException(status_code=503, detail="Diagnose event stream is unavailable")
    return stream


def _workspace_id(current: Any) -> str:
    return getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)


def _canonical_api_version(api_group: str, api_version: str) -> str:
    group = api_group.strip().strip("/")
    version = api_version.strip().strip("/")
    if not version or "/" in version:
        raise HTTPException(status_code=422, detail="Diagnose API version is invalid")
    return f"{group}/{version}" if group else version


def _scope_freshness(db: Any, workspace_id: str, cluster_id: str) -> str:
    reader = getattr(db, "latest_cluster_agent_statuses", None)
    if not callable(reader):
        return "partial"
    latest = reader(workspace_id, {cluster_id})
    agent = latest.get(cluster_id) if isinstance(latest, Mapping) else None
    connection = cluster_connection_status(agent)
    if connection == AGENT_STATUS_ONLINE:
        return "live"
    if connection == AGENT_STATUS_STALE:
        return "stale"
    return "disconnected"


def _resolve_cursor(after: int | None, last_event_id: str | None) -> int:
    header_cursor: int | None = None
    if last_event_id is not None:
        if not last_event_id.isdigit():
            raise HTTPException(status_code=422, detail="invalid Last-Event-ID")
        header_cursor = int(last_event_id)
    if after is not None and header_cursor is not None and after != header_cursor:
        raise HTTPException(status_code=409, detail="Diagnose replay cursors conflict")
    return after if after is not None else header_cursor or 0


def _required_text(value: object) -> str:
    return value.strip() if isinstance(value, str) else ""


def _sse_frame(event: DiagnoseEvent) -> str:
    payload = event.model_dump_json()
    return f"id: {event.sequence}\nevent: diagnose\ndata: {payload}\n\n"
