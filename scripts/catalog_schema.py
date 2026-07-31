#!/usr/bin/env python3
"""카탈로그 테이블을 생성한다."""
from __future__ import annotations
import os, sys
from pathlib import Path
ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
from sqlalchemy import create_engine  # noqa: E402
from packages.storage.base import Base  # noqa: E402
import domains.datacatalog.models  # noqa: E402,F401

DSN = os.environ.get("CATALOG_DATABASE_URL", "postgresql+psycopg://postgres@127.0.0.1:5433/catalog")
tables = [t for n, t in Base.metadata.tables.items() if n.startswith("catalog_")]
Base.metadata.create_all(create_engine(DSN, future=True), tables=tables)
print(f"{len(tables)}개 테이블 준비 완료")
