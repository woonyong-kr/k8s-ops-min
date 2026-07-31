"""알림 채널 HTTP 전송 공용 함수 — router 사전검증과 worker 실제 발송이 같이 사용."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

import httpx

from domains.alert.events import AlertRequestedBody
from packages.config.settings import env
from packages.security.outbound_url import (
    HostResolver,
    UnsafeOutboundUrlError,
    validate_outbound_url,
)

ALERT_HTTP_TIMEOUT_SECONDS_ENV = "ALERT_HTTP_TIMEOUT_SECONDS"
DEFAULT_ALERT_HTTP_TIMEOUT_SECONDS = "10"


@dataclass(frozen=True)
class AlertDeliveryResult:
    delivered: bool
    status_code: int | None = None
    error: str = ""


async def post_alert_webhook(
    url: str,
    alert: AlertRequestedBody | dict[str, Any],
    *,
    transport: httpx.AsyncBaseTransport | None = None,
    resolver: HostResolver | None = None,
) -> AlertDeliveryResult:
    timeout = float(env(ALERT_HTTP_TIMEOUT_SECONDS_ENV, DEFAULT_ALERT_HTTP_TIMEOUT_SECONDS))
    payload = alert.to_body() if isinstance(alert, AlertRequestedBody) else dict(alert)
    try:
        # 전송 라이브러리가 DNS를 다시 조회하므로 TOCTOU를 완전히 없애지는 못한다.
        # 그래도 저장 시점 결과를 신뢰하지 않고 매 전송 직전에 같은 검증을 반복한다.
        await validate_outbound_url(url, resolver=resolver)
    except UnsafeOutboundUrlError:
        return AlertDeliveryResult(delivered=False, error="unsafe_webhook_url")
    try:
        async with httpx.AsyncClient(
            timeout=timeout,
            transport=transport,
            follow_redirects=False,
        ) as client:
            response = await client.post(url, json=payload)
        if response.status_code >= 400:
            return AlertDeliveryResult(
                delivered=False,
                status_code=response.status_code,
                error=f"HTTP {response.status_code}",
            )
        return AlertDeliveryResult(delivered=True, status_code=response.status_code)
    except httpx.TimeoutException:
        return AlertDeliveryResult(delivered=False, error="timeout")
    except httpx.HTTPError as exc:
        return AlertDeliveryResult(delivered=False, error=type(exc).__name__)
