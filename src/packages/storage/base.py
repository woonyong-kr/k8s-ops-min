"""SQLAlchemy 선언 베이스 + 공용 컬럼 헬퍼(순환참조 없는 단일 출처).

도메인 테이블(domains/<d>/tables.py)과 코어 테이블(schema.py)이 같은 Base·metadata
를 공유하도록 여기서 정의. 둘 다 이 모듈만 의존 → 순환 import 없음.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Text, func
from sqlalchemy.dialects.postgresql import JSONB, TIMESTAMP
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column


class Base(DeclarativeBase):
    pass


metadata = Base.metadata


def text_column() -> Mapped[str]:
    return mapped_column(Text, nullable=False)


def jsonb_column() -> Mapped[dict[str, Any]]:
    return mapped_column(JSONB, nullable=False)


def created_at_column() -> Mapped[Any]:
    return mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())


def updated_at_column() -> Mapped[Any]:
    return mapped_column(TIMESTAMP(timezone=True), nullable=False, server_default=func.now())
