"""MCP 신뢰 경계 검증.

catalog-api-mcp.md 의 검증 표가 주장하는 것을 여기서 실제로 확인한다.
표에 있는데 여기 없는 항목은 문서에서 "설계안(미구현)"으로 내려야 한다.
"""

from __future__ import annotations

import io
import json
from typing import Any

import pytest

from services.catalog_mcp.client import CatalogApiClient
from services.catalog_mcp.dispatch import ToolCallFailed, call_tool
from services.catalog_mcp.session import AuditLog, Session
from services.catalog_mcp.sts import (
    GRANT_TYPE,
    TokenExchangeError,
    TokenExchanger,
)

AUDIENCE = "urn:kyro:catalog-api"
SCOPES = frozenset({"catalog:read"})


class FakeTransport:
    """STS 와 카탈로그 API 를 대신한다. 오간 요청을 전부 기록한다."""

    def __init__(
        self,
        *,
        sts_response: dict[str, Any] | None = None,
        api_status: int = 200,
        api_body: dict[str, Any] | None = None,
    ) -> None:
        self.sts_response = sts_response if sts_response is not None else {
            "access_token": "downscoped-token",
            "audience": AUDIENCE,
            "scope": "catalog:read",
            "expires_in": 120,
        }
        self.api_status = api_status
        self.api_body = api_body if api_body is not None else {"data": [], "page": {}}
        self.sts_calls: list[dict[str, str]] = []
        self.api_calls: list[dict[str, Any]] = []

    def post_form(self, url: str, form: dict[str, str]) -> dict[str, Any]:
        self.sts_calls.append(form)
        return self.sts_response

    def get_json(
        self, url: str, *, params: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        self.api_calls.append({"url": url, "params": params, "headers": headers})
        return self.api_status, self.api_body


def make(transport: FakeTransport, *, principal: str, token: str, budget: int = 200):
    exchanger = TokenExchanger(
        transport, token_endpoint="https://sts.test/token", audience=AUDIENCE, scopes=SCOPES
    )
    client = CatalogApiClient(transport, base_url="https://api.test/v1/catalog", exchanger=exchanger)
    session = Session(principal_sub=principal, subject_token=token, call_budget=budget)
    audit = AuditLog(stream=io.StringIO())
    return client, session, audit


# 1. DB 직접 접근 -----------------------------------------------------------


def test_mcp_모듈은_db_드라이버를_들이지_않는다():
    """MCP 가 DB 에 직접 붙으면 API 의 권한 검사와 응답 경계를 통째로 우회한다."""
    import importlib
    import pkgutil

    import services.catalog_mcp as pkg

    banned = ("sqlalchemy", "psycopg", "asyncpg", "sqlite3")
    for mod in pkgutil.iter_modules(pkg.__path__):
        source = importlib.import_module(f"services.catalog_mcp.{mod.name}").__file__
        assert source is not None
        text = open(source, encoding="utf-8").read()
        for name in banned:
            assert f"import {name}" not in text, f"{mod.name} 가 {name} 를 들인다"


def test_mcp_는_db_접속정보를_읽지_않는다(monkeypatch):
    monkeypatch.setenv("CATALOG_DATABASE_URL", "postgresql://should-not-be-read/db")
    transport = FakeTransport()
    client, session, audit = make(transport, principal="alice", token="alice-token")
    call_tool("list_data_sources", {}, session=session, client=client, audit=audit)
    sent = json.dumps(transport.api_calls)
    assert "should-not-be-read" not in sent


# 2. 토큰 교환 (RFC 8693) ---------------------------------------------------


def test_토큰_교환은_rfc8693_grant_type_을_쓴다():
    transport = FakeTransport()
    client, session, audit = make(transport, principal="alice", token="alice-token")
    call_tool("list_data_sources", {}, session=session, client=client, audit=audit)
    form = transport.sts_calls[0]
    assert form["grant_type"] == GRANT_TYPE
    assert form["subject_token"] == "alice-token"
    assert form["audience"] == AUDIENCE
    assert form["scope"] == "catalog:read"


@pytest.mark.parametrize(
    ("response", "reason"),
    [
        ({"access_token": "t", "audience": "urn:other", "scope": "catalog:read",
          "expires_in": 60}, "audience_mismatch"),
        ({"access_token": "t", "audience": AUDIENCE, "scope": "catalog:read catalog:write",
          "expires_in": 60}, "scope_widened"),
        ({"access_token": "t", "audience": AUDIENCE, "scope": "catalog:read",
          "expires_in": 86400}, "ttl_out_of_range"),
        ({"access_token": "", "audience": AUDIENCE, "scope": "catalog:read",
          "expires_in": 60}, "no_access_token"),
    ],
)
def test_교환_결과가_요청보다_넓으면_거부한다(response, reason):
    """검증 없는 교환은 교환하지 않은 것과 같다."""
    transport = FakeTransport(sts_response=response)
    exchanger = TokenExchanger(
        transport, token_endpoint="https://sts.test/token", audience=AUDIENCE, scopes=SCOPES
    )
    with pytest.raises(TokenExchangeError) as exc:
        exchanger.exchange("alice-token")
    assert exc.value.reason == reason


def test_교환에_실패하면_원본_토큰으로_물러서지_않는다():
    transport = FakeTransport(sts_response={"access_token": "t", "audience": "urn:other",
                                            "scope": "catalog:read", "expires_in": 60})
    client, session, audit = make(transport, principal="alice", token="alice-token")
    with pytest.raises(ToolCallFailed) as exc:
        call_tool("list_data_sources", {}, session=session, client=client, audit=audit)
    assert exc.value.code == "token_exchange_failed"
    assert transport.api_calls == []  # 상위 호출 자체가 일어나지 않는다


# 3. 토큰 전달 (주체가 바뀌지 않는가) ---------------------------------------


def test_alice_세션은_alice_토큰을_교환해_전달한다():
    """API 가 거부하는지가 아니라 MCP 가 올바른 주체를 전달하는지를 본다.

    전자만 보면 정적 서비스 계정을 쓰고 있어도 통과한다.
    """
    transport = FakeTransport(sts_response={
        "access_token": "downscoped-for-alice", "audience": AUDIENCE,
        "scope": "catalog:read", "expires_in": 60,
    })
    client, session, audit = make(transport, principal="alice", token="alice-token")
    call_tool("search_assets", {"query": "metric"}, session=session, client=client, audit=audit)

    assert transport.sts_calls[0]["subject_token"] == "alice-token"
    assert transport.api_calls[0]["headers"]["Authorization"] == "Bearer downscoped-for-alice"


def test_bob_자산_요청은_상위_403_을_그대로_실패로_만든다():
    transport = FakeTransport(api_status=403)
    client, session, audit = make(transport, principal="alice", token="alice-token")
    with pytest.raises(ToolCallFailed) as exc:
        call_tool(
            "get_asset_schema", {"asset_id": "bob.private_asset"},
            session=session, client=client, audit=audit,
        )
    assert exc.value.code == "forbidden"
    assert transport.api_calls[0]["headers"]["Authorization"] == "Bearer downscoped-token"


def test_두_주체는_서로_다른_토큰을_전달한다():
    transport = FakeTransport()
    for principal, token in (("alice", "alice-token"), ("bob", "bob-token")):
        client, session, audit = make(transport, principal=principal, token=token)
        call_tool("list_data_sources", {}, session=session, client=client, audit=audit)
    assert [c["subject_token"] for c in transport.sts_calls] == ["alice-token", "bob-token"]


# 4. 세션 단위 열거 ---------------------------------------------------------


def test_예산을_넘기면_거부하고_retry_after_를_준다():
    transport = FakeTransport()
    client, session, audit = make(transport, principal="alice", token="t", budget=3)
    for _ in range(3):
        call_tool("list_data_sources", {}, session=session, client=client, audit=audit)
    with pytest.raises(ToolCallFailed) as exc:
        call_tool("list_data_sources", {}, session=session, client=client, audit=audit)
    assert exc.value.code == "session_budget_exhausted"
    assert exc.value.retry_after == 3600
    assert len(transport.api_calls) == 3


def test_인자_검증_실패는_예산을_깎지_않는다():
    """검증보다 예산을 먼저 깎으면 잘못된 인자만으로 남의 예산을 태울 수 있다."""
    transport = FakeTransport()
    client, session, audit = make(transport, principal="alice", token="t", budget=2)
    for _ in range(5):
        with pytest.raises(ToolCallFailed):
            call_tool("list_data_sources", {"없는인자": 1},
                      session=session, client=client, audit=audit)
    assert session.calls_made == 0
    call_tool("list_data_sources", {}, session=session, client=client, audit=audit)
    assert session.remaining == 1


# 5. 내부 오류 노출 ---------------------------------------------------------


def test_상위_오류에_내부_정보가_섞이지_않는다():
    transport = FakeTransport(
        api_status=500,
        api_body={"detail": 'psycopg.OperationalError at /srv/app/db.py:42 host=10.0.3.7'},
    )
    client, session, audit = make(transport, principal="alice", token="t")
    with pytest.raises(ToolCallFailed) as exc:
        call_tool("list_data_sources", {}, session=session, client=client, audit=audit)
    payload = json.dumps(exc.value.to_payload())
    assert exc.value.code == "upstream_error"
    for leak in ("psycopg", "/srv/app", "10.0.3.7", "Traceback"):
        assert leak not in payload
    assert exc.value.correlation_id.startswith("cid-")


# 6. 감사 로그 --------------------------------------------------------------


def test_감사_로그에_주체가_남는다():
    """session_id 만 남기면 누구를 대신해 읽었는지 답할 수 없다."""
    transport = FakeTransport()
    client, session, audit = make(transport, principal="alice@example.com", token="t")
    call_tool("list_data_sources", {}, session=session, client=client, audit=audit)
    entry = audit.records[-1]
    assert entry["principal_sub"] == "alice@example.com"
    assert entry["session_id"].startswith("mcp-")
    assert entry["outcome"] == "ok"


def test_거부된_시도도_감사_로그에_남는다():
    transport = FakeTransport(api_status=403)
    client, session, audit = make(transport, principal="alice", token="t")
    with pytest.raises(ToolCallFailed):
        call_tool("get_asset_schema", {"asset_id": "x"},
                  session=session, client=client, audit=audit)
    assert audit.records[-1]["outcome"] == "forbidden"
    assert audit.records[-1]["principal_sub"] == "alice"


# 7. 도구 인자와 API 파라미터가 맞는가 --------------------------------------


def test_도구_인자가_api_쿼리_이름으로_옮겨진다():
    """이름이 어긋난 채 방치되면 도구는 한 번도 성공하지 못한다."""
    transport = FakeTransport()
    client, session, audit = make(transport, principal="alice", token="t")
    call_tool("search_assets", {"query": "metric", "limit": 10},
              session=session, client=client, audit=audit)
    assert transport.api_calls[0]["params"] == {"q": "metric", "limit": 10}


def test_경로_인자는_이스케이프된다():
    transport = FakeTransport()
    client, session, audit = make(transport, principal="alice", token="t")
    call_tool("get_asset_lineage", {"asset_id": "ops/normalized evidence"},
              session=session, client=client, audit=audit)
    assert transport.api_calls[0]["url"].endswith("/assets/ops%2Fnormalized%20evidence/lineage")
