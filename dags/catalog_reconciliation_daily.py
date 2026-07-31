"""카탈로그 정합성 재검사 DAG.

로직은 domains/datacatalog/pipeline.py 에 있고 여기서는 오케스트레이션만 한다.
Airflow 를 띄우지 않고도 파이프라인을 검증할 수 있게 분리했다.
scripts/catalog_run.py 가 같은 순서를 Airflow 없이 실행한다.

설계 근거: docs/portfolio/05-airflow-pipeline.md
"""

from __future__ import annotations

import os
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

from airflow.decorators import dag, task
from airflow.exceptions import AirflowFailException
from airflow.utils.trigger_rule import TriggerRule

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import create_engine  # noqa: E402

from domains.datacatalog import checks, pipeline  # noqa: E402

SOURCES = ("kubernetes", "prometheus", "loki", "tempo")
DSN = os.environ.get(
    "CATALOG_DATABASE_URL", "postgresql+psycopg://postgres@postgres:5432/catalog"
)
FIXTURE_ROOT = ROOT / "fixtures" / "catalog" / "normal"
ARCHIVE_ROOT = Path(os.environ.get("CATALOG_ARCHIVE_ROOT", ROOT / ".catalog-archive"))


def _engine():
    return create_engine(DSN, future=True)


@dag(
    dag_id="catalog_reconciliation_daily",
    schedule="0 3 * * *",
    start_date=datetime(2026, 7, 1, tzinfo=UTC),
    # catchup 이 이 DAG 를 Airflow 로 만든 이유다. 끄면 CronJob 과 차이가 없다.
    catchup=True,
    # backfill 중 여러 날짜가 동시에 같은 자산을 upsert 하는 것을 막는다.
    max_active_runs=1,
    default_args={
        "retries": 2,
        "retry_delay": timedelta(minutes=2),
    },
    tags=["datacatalog"],
)
def catalog_reconciliation_daily():
    @task
    def extract(source_id: str, **context) -> dict:
        """소스 하나를 추출한다.

        예외를 삼키지 않는다. 실패하면 실패해야 Airflow 재시도가 동작한다.
        상태 확정은 재시도가 모두 소진된 뒤 resolve_dag_run_status 가 한다.
        """
        logical_date = context["logical_date"].date().isoformat()
        outcome = pipeline.extract_source(
            source_id,
            logical_date,
            FIXTURE_ROOT,
            ARCHIVE_ROOT,
            today=datetime.now(UTC).date().isoformat(),
        )
        with _engine().begin() as conn:
            dag_run_id = pipeline.open_dag_run(conn, logical_date, datetime.now(UTC))
            pipeline.upsert_collection_runs(
                conn, dag_run_id, logical_date, [outcome], datetime.now(UTC)
            )
        if outcome.status == "FAILED":
            raise AirflowFailException(f"{source_id} extract failed")
        # XCom 에는 payload 를 넣지 않는다. 메타데이터 DB 에 들어가고 크기 제한이 있다.
        return {"source_id": source_id, "status": outcome.status}

    @task(trigger_rule=TriggerRule.ALL_DONE)
    def archive_and_load(**context) -> str:
        """원본 보관부터 적재까지.

        trigger_rule 이 all_done 인 이유:
        none_failed_min_one_success 는 "하나라도 성공하면 진행"이 아니라
        "실패가 하나도 없을 때만 진행"이다. 그 규칙을 쓰면 loki 하나가
        실패했을 때 이 task 이하가 통째로 건너뛰어지고, PARTIAL 이
        기록될 기회조차 없어진다.

        one_success 도 답이 아니다. upstream 완료를 기다리지 않아서
        아직 재시도 중인 소스가 빠진 스냅샷을 저장하게 된다.

        규칙 이름에 조건을 맡기지 않고 본문에서 판단한다.
        """
        logical_date = context["logical_date"].date().isoformat()
        now = datetime.now(UTC)
        logical_ts = context["logical_date"]

        outcomes = [
            pipeline.extract_source(
                s, logical_date, FIXTURE_ROOT, ARCHIVE_ROOT,
                today=datetime.now(UTC).date().isoformat(),
            )
            for s in SOURCES
        ]
        if not any(o.ok for o in outcomes):
            raise AirflowFailException("no source produced output")

        with _engine().begin() as conn:
            dag_run_id = pipeline.open_dag_run(conn, logical_date, now)
            pipeline.upsert_collection_runs(conn, dag_run_id, logical_date, outcomes, now)
            pipeline.archive_raw_snapshot(
                conn, dag_run_id, logical_date, outcomes, ARCHIVE_ROOT
            )
            pipeline.normalize_asset_schema(
                conn, dag_run_id, logical_date, outcomes, logical_ts
            )
            pipeline.record_schema_observations(conn, dag_run_id, outcomes, logical_ts)
            pipeline.load_catalog(conn, logical_date, outcomes, now)
            pipeline.record_lineage(conn, dag_run_id, outcomes)
        return dag_run_id

    @task(trigger_rule=TriggerRule.ALL_DONE)
    def resolve_status(dag_run_id: str | None, **context) -> str:
        """상태를 확정한다. 성공·실패·건너뜀 무관하게 항상 실행된다.

        extract 상태만 읽으면 안 된다. downstream 이 실패해서 아무것도
        적재하지 않은 실행이 SUCCESS 로 남는다.
        """
        logical_date = context["logical_date"].date().isoformat()
        now = datetime.now(UTC)
        with _engine().begin() as conn:
            resolved = dag_run_id or pipeline.open_dag_run(conn, logical_date, now)
            return pipeline.resolve_dag_run_status(
                conn, resolved, downstream_complete=dag_run_id is not None, finished_at=now
            )

    @task
    def publish_quality_report(**context) -> int:
        logical_date = context["logical_date"].date().isoformat()
        now = datetime.now(UTC)
        with _engine().begin() as conn:
            dag_run_id = pipeline.dag_run_id_for(pipeline.DAG_ID, logical_date)
            findings = checks.run_checks(
                conn, dag_run_id, logical_date, context["logical_date"], now
            )
        return len(findings)

    extracted = extract.expand(source_id=list(SOURCES))
    loaded = archive_and_load()
    extracted >> loaded
    resolved = resolve_status(loaded)
    resolved >> publish_quality_report()


catalog_reconciliation_daily()
