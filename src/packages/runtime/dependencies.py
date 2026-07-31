"""FastAPI 공유 의존성(DI) — app.state 의 공유 객체를 라우터에 주입.

도메인 router 가 클로저 대신 Depends 로 db·events 를 받음. gateway 가
app.state.db/events 를 세팅하고, 각 도메인 router 는 이 provider 만 의존함.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from fastapi import Request

if TYPE_CHECKING:
    from packages.runtime.gateway import ApiEventGateway
    from packages.runtime.operation_events import OperationEventBroker
    from packages.storage.database import Database


def get_db(request: Request) -> Database:
    return request.app.state.db


def get_events(request: Request) -> ApiEventGateway:
    return request.app.state.events


def get_operation_events(request: Request) -> OperationEventBroker:
    return request.app.state.operation_events


def get_timeline_fanout(request: Request) -> Any:
    """Return the app-owned Timeline wake-up provider without a package→domain edge.

    The concrete fan-out belongs to ``domains.timeline``; this generic runtime
    dependency only retrieves the object the gateway placed in app state.
    Domain routers validate the protocol they require at their own boundary.
    """
    return request.app.state.timeline_fanout


def get_dashboard_ready_fanout(request: Request) -> Any:
    """Return the app-owned Home snapshot-completion wake-up provider."""

    return request.app.state.dashboard_ready_fanout
