"""카탈로그 조회 API.

설계 근거: docs/portfolio/catalog-api-mcp.md

모든 응답이 같은 envelope 을 쓴다. data / page / evidence 셋이다.

evidence 가 항상 붙는 이유: run_status 가 PARTIAL 이면 이 조회 결과 자체가
부분 데이터라는 뜻이다. 카탈로그가 "이슈 0건"이라고 답해도 그 검사가 일부
소스를 못 봤다면 0건의 의미가 다르다. 01번 문서의 원칙이 한 단계 위로
올라간다. 수집 결과의 완전성뿐 아니라 검사 결과의 완전성도 전달한다.
"""

from __future__ import annotations

import base64
import json
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Path, Query
from sqlalchemy import text
from sqlalchemy.engine import Connection

from packages.contracts.catalog.reason_codes import Reason, ReasonCode, bound_reasons

router = APIRouter(prefix="/v1/catalog", tags=["catalog"])

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


# 커서 ---------------------------------------------------------------------


def encode_cursor(offset: int) -> str:
    return base64.urlsafe_b64encode(json.dumps({"offset": offset}).encode()).decode()


def decode_cursor(cursor: str | None) -> int:
    """상한만 두고 페이지네이션이 없으면 상한 너머 데이터에 영원히 접근할 수 없다.

    02번 문서에서 잘림을 숨기지 않기로 했는데, 숨기지 않는 것과
    도달할 수 있게 하는 것은 다르다.
    """
    if not cursor:
        return 0
    try:
        payload = json.loads(base64.urlsafe_b64decode(cursor.encode()))
        offset = int(payload["offset"])
    except Exception as exc:  # noqa: BLE001
        raise HTTPException(422, {"code": "invalid_parameter", "field": "cursor"}) from exc
    if offset < 0:
        raise HTTPException(422, {"code": "invalid_parameter", "field": "cursor"})
    return offset


# envelope ------------------------------------------------------------------


def latest_evidence(conn: Connection) -> dict[str, Any]:
    """가장 최근 검사 실행의 근거.

    검사가 한 번도 돌지 않았으면 NEVER_RUN 이다. "아직 검사 안 됨"과
    "검사했는데 이슈 없음"을 같은 응답으로 내보내지 않는다.
    """
    row = conn.execute(
        text(
            """
            SELECT dag_run_id, logical_date, status, finished_at
            FROM catalog_dag_runs
            ORDER BY logical_date DESC, started_at DESC
            LIMIT 1
            """
        )
    ).mappings().first()

    if row is None:
        codes, truncated = bound_reasons([Reason(ReasonCode.NEVER_RUN)])
        return {
            "run_id": None,
            "logical_date": None,
            "run_status": "NEVER_RUN",
            "checked_at": None,
            "reason_codes": codes,
            "reason_codes_truncated": truncated,
        }

    reasons: list[Reason] = []
    if row["status"] in ("PARTIAL", "INCOMPLETE", "FAILED"):
        failed = conn.execute(
            text(
                "SELECT source_id FROM catalog_collection_runs "
                "WHERE dag_run_id = :d AND status IN ('FAILED','TRUNCATED')"
            ),
            {"d": row["dag_run_id"]},
        ).scalars().all()
        reasons = [Reason(ReasonCode.SOURCE_FAILED, source=s) for s in failed]

    codes, truncated = bound_reasons(reasons)
    return {
        "run_id": row["dag_run_id"],
        "logical_date": str(row["logical_date"]),
        "run_status": row["status"],
        "checked_at": row["finished_at"].isoformat() if row["finished_at"] else None,
        "reason_codes": codes,
        "reason_codes_truncated": truncated,
    }


def envelope(
    conn: Connection, rows: list[dict[str, Any]], *, limit: int, offset: int, total: int
) -> dict[str, Any]:
    returned = len(rows)
    truncated = offset + returned < total
    page: dict[str, Any] = {
        "limit": limit,
        "returned_count": returned,
        "total_estimated": total,
        "truncated": truncated,
    }
    if truncated:
        page["next_cursor"] = encode_cursor(offset + returned)
    return {"data": rows, "page": page, "evidence": latest_evidence(conn)}


# 의존성 --------------------------------------------------------------------


def get_connection() -> Connection:  # pragma: no cover - 앱 배선에서 주입된다
    raise NotImplementedError("애플리케이션 배선에서 오버라이드한다")


Conn = Annotated[Connection, Depends(get_connection)]
Limit = Annotated[int, Query(ge=1, le=MAX_LIMIT)]


# 엔드포인트 ----------------------------------------------------------------


@router.get("/sources")
def list_sources(conn: Conn, limit: Limit = DEFAULT_LIMIT, cursor: str | None = None):
    offset = decode_cursor(cursor)
    total = conn.execute(text("SELECT count(*) FROM catalog_data_sources")).scalar_one()
    rows = conn.execute(
        text(
            "SELECT source_id, name, source_type, owner, enabled, "
            "       collection_interval_seconds "
            "FROM catalog_data_sources ORDER BY source_id LIMIT :l OFFSET :o"
        ),
        {"l": limit, "o": offset},
    ).mappings().all()
    return envelope(conn, [dict(r) for r in rows], limit=limit, offset=offset, total=total)


