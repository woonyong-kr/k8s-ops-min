"""rca 범용 조회 라우터 — 세션 워크스페이스 범위의 evidence/RCA report 읽기 API.

agent 수신 라우터(router.py, 토큰 가드)와 달리 이 라우터는 사용자 세션 가드만 쓰고,
모든 질의를 세션 workspace 로 강제 범위 지정한다(다른 워크스페이스 row 노출 불가).
RCA report 는 payload 원문 대신 화이트리스트 요약만 내려 secret 원문 유출을 차단한다.
"""

from __future__ import annotations

import asyncio
import base64
import json
from binascii import Error as BinasciiError
from datetime import datetime
from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Query

from domains.identity.dependencies import require_session
from domains.rca.report_projection import rca_report_summary
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.gateway import limits as gateway_limits
from packages.contracts.gateway import routes as gateway_routes
from packages.contracts.gateway.responses import (
    EvidenceQueryResponse,
    EvidenceRecordItem,
    EvidenceWindowListResponse,
    EvidenceWindowPayloadResponse,
    EvidenceWindowSummaryItem,
    RcaReportListResponse,
    RcaReportSummaryItem,
)
from packages.contracts.identity import DEFAULT_WORKSPACE_ID
from packages.runtime.dependencies import get_db

DEFAULT_QUERY_LIMIT = gateway_limits.RCA_QUERY_DEFAULT_LIMIT
MAX_QUERY_LIMIT = gateway_limits.RCA_QUERY_MAX_LIMIT
HTTP_NOT_FOUND = 404
HTTP_UNPROCESSABLE = 422
INVALID_TIMESTAMP_DETAIL = "must be an ISO-8601 timestamp"
INVALID_CURSOR_DETAIL = "cursor is invalid"
CURSOR_VERSION = 1

router = APIRouter()


