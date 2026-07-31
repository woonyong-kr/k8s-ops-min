"""Authenticated pod/workload log SSE routes."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query, Request
from fastapi.responses import StreamingResponse

from domains.identity.dependencies import require_session
from domains.log_stream.service import (
    WorkloadLogKind,
    queue_log_query,
    resolve_pod_target,
    resolve_scheduled_run_target,
    resolve_workload_target,
    scheduled_workload_run_catalog,
    stream_log_events,
)
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.identity import DEFAULT_WORKSPACE_ID
from packages.contracts.log_stream import (
    LogStreamSseMessage,
    ScheduledWorkloadRunCatalog,
    encode_log_stream_sse,
)
from packages.runtime.dependencies import get_db

KUBERNETES_NAME_PATTERN = r"^[a-z0-9](?:[-a-z0-9.]*[a-z0-9])?$"
KUBERNETES_NAMESPACE_PATTERN = r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$"
KUBERNETES_CONTAINER_PATTERN = r"^[a-z0-9](?:[-a-z0-9]*[a-z0-9])?$"
KUBERNETES_KIND_PATTERN = r"^[A-Za-z][A-Za-z0-9.]*$"
RUN_KEY_PATTERN = r"^[A-Za-z0-9][A-Za-z0-9._:-]*$"

router = APIRouter()


class LogSseResponse(StreamingResponse):
    media_type = "text/event-stream"


def _streaming_response(request: Request, db: Any, current: Any, target: Any) -> LogSseResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    try:
        initial_query = queue_log_query(
            db,
            workspace_id=workspace_id,
            user_id=str(current.user_id),
            target=target,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail="log stream unavailable") from exc

    async def body():
        async for envelope in stream_log_events(
            request,
            db,
            current=current,
            workspace_id=workspace_id,
            initial_target=target,
            initial_query=initial_query,
        ):
            yield encode_log_stream_sse(envelope)

    return LogSseResponse(
        body(),
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@router.get(
    gateway_routes.POD_LOG_STREAM_PATH,
    response_model=LogStreamSseMessage,
    response_class=LogSseResponse,
)
async def stream_pod_logs(
    request: Request,
    namespace: str = Path(min_length=1, max_length=63, pattern=KUBERNETES_NAMESPACE_PATTERN),
    name: str = Path(min_length=1, max_length=253, pattern=KUBERNETES_NAME_PATTERN),
    cluster_id: str = Query(min_length=1, max_length=512),
    container: str | None = Query(
        default=None,
        min_length=1,
        max_length=63,
        pattern=KUBERNETES_CONTAINER_PATTERN,
    ),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> LogSseResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    target = resolve_pod_target(
        db,
        current=current,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        namespace=namespace,
        name=name,
        container=container,
    )
    return _streaming_response(request, db, current, target)


@router.get(
    gateway_routes.WORKLOAD_LOG_STREAM_PATH,
    response_model=LogStreamSseMessage,
    response_class=LogSseResponse,
)
async def stream_workload_logs(
    request: Request,
    kind: WorkloadLogKind,
    namespace: str = Path(min_length=1, max_length=63, pattern=KUBERNETES_NAMESPACE_PATTERN),
    name: str = Path(min_length=1, max_length=253, pattern=KUBERNETES_NAME_PATTERN),
    cluster_id: str = Query(min_length=1, max_length=512),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> LogSseResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    target = resolve_workload_target(
        db,
        current=current,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        kind=kind,
        namespace=namespace,
        name=name,
        container=None,
    )
    return _streaming_response(request, db, current, target)


@router.get(
    gateway_routes.SCHEDULED_WORKLOAD_RUNS_PATH,
    response_model=ScheduledWorkloadRunCatalog,
)
async def list_scheduled_workload_runs(
    kind: str = Path(min_length=1, max_length=80, pattern=KUBERNETES_KIND_PATTERN),
    namespace: str = Path(min_length=1, max_length=63, pattern=KUBERNETES_NAMESPACE_PATTERN),
    name: str = Path(min_length=1, max_length=253, pattern=KUBERNETES_NAME_PATTERN),
    cluster_id: str = Query(min_length=1, max_length=512),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> ScheduledWorkloadRunCatalog:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    return scheduled_workload_run_catalog(
        db,
        current=current,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        owner_kind=kind,
        namespace=namespace,
        owner_name=name,
    )


@router.get(
    gateway_routes.SCHEDULED_WORKLOAD_RUN_LOG_STREAM_PATH,
    response_model=LogStreamSseMessage,
    response_class=LogSseResponse,
)
async def stream_scheduled_workload_run_logs(
    request: Request,
    kind: str = Path(min_length=1, max_length=80, pattern=KUBERNETES_KIND_PATTERN),
    namespace: str = Path(min_length=1, max_length=63, pattern=KUBERNETES_NAMESPACE_PATTERN),
    name: str = Path(min_length=1, max_length=253, pattern=KUBERNETES_NAME_PATTERN),
    run_key: str = Path(min_length=1, max_length=255, pattern=RUN_KEY_PATTERN),
    cluster_id: str = Query(min_length=1, max_length=512),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> LogSseResponse:
    workspace_id = getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
    target = resolve_scheduled_run_target(
        db,
        current=current,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        owner_kind=kind,
        namespace=namespace,
        owner_name=name,
        run_key=run_key,
    )
    return _streaming_response(request, db, current, target)