@router.get("/assets")
def search_assets(
    conn: Conn,
    q: str | None = Query(None, max_length=128),
    source: str | None = Query(None, max_length=64),
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    """자산 검색.

    classification 을 필터로 노출하지만 인가 입력으로는 쓰지 않는다.
    그 한계는 07번 문서에 적어 두었다.
    """
    offset = decode_cursor(cursor)
    where = "WHERE (:q IS NULL OR qualified_name ILIKE '%' || :q || '%') " \
            "AND (:s IS NULL OR source_id = :s)"
    params = {"q": q, "s": source, "l": limit, "o": offset}
    total = conn.execute(
        text(f"SELECT count(*) FROM catalog_data_assets {where}"), params
    ).scalar_one()
    rows = conn.execute(
        text(
            f"SELECT asset_id, qualified_name, asset_type, source_id, owner, "
            f"       classification, current_schema_version, freshness_sla_seconds "
            f"FROM catalog_data_assets {where} ORDER BY qualified_name LIMIT :l OFFSET :o"
        ),
        params,
    ).mappings().all()
    return envelope(conn, [dict(r) for r in rows], limit=limit, offset=offset, total=total)


@router.get("/assets/{asset_id}")
def get_asset(conn: Conn, asset_id: Annotated[str, Path(max_length=256)]):
    row = conn.execute(
        text("SELECT * FROM catalog_data_assets WHERE asset_id = :a"), {"a": asset_id}
    ).mappings().first()
    if row is None:
        raise HTTPException(404, {"code": "not_found"})
    return envelope(conn, [dict(row)], limit=1, offset=0, total=1)


@router.get("/assets/{asset_id}/schema")
def get_asset_schema(conn: Conn, asset_id: Annotated[str, Path(max_length=256)]):
    """계약 이력.

    append-only 인 schema_observations 를 읽는다. asset_fields 는 upsert 되므로
    이전 세대 해시가 남지 않는다.
    """
    rows = conn.execute(
        text(
            """
            SELECT schema_version, schema_hash, first_seen_run_id, first_seen_at
            FROM catalog_schema_observations
            WHERE asset_id = :a
            ORDER BY first_seen_at
            """
        ),
        {"a": asset_id},
    ).mappings().all()
    if not rows:
        raise HTTPException(404, {"code": "not_found"})
    data = [
        {
            "schema_version": r["schema_version"],
            "schema_hash": r["schema_hash"],
            "first_seen_run_id": r["first_seen_run_id"],
            "first_seen_at": r["first_seen_at"].isoformat(),
        }
        for r in rows
    ]
    return envelope(conn, data, limit=len(data), offset=0, total=len(data))


@router.get("/assets/{asset_id}/lineage")
def get_asset_lineage(conn: Conn, asset_id: Annotated[str, Path(max_length=256)]):
    """리니지 역추적.

    간선이 언제 확인됐는지 함께 반환한다. run_id 를 저장만 하고 조인하지
    않으면 "이 관계가 언제 확인된 것인가"에 답할 수 없다.
    """
    sql = (
        __import__("pathlib").Path(__file__).resolve().parents[3]
        / "sql" / "quality" / "91_lineage_trace.sql"
    ).read_text(encoding="utf-8")
    rows = conn.execute(
        text(sql), {"asset_id": asset_id, "logical_ts": _now(conn)}
    ).mappings().all()
    data = [dict(r) for r in rows]
    return envelope(conn, data, limit=len(data), offset=0, total=len(data))


@router.get("/quality/issues")
def list_quality_issues(
    conn: Conn,
    severity: str | None = Query(None, pattern="^(error|warning)$"),
    limit: Limit = DEFAULT_LIMIT,
    cursor: str | None = None,
):
    offset = decode_cursor(cursor)
    where = "WHERE status = 'failed' AND (:sev IS NULL OR severity = :sev)"
    params = {"sev": severity, "l": limit, "o": offset}
    total = conn.execute(
        text(f"SELECT count(*) FROM catalog_quality_results {where}"), params
    ).scalar_one()
    rows = conn.execute(
        text(
            f"SELECT result_id, check_name, check_type, asset_id, severity, finding, "
            f"       observed_value, expected_value, first_seen_dag_run_id, checked_at "
            f"FROM catalog_quality_results {where} "
            f"ORDER BY severity, checked_at DESC LIMIT :l OFFSET :o"
        ),
        params,
    ).mappings().all()
    data = [{**dict(r), "checked_at": r["checked_at"].isoformat()} for r in rows]
    return envelope(conn, data, limit=limit, offset=offset, total=total)


@router.get("/runs")
def list_runs(conn: Conn, limit: Limit = DEFAULT_LIMIT, cursor: str | None = None):
    """실행 이력과 소스별 지표.

    별도 지표 저장소를 두지 않았다. 실행 이력이 이미 지표의 원천이다.
    """
    offset = decode_cursor(cursor)
    total = conn.execute(text("SELECT count(*) FROM catalog_dag_runs")).scalar_one()
    rows = conn.execute(
        text(
            """
            SELECT d.dag_run_id, d.logical_date, d.status, d.started_at, d.finished_at,
                   count(c.run_id)                                          AS source_count,
                   count(*) FILTER (WHERE c.status IN ('SUCCESS','NO_DATA')) AS healthy,
                   count(*) FILTER (WHERE c.status = 'FAILED')               AS failed,
                   sum(c.attempt)                                            AS attempts
            FROM catalog_dag_runs d
            LEFT JOIN catalog_collection_runs c ON c.dag_run_id = d.dag_run_id
            GROUP BY d.dag_run_id, d.logical_date, d.status, d.started_at, d.finished_at
            ORDER BY d.logical_date DESC LIMIT :l OFFSET :o
            """
        ),
        {"l": limit, "o": offset},
    ).mappings().all()
    data = [
        {
            **dict(r),
            "logical_date": str(r["logical_date"]),
            "started_at": r["started_at"].isoformat(),
            "finished_at": r["finished_at"].isoformat() if r["finished_at"] else None,
        }
        for r in rows
    ]
    return envelope(conn, data, limit=limit, offset=offset, total=total)


def _now(conn: Connection):
    return conn.execute(text("SELECT now()")).scalar_one()