@router.get(gateway_routes.EVIDENCE_QUERY_PATH, response_model=EvidenceQueryResponse)
async def list_evidence(
    correlation_id: str | None = None,
    kind: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = Query(default=DEFAULT_QUERY_LIMIT, ge=1, le=MAX_QUERY_LIMIT),
    offset: int = Query(default=0, ge=0),
    cursor: str | None = None,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> EvidenceQueryResponse:
    page_cursor = parse_page_cursor(cursor)
    rows = await asyncio.to_thread(
        db.list_evidence_records,
        _workspace_id(current),
        correlation_id=correlation_id,
        kind=kind,
        since=parse_query_timestamp(since, "since"),
        until=parse_query_timestamp(until, "until"),
        # has_more 판정용으로 1건 더 조회하고 응답은 limit 개로 자름.
        limit=limit + 1,
        offset=offset,
        cursor=page_cursor,
    )
    items = rows[:limit]
    return EvidenceQueryResponse(
        items=[EvidenceRecordItem(**evidence_record(row)) for row in items],
        limit=limit,
        offset=offset,
        has_more=len(rows) > limit,
        next_cursor=next_page_cursor(items, has_more=len(rows) > limit),
    )


@router.get(
    gateway_routes.EVIDENCE_WINDOWS_PATH,
    response_model=EvidenceWindowListResponse,
)
async def list_evidence_windows(
    limit: int = Query(default=DEFAULT_QUERY_LIMIT, ge=1, le=MAX_QUERY_LIMIT),
    offset: int = Query(default=0, ge=0),
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> EvidenceWindowListResponse:
    rows = await asyncio.to_thread(
        db.list_evidence_windows_for_workspace,
        _workspace_id(current),
        limit=limit + 1,
        offset=offset,
    )
    items = rows[:limit]
    return EvidenceWindowListResponse(
        items=[EvidenceWindowSummaryItem(**evidence_window_summary(row)) for row in items],
        limit=limit,
        offset=offset,
        has_more=len(rows) > limit,
    )


@router.get(
    gateway_routes.EVIDENCE_WINDOW_PATH,
    response_model=EvidenceWindowPayloadResponse,
)
async def get_evidence_window_payload(
    evidence_key: str,
    source: str | None = None,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> EvidenceWindowPayloadResponse:
    workspace_id = _workspace_id(current)
    payload = await asyncio.to_thread(
        db.get_evidence_window_payload_for_workspace,
        workspace_id,
        evidence_key,
    )
    if payload is None:
        raise HTTPException(status_code=HTTP_NOT_FOUND, detail="Evidence window not found")

    selected_payload = payload
    selected_source = None
    if source not in (None, ""):
        selected_source = str(source)
        value = payload.get(selected_source)
        if value in (None, {}, []):
            raise HTTPException(
                status_code=HTTP_NOT_FOUND,
                detail=f"Evidence source not found: {selected_source}",
            )
        selected_payload = {selected_source: value}

    return EvidenceWindowPayloadResponse(
        evidence_key=evidence_key,
        workspace_id=workspace_id,
        cluster_id=str(payload["cluster_id"]) if payload.get("cluster_id") else None,
        source=selected_source,
        payload=selected_payload,
    )


@router.get(gateway_routes.RCA_REPORTS_PATH, response_model=RcaReportListResponse)
async def list_rca_reports(
    correlation_id: str | None = None,
    since: str | None = None,
    until: str | None = None,
    limit: int = Query(default=DEFAULT_QUERY_LIMIT, ge=1, le=MAX_QUERY_LIMIT),
    offset: int = Query(default=0, ge=0),
    cursor: str | None = None,
    current: Any = Depends(require_session),
    db: Any = Depends(get_db),
) -> RcaReportListResponse:
    page_cursor = parse_page_cursor(cursor)
    rows = await asyncio.to_thread(
        db.list_rca_report_records,
        _workspace_id(current),
        correlation_id=correlation_id,
        since=parse_query_timestamp(since, "since"),
        until=parse_query_timestamp(until, "until"),
        limit=limit + 1,
        offset=offset,
        cursor=page_cursor,
    )
    items = rows[:limit]
    return RcaReportListResponse(
        items=[RcaReportSummaryItem(**rca_report_summary(row)) for row in items],
        limit=limit,
        offset=offset,
        has_more=len(rows) > limit,
        next_cursor=next_page_cursor(items, has_more=len(rows) > limit),
    )


def parse_query_timestamp(value: str | None, name: str) -> datetime | None:
    if value is None:
        return None
    try:
        # ISO-8601(예: 2026-07-07T10:00:00+00:00, Z suffix 포함) 만 허용.
        return datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise HTTPException(
            status_code=HTTP_UNPROCESSABLE,
            detail=f"{name} {INVALID_TIMESTAMP_DETAIL}",
        ) from exc


def parse_page_cursor(value: str | None) -> tuple[datetime, int] | None:
    if value is None:
        return None
    try:
        padding = "=" * (-len(value) % 4)
        decoded = base64.urlsafe_b64decode(f"{value}{padding}").decode("utf-8")
        payload = json.loads(decoded)
        if not isinstance(payload, dict) or payload.get("v") != CURSOR_VERSION:
            raise ValueError(INVALID_CURSOR_DETAIL)
        try:
            created_at = parse_query_timestamp(str(payload["created_at"]), "cursor.created_at")
        except HTTPException as exc:
            raise ValueError(INVALID_CURSOR_DETAIL) from exc
        row_id = int(payload["id"])
        if created_at is None or row_id < 1:
            raise ValueError(INVALID_CURSOR_DETAIL)
    except (
        BinasciiError,
        KeyError,
        TypeError,
        ValueError,
        UnicodeDecodeError,
        json.JSONDecodeError,
    ) as exc:
        raise HTTPException(status_code=HTTP_UNPROCESSABLE, detail=INVALID_CURSOR_DETAIL) from exc
    return created_at, row_id


def next_page_cursor(rows: list[JsonObject], *, has_more: bool) -> str | None:
    if not has_more or not rows:
        return None
    tail = rows[-1]
    created_at = tail.get("created_at")
    row_id = tail.get("id")
    if created_at in (None, "") or row_id is None:
        return None
    payload = {
        "v": CURSOR_VERSION,
        "created_at": created_at.isoformat()
        if hasattr(created_at, "isoformat")
        else str(created_at),
        "id": int(row_id),
    }
    raw = json.dumps(payload, separators=(",", ":"), sort_keys=True).encode("utf-8")
    return base64.urlsafe_b64encode(raw).decode("ascii").rstrip("=")


def evidence_record(row: JsonObject) -> JsonObject:
    payload = row.get("payload") or {}
    sources = _evidence_source_summaries(payload)
    return {
        "id": row["id"],
        "workspace_id": row["workspace_id"],
        "correlation_id": row["correlation_id"],
        "kind": row["kind"],
        "cluster_id": payload.get("cluster_id") or None,
        "evidence_ref": payload.get("object_ref") or payload.get("evidence_ref") or None,
        "summary": _evidence_summary(payload, sources),
        "sources": sources,
        "created_at": row.get("created_at"),
    }


def evidence_window_summary(row: JsonObject) -> JsonObject:
    payload = row.get("payload") or {}
    return {
        "evidence_key": row["evidence_key"],
        "workspace_id": row["workspace_id"],
        "cluster_id": row.get("cluster_id") or None,
        "source_id": row.get("source_id") or None,
        "window_start": row.get("window_start") or None,
        "agent_id": row.get("agent_id") or None,
        "correlation_id": row.get("correlation_id") or None,
        "sources": _evidence_window_sources(payload),
        "created_at": _string_or_none(row.get("created_at")),
        "updated_at": _string_or_none(row.get("updated_at")),
    }


def _evidence_summary(payload: JsonObject, sources: list[JsonObject]) -> str:
    cluster_id = payload.get("cluster_id")
    labels = [str(item["source"]) for item in sources if item.get("source")]
    if cluster_id and labels:
        return f"{cluster_id}: {', '.join(labels)}"
    if cluster_id:
        return str(cluster_id)
    if labels:
        return ", ".join(labels)
    return "evidence"


def _evidence_window_sources(payload: JsonObject) -> list[str]:
    if not isinstance(payload, dict):
        return []
    known_sources = ("kubernetes", "metrics", "logs", "traces", "metadata")
    return [source for source in known_sources if payload.get(source) not in (None, {}, [])]


def _evidence_source_summaries(payload: JsonObject) -> list[JsonObject]:
    items: list[JsonObject] = []
    for source in ("kubernetes", "metrics", "logs", "traces", "metadata"):
        value = payload.get(source)
        if value in (None, {}, []):
            continue
        items.append(
            {
                "source": source,
                "summary": _source_summary(source, value),
                **_lineage_from_source_value(value),
            }
        )
    return items


def _source_summary(source: str, value: Any) -> str:
    if source == "kubernetes" and isinstance(value, dict):
        pods = _len(value.get("pods"))
        nodes = _len(value.get("nodes"))
        events = _len(value.get("events"))
        parts = []
        if pods is not None:
            parts.append(f"pods={pods}")
        if nodes is not None:
            parts.append(f"nodes={nodes}")
        if events is not None:
            parts.append(f"events={events}")
        return ", ".join(parts) if parts else "kubernetes snapshot"
    if source in {"metrics", "traces"} and isinstance(value, dict):
        results = value.get("results")
        if isinstance(results, dict):
            return f"results={len(results)}"
        return f"{source} snapshot"
    if source == "logs" and isinstance(value, list):
        query_names = sorted(
            {
                str(entry["query_name"])
                for entry in value
                if isinstance(entry, dict) and entry.get("query_name")
            }
        )
        if query_names:
            return f"entries={len(value)}, queries={','.join(query_names[:3])}"
        return f"entries={len(value)}"
    return f"{source} evidence"


def _len(value: Any) -> int | None:
    return len(value) if isinstance(value, list) else None


def _string_or_none(value: Any) -> str | None:
    if value in (None, ""):
        return None
    return value.isoformat() if hasattr(value, "isoformat") else str(value)


def _lineage_from_source_value(value: Any) -> JsonObject:
    if isinstance(value, dict):
        return _lineage_from_value(value)
    if isinstance(value, list):
        for entry in value:
            if isinstance(entry, dict):
                lineage = _lineage_from_value(entry)
                if lineage:
                    return lineage
    return {}


LINEAGE_KEY = "_lineage"
LINEAGE_STRING_FIELDS = (
    "source_version",
    "collector",
    "collector_version",
    "query_version",
    "collected_at",
    "evidence_key",
    "source_id",
    "agent_id",
    "window_start",
)


def _lineage_from_value(value: Any) -> JsonObject:
    if not isinstance(value, dict):
        return {}
    lineage = value.get(LINEAGE_KEY)
    return _lineage_fields(lineage) if isinstance(lineage, dict) else {}


def _lineage_fields(raw: Any) -> JsonObject:
    if not isinstance(raw, dict):
        return {}
    out: JsonObject = {}
    schema_version = raw.get("schema_version")
    if schema_version is not None:
        try:
            out["schema_version"] = int(schema_version)
        except (TypeError, ValueError):
            pass
    for field in LINEAGE_STRING_FIELDS:
        value = raw.get(field)
        if value not in (None, ""):
            out[field] = str(value)
    return out


def _workspace_id(current: Any) -> str:
    return getattr(current, "workspace_id", DEFAULT_WORKSPACE_ID)
