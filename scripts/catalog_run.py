#!/usr/bin/env python3
"""카탈로그 배치를 1회 실행한다. Airflow 없이도 같은 순서로 돈다.

DAG(dags/catalog_reconciliation_daily.py)는 이 모듈의 함수를 호출한다.
오케스트레이션과 로직을 분리해서, Airflow 를 띄우지 않고도 파이프라인을
검증할 수 있게 했다.

사용:
    python scripts/catalog_run.py --logical-date 2026-07-30
    python scripts/catalog_run.py --logical-date 2026-07-30 --fail loki
    python scripts/catalog_run.py --logical-date 2026-07-30 --fixture drift
    python scripts/catalog_run.py --logical-date 2026-07-30 --fail-downstream
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))

from sqlalchemy import create_engine, text  # noqa: E402

from domains.datacatalog import checks, pipeline  # noqa: E402

DEFAULT_DSN = os.environ.get(
    "CATALOG_DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5433/catalog"
)
SOURCES = ("kubernetes", "prometheus", "loki", "tempo")


def seed(conn, fixture_root: Path) -> None:
    """원천과 자산 등록 정보를 넣는다. 여러 번 실행해도 늘지 않는다."""
    for row in json.loads((fixture_root.parent / "sources.json").read_text(encoding="utf-8")):
        conn.execute(
            text(
                """
                INSERT INTO catalog_data_sources
                    (source_id, name, source_type, owner, collection_interval_seconds, enabled)
                VALUES (:source_id, :name, :source_type, :owner,
                        :collection_interval_seconds, :enabled)
                ON CONFLICT (source_id) DO UPDATE
                    SET enabled = EXCLUDED.enabled, name = EXCLUDED.name
                """
            ),
            row,
        )
    for row in json.loads((fixture_root.parent / "assets.json").read_text(encoding="utf-8")):
        conn.execute(
            text(
                """
                INSERT INTO catalog_data_assets
                    (asset_id, source_id, qualified_name, asset_type, freshness_sla_seconds,
                     classification, owner, current_schema_version)
                VALUES (:asset_id, :source_id, :qualified_name, :asset_type,
                        :freshness_sla_seconds, :classification, :owner, :current_schema_version)
                ON CONFLICT (asset_id) DO UPDATE
                    SET current_schema_version = EXCLUDED.current_schema_version
                """
            ),
            row,
        )
        # 등록 계약. 첫 정상 실행의 관측 계약을 그대로 등록 계약으로 삼는다.


def register_contract(conn, fixture_root: Path) -> None:
    """정상 fixture 의 계약을 등록 계약으로 넣는다.

    실제 운영에서는 사람이 등록하지만, 여기서는 정상 상태를 기준선으로 잡는다.
    이 기준선이 있어야 드리프트를 '기준 대비 변화'로 판정할 수 있다.
    """
    from domains.datacatalog.schema_contract import contract_from_payload, schema_hash

    merged: dict[str, dict[str, str | None]] = {}
    for source in SOURCES:
        path = fixture_root / f"{source}.json"
        if not path.exists():
            continue
        for item in json.loads(path.read_text(encoding="utf-8")):
            target = merged.setdefault(item["asset_id"], {})
            for field_path, dtype in contract_from_payload(item["payload"]):
                if field_path not in target or target[field_path] is None:
                    target[field_path] = dtype

    for asset_id, contract in merged.items():
        digest = schema_hash(sorted(contract.items()))
        for field_path, dtype in sorted(contract.items()):
            conn.execute(
                text(
                    """
                    INSERT INTO catalog_asset_fields
                        (asset_id, schema_version, field_path, data_type, required, schema_hash)
                    VALUES (:a, 1, :p, :d, :req, :h)
                    ON CONFLICT (asset_id, schema_version, field_path) DO UPDATE
                        SET data_type = EXCLUDED.data_type, schema_hash = EXCLUDED.schema_hash
                    """
                ),
                {"a": asset_id, "p": field_path, "d": dtype, "req": field_path == "name",
                 "h": digest},
            )


def run_once(
    dsn: str,
    logical_date: str,
    fixture: str,
    fail_sources: frozenset[str],
    fail_downstream: bool,
    today: str,
) -> dict[str, object]:
    engine = create_engine(dsn, future=True)
    fixture_root = ROOT / "fixtures" / "catalog" / fixture
    archive_root = ROOT / ".catalog-archive"
    now = datetime.now(UTC)
    logical_ts = datetime.fromisoformat(f"{logical_date}T03:00:00+00:00")

    with engine.begin() as conn:
        seed(conn, fixture_root)
        register_contract(conn, ROOT / "fixtures" / "catalog" / "normal")
        dag_run_id = pipeline.open_dag_run(conn, logical_date, now)

        # 1. extract — 소스별 독립. 하나가 실패해도 나머지는 진행한다.
        outcomes = [
            pipeline.extract_source(
                source, logical_date, fixture_root, archive_root,
                today=today, fail_sources=fail_sources,
            )
            for source in SOURCES
        ]
        pipeline.upsert_collection_runs(conn, dag_run_id, logical_date, outcomes, now)

        # 2. archive — trigger_rule 이 아니라 여기서 조건을 판단한다.
        if not any(o.ok for o in outcomes):
            pipeline.resolve_dag_run_status(
                conn, dag_run_id, downstream_complete=False, finished_at=now
            )
            return {"dag_run_id": dag_run_id, "status": "FAILED", "reason": "no source produced output"}
        pipeline.archive_raw_snapshot(conn, dag_run_id, logical_date, outcomes, archive_root)

        # 3~5. normalize → drift 이력 → load → lineage
        downstream_complete = False
        if not fail_downstream:
            pipeline.normalize_asset_schema(conn, dag_run_id, logical_date, outcomes, logical_ts)
            pipeline.record_schema_observations(conn, dag_run_id, outcomes, logical_ts)
            pipeline.load_catalog(conn, logical_date, outcomes, now)
            pipeline.record_lineage(conn, dag_run_id, outcomes)
            downstream_complete = True

        # 6. 상태 확정 — 항상 실행된다.
        status = pipeline.resolve_dag_run_status(
            conn, dag_run_id, downstream_complete=downstream_complete, finished_at=now
        )

        # 7. 검사
        findings = checks.run_checks(conn, dag_run_id, logical_date, logical_ts, now)

    return {
        "dag_run_id": dag_run_id,
        "status": status,
        "sources": {o.source_id: o.status for o in outcomes},
        "findings": len(findings),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--logical-date", required=True)
    parser.add_argument("--dsn", default=DEFAULT_DSN)
    parser.add_argument("--fixture", default="normal", choices=("normal", "drift"))
    parser.add_argument("--fail", default="", help="강제 실패시킬 소스 (쉼표 구분)")
    parser.add_argument("--fail-downstream", action="store_true")
    parser.add_argument("--today", default=datetime.now(UTC).date().isoformat())
    args = parser.parse_args()

    result = run_once(
        args.dsn,
        args.logical_date,
        args.fixture,
        frozenset(s for s in args.fail.split(",") if s),
        args.fail_downstream,
        args.today,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
