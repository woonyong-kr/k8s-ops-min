"""MCP stdio 전송 — JSON-RPC 2.0 을 줄 단위로 주고받는다.

stdout 은 프로토콜 전용이다. 로그를 stdout 에 쓰면 클라이언트가 그걸 응답으로
파싱하려다 연결이 끊긴다. 감사 로그가 stderr 로 가는 이유가 이것이다.

주체(principal)는 환경에서 받는다. stdio MCP 서버는 사용자 세션마다 새로
기동되므로 프로세스 하나가 곧 주체 하나다. 주체 없이 뜨면 즉시 실패한다 —
익명으로 뜬 뒤 첫 호출에서 실패하면, 그때는 이미 감사 로그에 남길 주체가 없다.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, TextIO

from .client import CatalogApiClient
from .dispatch import ToolCallFailed, call_tool
from .server import API_BASE, list_tools
from .session import AuditLog, Session
from .sts import TokenExchanger

PROTOCOL_VERSION = "2024-11-05"
SERVER_INFO = {"name": "catalog-mcp", "version": "0.1.0"}

DEFAULT_AUDIENCE = os.environ.get("CATALOG_API_AUDIENCE", "urn:kyro:catalog-api")
DEFAULT_SCOPES = frozenset(
    (os.environ.get("CATALOG_MCP_SCOPES") or "catalog:read").split()
)


class UrllibTransport:
    """표준 라이브러리만 쓴다. 이 서버에 DB 드라이버를 들이지 않기 위해서다."""

    def __init__(self, timeout: float = 10.0) -> None:
        self._timeout = timeout

    def get_json(
        self, url: str, *, params: dict[str, Any], headers: dict[str, str]
    ) -> tuple[int, dict[str, Any]]:
        if params:
            url = f"{url}?{urllib.parse.urlencode(params)}"
        req = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return resp.status, json.loads(resp.read().decode("utf-8") or "{}")
        except urllib.error.HTTPError as exc:
            return exc.code, {}
        except (urllib.error.URLError, TimeoutError, json.JSONDecodeError):
            return 599, {}

    def post_form(self, url: str, form: dict[str, str]) -> dict[str, Any]:
        body = urllib.parse.urlencode(form).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=body,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=self._timeout) as resp:
                return json.loads(resp.read().decode("utf-8") or "{}")
        except Exception:
            return {}


def _result(rid: Any, payload: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "result": payload}


def _error(rid: Any, code: int, message: str) -> dict[str, Any]:
    return {"jsonrpc": "2.0", "id": rid, "error": {"code": code, "message": message}}


def _content(payload: dict[str, Any], *, is_error: bool = False) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": json.dumps(payload, ensure_ascii=False)}],
        "isError": is_error,
    }


def handle(
    message: dict[str, Any],
    *,
    session: Session,
    client: CatalogApiClient,
    audit: AuditLog,
) -> dict[str, Any] | None:
    method = message.get("method")
    rid = message.get("id")

    if method == "initialize":
        return _result(
            rid,
            {
                "protocolVersion": PROTOCOL_VERSION,
                "capabilities": {"tools": {}},
                "serverInfo": SERVER_INFO,
            },
        )
    if method in ("notifications/initialized", "initialized"):
        return None
    if method == "ping":
        return _result(rid, {})
    if method == "tools/list":
        return _result(rid, {"tools": list_tools()})
    if method == "tools/call":
        params = message.get("params") or {}
        name = params.get("name")
        args = params.get("arguments") or {}
        if not isinstance(name, str) or not isinstance(args, dict):
            return _result(rid, _content({"error": "bad_request"}, is_error=True))
        try:
            return _result(rid, _content(call_tool(
                name, args, session=session, client=client, audit=audit
            )))
        except ToolCallFailed as exc:
            return _result(rid, _content(exc.to_payload(), is_error=True))
    return _error(rid, -32601, "method not found")


def serve_stdio(
    stdin: TextIO | None = None,
    stdout: TextIO | None = None,
    *,
    session: Session | None = None,
    client: CatalogApiClient | None = None,
    audit: AuditLog | None = None,
) -> int:
    stdin = stdin or sys.stdin
    stdout = stdout or sys.stdout

    if session is None:
        principal = os.environ.get("CATALOG_MCP_PRINCIPAL_SUB")
        token = os.environ.get("CATALOG_MCP_SUBJECT_TOKEN")
        if not principal or not token:
            print(
                "CATALOG_MCP_PRINCIPAL_SUB 와 CATALOG_MCP_SUBJECT_TOKEN 이 필요합니다.",
                file=sys.stderr,
            )
            return 2
        session = Session(principal_sub=principal, subject_token=token)

    audit = audit or AuditLog()
    if client is None:
        transport = UrllibTransport()
        client = CatalogApiClient(
            transport,
            base_url=API_BASE,
            exchanger=TokenExchanger(
                transport,
                token_endpoint=os.environ.get("CATALOG_STS_TOKEN_ENDPOINT", ""),
                audience=DEFAULT_AUDIENCE,
                scopes=DEFAULT_SCOPES,
            ),
        )

    for line in stdin:
        line = line.strip()
        if not line:
            continue
        try:
            message = json.loads(line)
        except json.JSONDecodeError:
            print(json.dumps(_error(None, -32700, "parse error")), file=stdout, flush=True)
            continue
        response = handle(message, session=session, client=client, audit=audit)
        if response is not None:
            print(json.dumps(response, ensure_ascii=False), file=stdout, flush=True)
    return 0
