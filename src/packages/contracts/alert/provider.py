"""알림 전송 전략 계약."""

from __future__ import annotations

from typing import Any, Protocol


class AlertProvider(Protocol):
    """alert.requested 를 채널(Slack/Email/PagerDuty 등)로 전송하는 전략.

    alert 는 domains.alert.events.AlertRequestedBody, 반환은 AlertDispatchedBody 임
    (레이어 규칙상 packages 는 domains 를 import 못 해 구조적 시그니처로 둠).
    """

    async def dispatch(self, alert: Any) -> Any: ...
