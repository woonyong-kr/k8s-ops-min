"""MCP stdio 프로토콜.

서버가 실제로 JSON-RPC 를 말하는지 확인한다. 도구 스키마만 있고 프로토콜이
없으면 그건 MCP 서버가 아니라 스키마 파일이다.
"""

from __future__ import annotations

import io
import json

from test_mcp_trust_boundary import AUDIENCE, SCOPES, FakeTransport

from services.catalog_mcp.client import CatalogApiClient
from services.catalog_mcp.session import AuditLog, Session
from services.catalog_mcp.stdio import PROTOCOL_VERSION, serve_stdio
from services.catalog_mcp.sts import TokenExchanger


def run(lines: list[dict], *, transport: FakeTransport | None = None) -> list[dict]:
    transport = transport or FakeTransport()
    exchanger = TokenExchanger(
        transport, token_endpoint="https://sts.test/token", audience=AUDIENCE, scopes=SCOPES
    )
    client = CatalogApiClient(transport, base_url="https://api.test/v1/catalog", exchanger=exchanger)
    stdin = io.StringIO("\n".join(json.dumps(m) for m in lines) + "\n")
    stdout = io.StringIO()
    code = serve_stdio(
        stdin,
        stdout,
        session=Session(principal_sub="alice", subject_token="alice-token"),
        client=client,
        audit=AuditLog(stream=io.StringIO()),
    )
    assert code == 0
    return [json.loads(line) for line in stdout.getvalue().splitlines() if line.strip()]


def test_initialize_핸드셰이크():
    out = run([{"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}])
    assert out[0]["id"] == 1
    assert out[0]["result"]["protocolVersion"] == PROTOCOL_VERSION
    assert out[0]["result"]["serverInfo"]["name"] == "catalog-mcp"
    assert "tools" in out[0]["result"]["capabilities"]


def test_tools_list_가_선언된_도구를_모두_돌려준다():
    out = run([{"jsonrpc": "2.0", "id": 2, "method": "tools/list"}])
    tools = out[0]["result"]["tools"]
    assert {t["name"] for t in tools} == {
        "list_data_sources", "search_assets", "get_asset_schema",
        "get_asset_lineage", "list_quality_issues", "get_run_status",
        "get_resource_state",
    }
    for t in tools:
        assert t["inputSchema"]["additionalProperties"] is False


def test_tools_call_이_실제로_상위를_호출한다():
    transport = FakeTransport(api_body={"data": [{"source_id": "loki", "name": "Loki"}]})
    out = run(
        [{"jsonrpc": "2.0", "id": 3, "method": "tools/call",
          "params": {"name": "list_data_sources", "arguments": {}}}],
        transport=transport,
    )
    payload = json.loads(out[0]["result"]["content"][0]["text"])
    assert out[0]["result"]["isError"] is False
    assert payload["returned_count"] == 1
    assert transport.api_calls[0]["url"].endswith("/sources")


def test_실패는_isError_로_표시되고_스택이_없다():
    out = run(
        [{"jsonrpc": "2.0", "id": 4, "method": "tools/call",
          "params": {"name": "없는도구", "arguments": {}}}],
        transport=FakeTransport(),
    )
    assert out[0]["result"]["isError"] is True
    payload = json.loads(out[0]["result"]["content"][0]["text"])
    assert payload["error"] == "unknown_tool"
    assert "Traceback" not in json.dumps(out[0])


def test_initialized_통지에는_응답하지_않는다():
    out = run([
        {"jsonrpc": "2.0", "method": "notifications/initialized"},
        {"jsonrpc": "2.0", "id": 5, "method": "ping"},
    ])
    assert len(out) == 1 and out[0]["id"] == 5


def test_깨진_json_은_연결을_끊지_않는다():
    transport = FakeTransport()
    exchanger = TokenExchanger(
        transport, token_endpoint="https://sts.test/token", audience=AUDIENCE, scopes=SCOPES
    )
    client = CatalogApiClient(transport, base_url="https://api.test/v1/catalog", exchanger=exchanger)
    stdout = io.StringIO()
    serve_stdio(
        io.StringIO('{ 깨진\n{"jsonrpc":"2.0","id":9,"method":"ping"}\n'),
        stdout,
        session=Session(principal_sub="alice", subject_token="t"),
        client=client,
        audit=AuditLog(stream=io.StringIO()),
    )
    out = [json.loads(x) for x in stdout.getvalue().splitlines() if x.strip()]
    assert out[0]["error"]["code"] == -32700
    assert out[1]["id"] == 9


def test_알_수_없는_메서드는_32601():
    out = run([{"jsonrpc": "2.0", "id": 6, "method": "resources/list"}])
    assert out[0]["error"]["code"] == -32601


def test_주체_없이는_기동하지_않는다(monkeypatch):
    """익명으로 뜬 뒤 실패하면 감사 로그에 남길 주체가 없다."""
    monkeypatch.delenv("CATALOG_MCP_PRINCIPAL_SUB", raising=False)
    monkeypatch.delenv("CATALOG_MCP_SUBJECT_TOKEN", raising=False)
    assert serve_stdio(io.StringIO(""), io.StringIO()) == 2
