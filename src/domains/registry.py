"""도메인 합성 루트 — 코어 repo + 자동 발견된 도메인 repo 로 Database 구성.

팀원이 domains/<새도메인>/{tables,repo}.py 를 추가하면 자동 포함됨(packages/ 0 수정).
아직 domains/ 로 이전 전인 도메인(rca·command·auth·projection)은 임시로 명시 —
이전되면 _PENDING 에서 빠지고 자동 발견으로 흡수됨.
"""

from __future__ import annotations

import importlib
import pkgutil
from types import ModuleType
from typing import TYPE_CHECKING

import domains
from domains.audit.repository import AuditLogRepository
from domains.command.repository import AgentCommandRepository
from domains.identity.repository import IdentityAccessRepository
from domains.rca.repository import RcaRepository
from domains.scm.repository import PullRequestRepository
from domains.target.repository import TargetAgentRepository
from packages.storage.engine import DatabaseConnection
from packages.storage.repositories.dead_letter import DeadLetterRepository
from packages.storage.repositories.event import EventRepository
from packages.storage.repositories.outbox import OutboxRepository


def _domain_modules(suffix: str) -> list[ModuleType]:
    mods: list[ModuleType] = []
    for info in pkgutil.iter_modules(domains.__path__, f"{domains.__name__}."):
        if not info.ispkg:
            continue
        module_name = f"{info.name}.{suffix}"
        try:
            mods.append(importlib.import_module(module_name))
        except ModuleNotFoundError as exc:
            if exc.name != module_name:
                raise
            # 그 도메인에 models/repository 가 없을 수 있음.
    return mods


def load_domain_tables() -> None:
    """domains/*/tables.py 임포트 → Base.metadata 에 자동 등록."""
    _domain_modules("models")


def load_domain_events() -> None:
    """domains/*/events.py 임포트 → @event 데코레이터가 EventRegistry 에 자동 등록.

    이벤트 카탈로그(make events)나 전 도메인 계약이 필요한 곳(합성 루트)에서 호출.
    새 도메인 이벤트는 events.py 생성만으로 카탈로그에 포함됨.
    """
    _domain_modules("events")


def load_domain_tools() -> None:
    """domains/*/tools.py 임포트 → @ai.tool 데코레이터가 ToolRegistry 에 자동 등록.

    LLM 도구가 필요한 서비스(chat-worker 등)가 부팅 시 호출.
    새 도메인 도구는 tools.py 생성만으로 레지스트리에 포함됨.
    """
    _domain_modules("tools")


def _discovered_repositories() -> tuple[type, ...]:
    """domains/*/repo.py 에서 정의된 DatabaseConnection 하위 repo 수집."""
    found: list[type] = []
    for mod in _domain_modules("repository"):
        for obj in vars(mod).values():
            if (
                isinstance(obj, type)
                and issubclass(obj, DatabaseConnection)
                and obj.__module__ == mod.__name__
                and obj not in found
            ):
                found.append(obj)
    return tuple(found)


_CORE = (EventRepository, DeadLetterRepository, OutboxRepository)
# 모든 도메인 repo 는 domains/ 에서 자동 발견됨.

if TYPE_CHECKING:
    # 타입 검사용 스텁 — 코어+pending repo 계약을 선언(런타임엔 아래 type() 이
    # 도메인 repo 까지 동적 합성). Database store 메서드 타입체커 인식용.
    class Database(  # noqa: D101
        EventRepository,
        DeadLetterRepository,
        OutboxRepository,
        IdentityAccessRepository,
        AgentCommandRepository,
        RcaRepository,
        PullRequestRepository,
        AuditLogRepository,
        TargetAgentRepository,
    ): ...
else:
    Database = type("Database", _CORE + _discovered_repositories(), {})
