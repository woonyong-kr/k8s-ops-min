"""검사 결과 적재 키.

한 검사가 한 자산에서 위반을 여러 건 찾는다. 03 은 필드마다, 02 는 누락
필드마다, 08 은 리소스마다다. 유일 제약이 자산 단위면 두 번째 위반부터
앞의 것을 덮어써서 자산당 1건만 남는다.

이 파일은 그 손실이 없는지 본다. "통과·실패를 모두 남긴다"는 설계가 실패
쪽에서 깨지지 않는지 확인하는 자리다.
"""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import text

from domains.datacatalog.checks import _subject_key, _write_result

NOW = datetime(2026, 7, 20, 3, 0, tzinfo=UTC)


def _seed_dag_run(conn, dag_run_id: str = "dag-1") -> str:
    conn.execute(
        text(
            "INSERT INTO catalog_dag_runs (dag_run_id, dag_id, logical_date, status, started_at) "
            "VALUES (:d, 'catalog', :ld, 'SUCCESS', :t) ON CONFLICT DO NOTHING"
        ),
        {"d": dag_run_id, "ld": NOW.date(), "t": NOW},
    )
    return dag_run_id


def _write(conn, dag_run_id, *, subject_key, finding="TYPE_CHANGED", asset="ops.evidence"):
    _write_result(
        conn,
        dag_run_id=dag_run_id,
        check_name="03_schema_drift",
        check_type="SCHEMA_DRIFT",
        asset_id=asset,
        subject_key=subject_key,
        status="failed",
        severity="error",
        finding=finding,
        observed=None,
        expected=None,
        checked_at=NOW,
    )


def test_한_자산의_필드별_위반이_모두_남는다(conn):
    dag = _seed_dag_run(conn)
    for field in ("spec.replicas", "metadata.labels.app", "status.phase"):
        _write(conn, dag, subject_key=field)

    rows = conn.execute(
        text(
            "SELECT subject_key FROM catalog_quality_results "
            "WHERE dag_run_id = :d AND asset_id = 'ops.evidence' ORDER BY subject_key"
        ),
        {"d": dag},
    ).scalars().all()
    assert rows == ["metadata.labels.app", "spec.replicas", "status.phase"]


def test_같은_대상은_덮어쓴다(conn):
    """멱등성은 유지되어야 한다. 재실행이 행을 늘리면 안 된다."""
    dag = _seed_dag_run(conn)
    _write(conn, dag, subject_key="spec.replicas", finding="TYPE_CHANGED")
    _write(conn, dag, subject_key="spec.replicas", finding="FIELD_REMOVED")

    rows = conn.execute(
        text(
            "SELECT finding FROM catalog_quality_results "
            "WHERE dag_run_id = :d AND asset_id = 'ops.evidence'"
        ),
        {"d": dag},
    ).scalars().all()
    assert rows == ["FIELD_REMOVED"]


def test_자산_단위_검사는_대상키가_하나다(conn):
    dag = _seed_dag_run(conn)
    _write_result(
        conn, dag_run_id=dag, check_name="01_source_coverage", check_type="SOURCE_COVERAGE",
        asset_id="-", subject_key="-", status="passed", severity="warning",
        finding=None, observed=None, expected=None, checked_at=NOW,
    )
    count = conn.execute(
        text("SELECT count(*) FROM catalog_quality_results WHERE check_name = '01_source_coverage'")
    ).scalar_one()
    assert count == 1


@pytest.mark.parametrize(
    ("row", "expected"),
    [
        ({"field_path": "spec.replicas"}, "spec.replicas"),
        ({"resource_uid": "uid-7"}, "uid-7"),
        ({"schema_version": 3}, "3"),
        ({"asset_id": "ops.evidence"}, "-"),
        ({}, "-"),
    ],
)
def test_대상키_선정_순서(row, expected):
    assert _subject_key(row) == expected


def test_대상키는_길이를_자른다():
    assert len(_subject_key({"field_path": "a" * 1000})) == 256
