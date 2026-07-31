#!/usr/bin/env python3
"""검사가 실제로 잡는지 보여주는 고장 시연.

정상 데이터로만 시험하면 무엇이든 통과한다고 답하는 검사도 통과한다.
그래서 고장을 만들고, 검사가 그것만 잡는지 확인한다.

    python3 scripts/catalog_demo.py fail-source
    python3 scripts/catalog_demo.py drift
    python3 scripts/catalog_demo.py duplicate
"""
from __future__ import annotations

import argparse
import os
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import create_engine, text  # noqa: E402

from domains.datacatalog.checks import load_sql  # noqa: E402

DSN = os.environ.get(
    "CATALOG_DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5433/catalog"
)
TODAY = datetime.now(UTC).date()


def run_batch(logical_date: str, *extra: str) -> None:
    cmd = [sys.executable, "scripts/catalog_run.py", "--logical-date", logical_date, *extra]
    env = {**os.environ, "PYTHONPATH": "src"}
    out = subprocess.run(cmd, cwd=ROOT, capture_output=True, text=True, env=env)
    sys.stdout.write(out.stdout or out.stderr)


def check(name: str, logical_date: str) -> list[dict]:
    engine = create_engine(DSN, future=True)
    sql = load_sql(name)
    with engine.begin() as conn:
        params = {
            "logical_date": logical_date,
            "logical_ts": datetime.fromisoformat(f"{logical_date}T03:00:00+00:00"),
            "run_id": conn.execute(
                text(
                    "SELECT run_id FROM catalog_collection_runs "
                    "WHERE logical_date = CAST(:d AS date) ORDER BY run_id LIMIT 1"
                ),
                {"d": logical_date},
            ).scalar(),
            "asset_id": conn.execute(
                text("SELECT asset_id FROM catalog_data_assets LIMIT 1")
            ).scalar(),
        }
        bind = {k: v for k, v in params.items() if f":{k}" in sql}
        return [dict(r) for r in conn.execute(text(sql), bind).mappings().all()]


def demo_fail_source() -> None:
    date = (TODAY - timedelta(days=1)).isoformat()
    print(f"■ {date} 배치를 Loki 실패 상태로 실행합니다\n")
    run_batch(date, "--fail", "loki")
    engine = create_engine(DSN, future=True)
    with engine.begin() as conn:
        rows = conn.execute(
            text(
                "SELECT source_id, status FROM catalog_collection_runs "
                "WHERE logical_date = CAST(:d AS date) ORDER BY source_id"
            ),
            {"d": date},
        ).all()
        dag = conn.execute(
            text("SELECT status FROM catalog_dag_runs WHERE logical_date = CAST(:d AS date)"),
            {"d": date},
        ).scalar()
    print("\n소스별 상태")
    for source, status in rows:
        print(f"  {source:12} {status}")
    print(f"\nDAG 상태  {dag}")
    print("\n실패한 소스만 다시 돌리려면:")
    print(f"  python3 scripts/catalog_run.py --logical-date {date} --only loki")


def demo_drift() -> None:
    date = TODAY.isoformat()
    print(f"■ 원천 필드 타입이 바뀐 fixture 로 {date} 배치를 실행합니다\n")
    run_batch(date, "--fixture", "drift")
    for name in ("03_schema_drift", "04_unversioned_change"):
        rows = check(name, date)
        print(f"\n{name}  위반 {len(rows)}건")
        for r in rows[:5]:
            print(f"  {r}")


def demo_duplicate() -> None:
    date = TODAY.isoformat()
    print(f"■ 같은 관측을 다른 실행이 다시 적재한 상황을 만듭니다\n")
    engine = create_engine(DSN, future=True)
    with engine.begin() as conn:
        before = len(check("08_duplicate_candidates", date))
        print(f"주입 전  중복 적재 후보 {before}건")
        injected = conn.execute(
            text(
                """
                INSERT INTO catalog_normalized_evidence
                  (evidence_id, asset_id, run_id, cluster_id, source_id, resource_uid,
                   collection_status, observed_at, ingested_at)
                SELECT evidence_id || '__demo', asset_id, run_id || '__retry',
                       cluster_id, source_id, resource_uid, collection_status,
                       observed_at + interval '3 second', now()
                FROM catalog_normalized_evidence
                WHERE evidence_id NOT LIKE '%__demo'
                ORDER BY ingested_at DESC
                LIMIT 5
                ON CONFLICT DO NOTHING
                RETURNING evidence_id
                """
            )
        ).all()
        print(f"주입      관측 시각이 3초 다른 행 {len(injected)}건")
        print("          (유일 제약 (cluster, source, uid, observed_at) 은 이걸 못 막습니다)")
    after = check("08_duplicate_candidates", date)
    print(f"\n주입 후  중복 적재 후보 {len(after)}건")
    for r in after[:5]:
        print(f"  {r['cluster_id']} / {r['source_id']} / {r['resource_uid']} "
              f"— 실행 {r['run_count']}회")
    with engine.begin() as conn:
        conn.execute(text("DELETE FROM catalog_normalized_evidence WHERE evidence_id LIKE '%__demo'"))
    print("\n주입한 행을 지웠습니다. 검사 결과는 다시 0건이 됩니다.")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("scenario", choices=("fail-source", "drift", "duplicate"))
    args = parser.parse_args()
    {"fail-source": demo_fail_source, "drift": demo_drift, "duplicate": demo_duplicate}[
        args.scenario
    ]()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
