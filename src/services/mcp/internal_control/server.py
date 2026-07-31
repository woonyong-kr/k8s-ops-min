from __future__ import annotations

import asyncio
import json
import sys
from dataclasses import dataclass
from typing import Any, TextIO

from packages.security.log_lines import redact_log_line
from services.mcp.internal_control import limits as mcp_limits
from services.mcp.internal_control.api_client import ManagementApiClient, ManagementApiError
from services.mcp.internal_control.config import McpConfigurationError, load_settings
from services.mcp.internal_control.tools import (
    ToolInputError,
    ToolRegistry,
    default_tool_registry,
    dumps_tool_result,
)

JSONRPC_VERSION = "2.0"
SUPPORTED_PROTOCOL_VERSION = "2025-11-25"
SERVER_NAME = "opsia-internal-control"
SERVER_VERSION = "0.1.0"
MAX_JSONRPC_LINE_BYTES = mcp_limits.MAX_JSONRPC_LINE_BYTES

PARSE_ERROR = -32700
INVALID_REQUEST = -32600
METHOD_NOT_FOUND = -32601
INVALID_PARAMS = -32602
INTERNAL_ERROR = -32603


@dataclass
class JsonRpcError(Exception):
    code: int
    message: str
    data: Any | None = None


class InternalControlMcpServer:
    def __init__(self, registry: ToolRegistry, client: ManagementApiClient) -> None:
        self.registry = registry
        self.client = client

    async def handle(self, message: dict[str, Any]) -> dict[str, Any] | None:
        request_id = message.get("id")
        try:
            if message.get("jsonrpc") != JSONRPC_VERSION:
                raise JsonRpcError(INVALID_REQUEST, "jsonrpc must be 2.0")
            method = message.get("method")
            if not isinstance(method, str) or not method:
                raise JsonRpcError(INVALID_REQUEST, "method is required")
            if request_id is None and method.startswith("notifications/"):
                return None
            params = message["params"] if "params" in message else {}
            result = await self._dispatch(method, params)
            return _success(request_id, result)
        except JsonRpcError as exc:
            return _error(request_id, exc.code, exc.message, exc.data)
        except Exception:
            return _error(request_id, INTERNAL_ERROR, "internal MCP server error")

    async def _dispatch(self, method: str, params: Any) -> dict[str, Any]:
        if not isinstance(params, dict):
            raise JsonRpcError(INVALID_PARAMS, "params must be an object")
        if method == "initialize":
            return self._initialize(params)
        if method == "ping":
            return {}
        if method == "tools/list":
            return {"tools": self.registry.list_tools()}
        if method == "tools/call":
            return await self._call_tool(params)
        raise JsonRpcError(METHOD_NOT_FOUND, f"unknown method: {method}")

    def _initialize(self, params: dict[str, Any]) -> dict[str, Any]:
        requested = params.get("protocolVersion")
        protocol_version = (
            requested if requested == SUPPORTED_PROTOCOL_VERSION else SUPPORTED_PROTOCOL_VERSION
        )
        return {
            "protocolVersion": protocol_version,
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {
                "name": SERVER_NAME,
                "title": "Opsia Internal Control",
                "version": SERVER_VERSION,
            },
            "instructions": (
                "Opsia control-plane tools. Read tools issue authenticated GET "
                "requests. Write tools default to dry-run proposals and only submit "
                "existing Gateway POST/PATCH requests when OPSIA_MCP_ENABLE_WRITES=true "
                "and the call is explicitly confirmed, relying on Gateway "
                "authentication, RBAC, audit, and workflow state checks."
            ),
        }

    async def _call_tool(self, params: dict[str, Any]) -> dict[str, Any]:
        name = params.get("name")
        arguments = params.get("arguments", {})
        if arguments is None:
            arguments = {}
        if not isinstance(name, str) or not name:
            raise JsonRpcError(INVALID_PARAMS, "tool name is required")
        if not isinstance(arguments, dict):
            raise JsonRpcError(INVALID_PARAMS, "tool arguments must be an object")
        try:
            result = await self.registry.call(name, arguments, self.client)
            return _tool_result(result, is_error=False)
        except ToolInputError as exc:
            return _tool_result(
                {"error": "invalid_tool_input", "detail": redact_log_line(str(exc))},
                is_error=True,
            )
        except ManagementApiError as exc:
            return _tool_result(
                {
                    "error": "management_api_error",
                    "status_code": exc.status_code,
                    "detail": exc.detail,
                },
                is_error=True,
            )


async def run_stdio(
    server: InternalControlMcpServer,
    *,
    stdin: TextIO = sys.stdin,
    stdout: TextIO = sys.stdout,
) -> None:
    while True:
        line = await asyncio.to_thread(stdin.readline)
        if line == "":
            break
        response = await _handle_line(server, line)
        if response is None:
            continue
        stdout.write(json.dumps(response, ensure_ascii=False, separators=(",", ":")) + "\n")
        stdout.flush()


async def _handle_line(
    server: InternalControlMcpServer,
    line: str,
) -> dict[str, Any] | None:
    if len(line.encode("utf-8")) > MAX_JSONRPC_LINE_BYTES:
        return _error(None, INVALID_REQUEST, "request exceeds maximum size")
    try:
        message = json.loads(line)
    except json.JSONDecodeError as exc:
        return _error(None, PARSE_ERROR, "invalid JSON", str(exc))
    if not isinstance(message, dict):
        return _error(None, INVALID_REQUEST, "request must be a JSON object")
    return await server.handle(message)


async def create_default_server() -> InternalControlMcpServer:
    settings = load_settings()
    return InternalControlMcpServer(default_tool_registry(), ManagementApiClient(settings))


async def amain() -> int:
    server: InternalControlMcpServer | None = None
    try:
        server = await create_default_server()
        await run_stdio(server)
        return 0
    except McpConfigurationError as exc:
        print(f"MCP configuration error: {exc}", file=sys.stderr)
        return 2
    finally:
        if server is not None:
            await server.client.aclose()


def main() -> int:
    return asyncio.run(amain())


def _success(request_id: Any, result: dict[str, Any]) -> dict[str, Any]:
    return {"jsonrpc": JSONRPC_VERSION, "id": request_id, "result": result}


def _error(
    request_id: Any,
    code: int,
    message: str,
    data: Any | None = None,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "jsonrpc": JSONRPC_VERSION,
        "id": request_id,
        "error": {"code": code, "message": message},
    }
    if data is not None:
        payload["error"]["data"] = data
    return payload


def _tool_result(result: dict[str, Any], *, is_error: bool) -> dict[str, Any]:
    return {
        "content": [{"type": "text", "text": dumps_tool_result(result)}],
        "structuredContent": result,
        "isError": is_error,
    }
