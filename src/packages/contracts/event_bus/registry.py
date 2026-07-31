"""이벤트 타입 레지스트리(전역 계약, 카탈로그).

"어떤 이벤트가 있나"만 담당(정적, 전역 공유 계약).
- @event(SUBJECT): body ↔ 이벤트 매핑.
- describe(): make events 로 한눈에 보는 표.

실제 구독/실행(런타임)은 packages/runtime/ 의 App + dispatch 담당.
이벤트 "정의"는 공유 계약이라 전역, "핸들러"는 서비스별이라 App 소유.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, fields
from typing import Any, Protocol, cast

from packages.contracts.event_bus.subjects import EventSubject


class EventBodyContract(Protocol):
    @classmethod
    def from_body(cls, raw: Mapping[str, Any]) -> Any: ...


@dataclass(frozen=True)
class Subscription:
    """App.on 가 만드는 핸들러 바인딩(subject ↔ body ↔ 함수)."""

    subject: EventSubject
    body_type: type[EventBodyContract]
    fn: Callable[..., Any]
    wants_ctx: bool


class EventRegistry:
    """전역 이벤트 카탈로그(주소록). @event 로 이벤트 타입 등록"""

    def __init__(self) -> None:
        self._defs: dict[EventSubject, type[EventBodyContract]] = {}
        self._handlers: dict[EventSubject, list[tuple[str, str]]] = {}
        self._raw_handlers: list[tuple[str, str]] = []

    def define(
        self, subject: EventSubject
    ) -> Callable[[type[EventBodyContract]], type[EventBodyContract]]:
        def decorator(body_type: type[EventBodyContract]) -> type[EventBodyContract]:
            body_type.__subject__ = subject  # type: ignore[attr-defined]  # 의도된 동적 부착
            self._defs[subject] = body_type
            return body_type

        return decorator

    def note_handler(self, service: str, sub: Subscription) -> None:
        """App 이 자기 핸들러를 카탈로그에 알림(make events 표시용)"""
        binding = (service, sub.fn.__name__)
        handlers = self._handlers.setdefault(sub.subject, [])
        if binding not in handlers:
            handlers.append(binding)

    def note_raw_handler(self, service: str, handler: str) -> None:
        """전체(>) 구독 프로젝터를 카탈로그에 알림"""
        binding = (service, handler)
        if binding not in self._raw_handlers:
            self._raw_handlers.append(binding)

    def describe(self) -> str:
        rows = ["EVENTS (한눈에 보기)", ""]
        for subject, body_type in sorted(self._defs.items()):
            names = ", ".join(f.name for f in fields(cast(type[Any], body_type)))
            handlers = ", ".join(
                f"{service}/{handler}"
                for service, handler in sorted(self._handlers.get(subject, []))
            )
            if not handlers:
                handlers = "-"
            rows.append(f"{subject:<28} {body_type.__name__:<26} by={handlers}  fields=({names})")
        if self._raw_handlers:
            rows.append("")
            rows.append("ALL-EVENT 구독(프로젝터):")
            for service, handler in sorted(self._raw_handlers):
                rows.append(f"  {service}/{handler}  ← 모든 이벤트(>)")
        return "\n".join(rows)


events = EventRegistry()
event = events.define  # @event(SUBJECT): body ↔ 이벤트 선언 데코레이터
