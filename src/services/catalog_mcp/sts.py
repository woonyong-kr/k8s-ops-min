"""RFC 8693 토큰 교환.

MCP 서버는 사용자 토큰을 그대로 카탈로그 API 에 넘기지 않는다. 그 토큰은
사용자가 가진 모든 권한을 담고 있고, MCP 가 필요한 것은 카탈로그 읽기 하나다.
중간 서버가 상위 권한 토큰을 들고 있으면 그 서버가 뚫렸을 때 피해 범위가
사용자의 전체 권한이 된다.

그래서 STS 에 토큰을 제출하고 대상(audience)과 범위(scope)를 좁힌 토큰을
받아서 그것만 하위로 보낸다.

교환 결과를 반드시 검증한다. STS 가 요청한 것보다 넓은 토큰을 돌려줄 수 있고,
그걸 그대로 쓰면 좁히려던 목적이 사라진다. 검증 없는 교환은 교환하지 않은
것과 같다.
"""

from __future__ import annotations

import time
from dataclasses import dataclass
from typing import Any, Protocol

GRANT_TYPE = "urn:ietf:params:oauth:grant-type:token-exchange"
TOKEN_TYPE_ACCESS = "urn:ietf:params:oauth:token-type:access_token"

# 교환된 토큰의 수명 상한. STS 가 이보다 긴 토큰을 주면 거부한다.
# 하위 전달용 토큰은 한 번의 도구 호출을 넘겨서 살아 있을 이유가 없다.
MAX_TTL_SECONDS = 300


class TokenExchangeError(Exception):
    """교환 실패 또는 교환 결과 검증 실패."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


@dataclass(frozen=True)
class ExchangedToken:
    access_token: str
    audience: str
    scopes: frozenset[str]
    expires_in: int
    issued_at: float

    def is_expired(self, *, now: float | None = None) -> bool:
        return (now or time.time()) >= self.issued_at + self.expires_in


class StsTransport(Protocol):
    """STS 에 POST 하는 최소 인터페이스. 테스트에서 가짜로 갈아 끼운다."""

    def post_form(self, url: str, form: dict[str, str]) -> dict[str, Any]: ...


class TokenExchanger:
    def __init__(
        self,
        transport: StsTransport,
        *,
        token_endpoint: str,
        audience: str,
        scopes: frozenset[str],
        max_ttl_seconds: int = MAX_TTL_SECONDS,
    ) -> None:
        self._transport = transport
        self._endpoint = token_endpoint
        self._audience = audience
        self._scopes = frozenset(scopes)
        self._max_ttl = max_ttl_seconds

    def exchange(self, subject_token: str) -> ExchangedToken:
        if not subject_token:
            raise TokenExchangeError("missing_subject_token")

        payload = self._transport.post_form(
            self._endpoint,
            {
                "grant_type": GRANT_TYPE,
                "subject_token": subject_token,
                "subject_token_type": TOKEN_TYPE_ACCESS,
                "requested_token_type": TOKEN_TYPE_ACCESS,
                "audience": self._audience,
                "scope": " ".join(sorted(self._scopes)),
            },
        )
        return self._verify(payload)

    def _verify(self, payload: dict[str, Any]) -> ExchangedToken:
        """교환 결과를 검증한다. 셋 중 하나라도 어긋나면 쓰지 않는다."""
        token = payload.get("access_token")
        if not isinstance(token, str) or not token:
            raise TokenExchangeError("no_access_token")

        # 대상 — 우리가 요청한 대상이 아니면 다른 서비스용 토큰이다.
        audience = payload.get("audience") or payload.get("aud")
        if isinstance(audience, list):
            audience = audience[0] if len(audience) == 1 else None
        if audience != self._audience:
            raise TokenExchangeError("audience_mismatch")

        # 범위 — 요청한 것보다 넓으면 좁히려던 목적이 사라진다.
        granted = frozenset((payload.get("scope") or "").split())
        if not granted or not granted.issubset(self._scopes):
            raise TokenExchangeError("scope_widened")

        # 수명 — 상한을 넘으면 유출 시 피해 시간이 길어진다.
        try:
            expires_in = int(payload.get("expires_in", 0))
        except (TypeError, ValueError):
            raise TokenExchangeError("bad_expires_in") from None
        if expires_in <= 0 or expires_in > self._max_ttl:
            raise TokenExchangeError("ttl_out_of_range")

        return ExchangedToken(
            access_token=token,
            audience=audience,
            scopes=granted,
            expires_in=expires_in,
            issued_at=time.time(),
        )
