"""카탈로그 API 호출.

MCP 서버는 DB 에 직접 붙지 않는다. 붙으면 API 가 가진 권한 검사와 응답 경계를
전부 우회한다. 같은 데이터를 두 경로로 읽으면 두 경로의 규칙이 갈라지고,
갈라진 뒤에는 어느 쪽이 맞는지 아무도 모른다. 읽기 경로는 하나여야 한다.

그래서 이 모듈에는 DB 드라이버도 접속 문자열도 없다. HTTP 만 있다.

토큰은 교환된 것을 보낸다. 사용자 토큰을 그대로 전달하면 카탈로그 API 가
사용자의 전체 권한으로 동작한다. 교환 실패는 호출 실패다 — 원본 토큰으로
물러서지 않는다. 물러서면 교환이 있으나 마나다.
"""

from __future__ import annotations

from typing import Any, Protocol
from urllib.parse import quote

from .server import TOOLS_BY_NAME
from .session import Session
from .sts import ExchangedToken, TokenExchangeError, TokenExchanger


class HttpTransport(Protocol):
    def get_json(
        self, url: str, *, params: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, dict[str, Any]]: ...


class UpstreamError(Exception):
    def __init__(self, code: str, status: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status = status


class CatalogApiClient:
    def __init__(
        self,
        transport: HttpTransport,
        *,
        base_url: str,
        exchanger: TokenExchanger,
    ) -> None:
        self._transport = transport
        self._base = base_url.rstrip("/")
        self._exchanger = exchanger
        self._token: ExchangedToken | None = None

    def call(
        self, tool_name: str, arguments: dict[str, Any], *, session: Session
    ) -> dict[str, Any]:
        route = TOOLS_BY_NAME.get(tool_name)
        if route is None:
            raise UpstreamError("unknown_tool")

        path = route.path
        for name in route.path_args:
            value = arguments.get(name)
            if not value:
                raise UpstreamError("missing_path_argument")
            path = path.replace("{" + name + "}", quote(str(value), safe=""))

        params = {
            api_name: arguments[arg_name]
            for arg_name, api_name in route.query_map.items()
            if arguments.get(arg_name) is not None
        }

        # 만료 전까지 재사용한다. 매 호출마다 교환하면 도구 200회가 STS 200회가 된다.
        if self._token is None or self._token.is_expired():
            try:
                self._token = self._exchanger.exchange(session.subject_token)
            except TokenExchangeError as exc:
                raise UpstreamError(f"token_exchange_failed:{exc.reason}") from None
        token = self._token

        status, body = self._transport.get_json(
            f"{self._base}{path}",
            params=params,
            headers={
                "Authorization": f"Bearer {token.access_token}",
                "Accept": "application/json",
            },
        )
        if status == 403:
            raise UpstreamError("forbidden", status)
        if status == 404:
            raise UpstreamError("not_found", status)
        if status >= 400:
            # 상위 응답 본문을 그대로 올리지 않는다. 내부 경로·드라이버 메시지가
            # 섞여 있을 수 있고, 모델을 거쳐 사용자에게 그대로 노출된다.
            raise UpstreamError("upstream_error", status)
        return body
