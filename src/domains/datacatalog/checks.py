"""정합성 검사 실행기.

sql/quality/ 의 질의를 읽어 실행하고 결과를 catalog_quality_results 에 적재한다.
검사 기준을 Python 이 아니라 SQL 에 둔 이유는 06번 문서에 있다.

Python 은 질의를 실행하고 결과를 적재하는 역할만 한다.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from pathlib import Path
from typing import Any

from sqlalchemy import text
from sqlalchemy.engine import Connection

from domains.datacatalog.models import CHECK_SEVERITY

SQL_ROOT = Path(__file__).resolve().parents[3] / "sql" / "quality"

# 검사 파일과 검사 종류의 대응. 90번대는 조회 도구라 여기 없다.
CHECK_FILES: dict[str, str] = {
    "01_source_coverage": "SOURCE_COVERAGE",
    "02_required_field": "REQUIRED_FIELD",
    "03_schema_drift": "SCHEMA_DRIFT",
    "04_unversioned_change": "SCHEMA_DRIFT",
    "05_freshness": "FRESHNESS",
    "06_lineage_break": "LINEAGE_BREAK",
    "07_run_consistency": "RUN_CONSISTENCY",
    "08_duplicate_candidates": "RUN_CONSISTENCY",
}

LOOKUP_FILES = ("90_latest_state", "91_lineage_trace")


def load_sql(name: str) -> str:
    return (SQL_ROOT / f"{name}.sql").read_text(encoding="utf-8")


def _bind_params(sql: str, params: dict[str, Any]) -> dict[str, Any]:
    """질의가 실제로 쓰는 바인드만 넘긴다."""
    return {k: v for k, v in params.items() if f":{k}" in sql}


def run_checks(
    conn: Connection,
    dag_run_id: str,
    logical_date: str,
    logical_ts: datetime,
    checked_at: datetime,
) -> list[dict[str, Any]]:
    """검사 8개를 실행하고 결과를 적재한다.

    위반이 0건이어도 통과 결과를 남긴다. 실패만 저장하면 "검사를 안 한 것"과
    "검사했는데 통과한 것"을 구분할 수 없다. 01번 문서의 빈 목록 문제와 같다.
    """
    params = {
        "logical_date": logical_date,
        "logical_ts": logical_ts,
        "run_id": None,  # 03번 검사가 쓰는 소스별 run_id 는 아래에서 채운다
    }
    findings: list[dict[str, Any]] = []

    for name, check_type in CHECK_FILES.items():
        sql = load_sql(name)
        severity = CHECK_SEVERITY[check_type]

        if ":run_id" in sql:
            run_ids = conn.execute(
                text("SELECT run_id FROM catalog_collection_runs WHERE dag_run_id = :d"),
                {"d": dag_run_id},
            ).scalars().all()
            rows: list[Any] = []
            for run_id in run_ids:
                rows.extend(
                    conn.execute(
                        text(sql), _bind_params(sql, {**params, "run_id": run_id})
                    ).mappings().all()
                )
        else:
            rows = list(
                conn.execute(text(sql), _bind_params(sql, params)).mappings().all()
            )

        if not rows:
            _write_result(
                conn,
                dag_run_id=dag_run_id,
                check_name=name,
                check_type=check_type,
                asset_id="-",
                status="passed",
                severity=severity,
                finding=None,
                observed=None,
                expected=None,
                checked_at=checked_at,
            )
            continue

        for row in rows:
            asset_id = row.get("asset_id") or row.get("source_id") or row.get("dag_run_id")
            finding = row.get("finding") or row.get("drift_type") or check_type
            first_seen = _first_seen_dag_run(
                conn, check_type, str(asset_id), str(finding), dag_run_id
            )
            _write_result(
                conn,
                dag_run_id=dag_run_id,
                check_name=name,
                check_type=check_type,
                asset_id=str(asset_id) if asset_id is not None else "-",
                status="failed",
                severity=severity,
                finding=str(finding),
                observed=_stringify(row, ("observed_type", "observed_value", "staleness_seconds",
                                          "duplicate_count", "hash_variants", "violation_count")),
                expected=_stringify(row, ("declared_type", "expected_value",
                                          "freshness_sla_seconds")),
                checked_at=checked_at,
                first_seen=first_seen,
            )
            findings.append(dict(row))

    return findings


def _stringify(row: Any, keys: tuple[str, ...]) -> str | None:
    for key in keys:
        value = row.get(key)
        if value is not None:
            return str(value)
    return None


def _first_seen_dag_run(
    conn: Connection, check_type: str, asset_id: str, finding: str, dag_run_id: str
) -> str:
    """이 위반이 처음 발생한 실행을 찾는다.

    이 값이 없으면 한 번 발생한 영구 위반이 이후 모든 실행을 정합성 위반으로
    만든다. 일주일이면 전부 붉어지고 그 뒤로는 아무도 안 본다.
    """
    previous = conn.execute(
        text(
            """
            SELECT COALESCE(MIN(first_seen_dag_run_id), MIN(dag_run_id))
            FROM catalog_quality_results
            WHERE check_type = :c AND status = 'failed'
              AND asset_id = :a
              AND finding IS NOT DISTINCT FROM :f
            """
        ),
        {"c": check_type, "a": asset_id, "f": finding},
    ).scalar()
    return previous or dag_run_id


def _write_result(
    conn: Connection,
    *,
    dag_run_id: str,
    check_name: str,
    check_type: str,
    asset_id: str,
    status: str,
    severity: str,
    finding: str | None,
    observed: str | None,
    expected: str | None,
    checked_at: datetime,
    first_seen: str | None = None,
) -> None:
    key = f"{dag_run_id}/{check_name}/{asset_id}"
    conn.execute(
        text(
            """
            INSERT INTO catalog_quality_results
                (result_id, dag_run_id, check_name, check_type, asset_id, status, severity,
                 finding, observed_value, expected_value, first_seen_dag_run_id, checked_at)
            VALUES (:rid, :d, :cn, :c, :a, :s, :sev, :f, :obs, :exp, :first, :t)
            ON CONFLICT (dag_run_id, check_name, asset_id) DO UPDATE
                SET status = EXCLUDED.status,
                    severity = EXCLUDED.severity,
                    finding = EXCLUDED.finding,
                    observed_value = EXCLUDED.observed_value,
                    expected_value = EXCLUDED.expected_value,
                    checked_at = EXCLUDED.checked_at
            """
        ),
        {
            "rid": f"qr-{uuid.uuid5(uuid.NAMESPACE_URL, key)}",
            "d": dag_run_id,
            "cn": check_name,
            "c": check_type,
            "a": asset_id,
            "s": status,
            "sev": severity,
            "f": finding,
            "obs": observed,
            "exp": expected,
            "first": first_seen or dag_run_id,
            "t": checked_at,
        },
    )
