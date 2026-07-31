from __future__ import annotations

from collections.abc import Iterable
from contextlib import asynccontextmanager
from copy import deepcopy
from dataclasses import dataclass
from typing import Any, Literal

from fastapi import Request

from packages.ai.engine import DEFAULT_MAX_TOOL_CALLS, ConversationEngine
from packages.ai.llm import LlmClient
from packages.ai.tools import ToolContext
from packages.ai.tools import ToolRegistry as AiToolRegistry
from packages.security.log_lines import redact_log_line, redact_sensitive_value
from services.mcp.internal_control.api_client import ManagementApiClient, ManagementApiError
from services.mcp.internal_control.config import (
    OPSIA_MCP_ENABLE_WRITES_ENV,
    McpConfigurationError,
    configured_session_cookie_name,
    load_settings,
    load_settings_with_auth,
)
from services.mcp.internal_control.tools import (
    ToolInputError,
    default_tool_registry,
)
from services.mcp.internal_control.tools import (
    ToolRegistry as McpToolRegistry,
)

FORMAT_NEUTRAL = "neutral"
FORMAT_OPENAI = "openai"
FORMAT_ANTHROPIC = "anthropic"
FORMAT_GEMINI = "gemini"
AiRuntimeToolFormat = Literal["neutral", "openai", "anthropic", "gemini"]

READ_ONLY_HINT = "readOnlyHint"
DESTRUCTIVE_HINT = "destructiveHint"
IDEMPOTENT_HINT = "idempotentHint"
JSON_SCHEMA_PROPERTIES_KEY = "properties"
JSON_SCHEMA_REQUIRED_KEY = "required"
JSON_SCHEMA_TYPE_KEY = "type"
JSON_SCHEMA_ITEMS_KEY = "items"
APPROVAL_CONFIRMED_ARGUMENT = "approval_confirmed"
DRY_RUN_ARGUMENT = "dry_run"
PROVIDER_SCHEMA_KEYS = frozenset(
    {
        "description",
        "enum",
        "format",
        "maximum",
        "maxItems",
        "maxLength",
        "minimum",
        "minItems",
        "minLength",
        JSON_SCHEMA_REQUIRED_KEY,
    }
)
OPENAI_FUNCTION_TOOL_TYPE = "function"
READ_ONLY_SAFETY_SUFFIX = "Safety: read-only Gateway API call."
WRITE_SAFETY_SUFFIX = (
    "Safety: write-capable Gateway API call; expose only after explicit operator approval. "
    "AI runtime exposure is proposal-only: automatic model calls may request dry-run proposals, "
    "but final submission must happen outside the model loop through the existing Gateway approval "
    "path and RBAC."
)
PROPOSAL_ONLY_WRITE_REJECTION = (
    "AI runtime write tools are proposal-only; use dry_run=true and request operator approval "
    "outside the automatic model loop"
)
PROPOSAL_ONLY_WRITE_ARGUMENTS = frozenset(
    {
        APPROVAL_CONFIRMED_ARGUMENT,
        DRY_RUN_ARGUMENT,
    }
)


@asynccontextmanager
async def request_context_mcp_engine(request: Request, llm: LlmClient):
    """Create one request-scoped read-only MCP engine at the service boundary."""
    bearer_token = _request_bearer_token(request)
    session_cookie = ""
    if not bearer_token:
        session_cookie = request.cookies.get(configured_session_cookie_name(), "")
    if not bearer_token and not session_cookie:
        yield None
        return
    try:
        client = management_client_from_auth(
            bearer_token=bearer_token,
            session_cookie=session_cookie,
            writes_enabled=False,
        )
    except McpConfigurationError:
        yield None
        return
    try:
        yield mcp_conversation_engine(llm, client, include_write_tools=False)
    finally:
        await client.aclose()


def _request_bearer_token(request: Request) -> str:
    authorization = request.headers.get("authorization", "").strip()
    scheme, separator, credential = authorization.partition(" ")
    if not separator or scheme.casefold() != "bearer":
        return ""
    return credential.strip()


@dataclass(frozen=True, slots=True)
class AiRuntimeTool:
    """Provider-neutral tool metadata that can be projected into model tool formats."""

    name: str
    description: str
    input_schema: dict[str, Any]
    read_only: bool
    destructive: bool
    idempotent: bool
    approval_required: bool

    def as_neutral_tool(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": deepcopy(self.input_schema),
            "safety": {
                "read_only": self.read_only,
                "destructive": self.destructive,
                "idempotent": self.idempotent,
                "approval_required": self.approval_required,
            },
        }

    def as_openai_tool(self) -> dict[str, Any]:
        return {
            "type": OPENAI_FUNCTION_TOOL_TYPE,
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": deepcopy(self.input_schema),
            },
        }

    def as_anthropic_tool(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": deepcopy(self.input_schema),
        }

    def as_gemini_function_declaration(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "description": self.description,
            "parameters": _gemini_schema(self.input_schema),
        }


class AiRuntimeMcpExecutor:
    """Execute runtime tool calls through the MCP registry and existing Gateway APIs."""

    def __init__(
        self,
        registry: McpToolRegistry,
        client: ManagementApiClient,
        *,
        allow_write_tools: bool = False,
    ) -> None:
        self.registry = registry
        self.client = client
        self.allow_write_tools = allow_write_tools
        self._tool_by_name = {
            tool.name: tool
            for tool in tools_from_mcp_registry(
                registry,
                include_write_tools=allow_write_tools,
                write_tools_enabled=client.settings.writes_enabled,
            )
        }

    async def call(self, name: str, arguments: dict[str, Any]) -> dict[str, Any]:
        if not isinstance(name, str) or not name:
            raise ToolInputError("tool name is required")
        if not isinstance(arguments, dict):
            raise ToolInputError("tool arguments must be an object")
        runtime_tool = self._tool_by_name.get(name)
        if runtime_tool is None:
            raise ToolInputError("tool is not exposed to the AI runtime")
        if not runtime_tool.read_only:
            _assert_proposal_only_write_arguments(arguments)
        try:
            result = await self.registry.call(name, arguments, self.client)
        except ToolInputError as exc:
            raise ToolInputError(redact_log_line(str(exc))) from exc
        except ManagementApiError as exc:
            raise ManagementApiError(exc.status_code, redact_log_line(exc.detail)) from exc
        except Exception as exc:
            raise ToolInputError(redact_log_line(str(exc))) from exc
        return {
            "tool": name,
            "ok": True,
            "result": redact_sensitive_value(result),
        }


def tools_from_mcp_registry(
    registry: McpToolRegistry | None = None,
    *,
    include_write_tools: bool = False,
    write_tools_enabled: bool = False,
) -> tuple[AiRuntimeTool, ...]:
    _assert_write_tools_exposure_allowed(
        include_write_tools=include_write_tools,
        write_tools_enabled=write_tools_enabled,
    )
    selected_registry = registry or default_tool_registry()
    runtime_tools: list[AiRuntimeTool] = []
    for protocol_tool in selected_registry.list_tools():
        runtime_tool = _runtime_tool(protocol_tool)
        if not include_write_tools and not runtime_tool.read_only:
            continue
        runtime_tools.append(runtime_tool)
    return tuple(runtime_tools)


def ai_tool_registry_from_mcp(
    registry: McpToolRegistry,
    client: ManagementApiClient,
    *,
    include_write_tools: bool = False,
) -> AiToolRegistry:
    """Build the existing internal ConversationEngine registry from MCP tools."""
    ai_registry = AiToolRegistry()
    executor = AiRuntimeMcpExecutor(
        registry,
        client,
        allow_write_tools=include_write_tools,
    )
    for runtime_tool in tools_from_mcp_registry(
        registry,
        include_write_tools=include_write_tools,
        write_tools_enabled=client.settings.writes_enabled,
    ):
        ai_registry.tool(
            name=runtime_tool.name,
            description=runtime_tool.description,
            parameters=_ai_tool_parameters(runtime_tool.input_schema),
        )(_ai_tool_handler(executor, runtime_tool.name))
    return ai_registry


def ai_tool_registry_with_mcp(
    base_registry: AiToolRegistry,
    client: ManagementApiClient,
    *,
    registry: McpToolRegistry | None = None,
    include_write_tools: bool = False,
) -> AiToolRegistry:
    """Copy an existing internal AI registry and append MCP runtime tools."""
    ai_registry = _copy_ai_tool_registry(base_registry)
    selected_registry = registry or default_tool_registry()
    mcp_registry = ai_tool_registry_from_mcp(
        selected_registry,
        client,
        include_write_tools=include_write_tools,
    )
    for spec in mcp_registry.tools():
        if ai_registry.get(spec.name) is not None:
            continue
        ai_registry.tool(
            name=spec.name,
            description=spec.description,
            parameters=deepcopy(spec.parameters),
            locales=spec.locales,
        )(spec.handler)
    return ai_registry


def mcp_conversation_engine(
    llm: LlmClient,
    client: ManagementApiClient,
    *,
    registry: McpToolRegistry | None = None,
    base_registry: AiToolRegistry | None = None,
    include_write_tools: bool = False,
    max_tool_calls: int = DEFAULT_MAX_TOOL_CALLS,
) -> ConversationEngine:
    """Create a ConversationEngine that can auto-run MCP tools from model tool calls."""
    selected_registry = registry or default_tool_registry()
    ai_registry = (
        ai_tool_registry_from_mcp(
            selected_registry,
            client,
            include_write_tools=include_write_tools,
        )
        if base_registry is None
        else ai_tool_registry_with_mcp(
            base_registry,
            client,
            registry=selected_registry,
            include_write_tools=include_write_tools,
        )
    )
    return ConversationEngine(llm, ai_registry, max_tool_calls=max_tool_calls)


def management_client_from_env() -> ManagementApiClient:
    return ManagementApiClient(load_settings())


def management_client_from_auth(
    *,
    bearer_token: str = "",
    cookie_header: str = "",
    session_cookie: str = "",
    writes_enabled: bool | None = None,
) -> ManagementApiClient:
    return ManagementApiClient(
        load_settings_with_auth(
            bearer_token=bearer_token,
            cookie_header=cookie_header,
            session_cookie=session_cookie,
            writes_enabled=writes_enabled,
        )
    )


def format_runtime_tools(
    tools: Iterable[AiRuntimeTool],
    *,
    format: AiRuntimeToolFormat = FORMAT_NEUTRAL,
) -> list[dict[str, Any]]:
    if format == FORMAT_NEUTRAL:
        return [tool.as_neutral_tool() for tool in tools]
    if format == FORMAT_OPENAI:
        return [tool.as_openai_tool() for tool in tools]
    if format == FORMAT_ANTHROPIC:
        return [tool.as_anthropic_tool() for tool in tools]
    if format == FORMAT_GEMINI:
        return [tool.as_gemini_function_declaration() for tool in tools]
    raise ValueError(f"unsupported AI runtime tool format: {format}")


def openai_tools(
    registry: McpToolRegistry | None = None,
    *,
    include_write_tools: bool = False,
    write_tools_enabled: bool = False,
) -> list[dict[str, Any]]:
    return format_runtime_tools(
        tools_from_mcp_registry(
            registry,
            include_write_tools=include_write_tools,
            write_tools_enabled=write_tools_enabled,
        ),
        format=FORMAT_OPENAI,
    )


def anthropic_tools(
    registry: McpToolRegistry | None = None,
    *,
    include_write_tools: bool = False,
    write_tools_enabled: bool = False,
) -> list[dict[str, Any]]:
    return format_runtime_tools(
        tools_from_mcp_registry(
            registry,
            include_write_tools=include_write_tools,
            write_tools_enabled=write_tools_enabled,
        ),
        format=FORMAT_ANTHROPIC,
    )


def gemini_function_declarations(
    registry: McpToolRegistry | None = None,
    *,
    include_write_tools: bool = False,
    write_tools_enabled: bool = False,
) -> list[dict[str, Any]]:
    return format_runtime_tools(
        tools_from_mcp_registry(
            registry,
            include_write_tools=include_write_tools,
            write_tools_enabled=write_tools_enabled,
        ),
        format=FORMAT_GEMINI,
    )


def _assert_write_tools_exposure_allowed(
    *,
    include_write_tools: bool,
    write_tools_enabled: bool,
) -> None:
    if include_write_tools and not write_tools_enabled:
        raise McpConfigurationError(
            f"{OPSIA_MCP_ENABLE_WRITES_ENV}=true is required before exposing "
            "MCP write tools to the AI runtime"
        )


def _ai_tool_handler(
    executor: AiRuntimeMcpExecutor,
    tool_name: str,
) -> Any:
    async def call_mcp_tool(_context: ToolContext, **arguments: Any) -> dict[str, Any]:
        return await executor.call(tool_name, arguments)

    return call_mcp_tool


def _copy_ai_tool_registry(base_registry: AiToolRegistry) -> AiToolRegistry:
    ai_registry = AiToolRegistry()
    for spec in base_registry.tools():
        ai_registry.tool(
            name=spec.name,
            description=spec.description,
            parameters=deepcopy(spec.parameters),
            locales=spec.locales,
        )(spec.handler)
    return ai_registry


def _ai_tool_parameters(input_schema: dict[str, Any]) -> dict[str, Any]:
    properties = input_schema.get(JSON_SCHEMA_PROPERTIES_KEY)
    if not isinstance(properties, dict):
        return {}
    required = input_schema.get(JSON_SCHEMA_REQUIRED_KEY)
    required_names = set(required if isinstance(required, list) else [])
    parameters: dict[str, Any] = {}
    for name, schema in properties.items():
        if not isinstance(name, str) or not isinstance(schema, dict):
            continue
        parameter_schema = deepcopy(schema)
        if name in required_names:
            parameter_schema[JSON_SCHEMA_REQUIRED_KEY] = True
        parameters[name] = parameter_schema
    return parameters


def _assert_proposal_only_write_arguments(arguments: dict[str, Any]) -> None:
    if arguments.get(DRY_RUN_ARGUMENT) is False:
        raise ToolInputError(PROPOSAL_ONLY_WRITE_REJECTION)
    if arguments.get(APPROVAL_CONFIRMED_ARGUMENT) is True:
        raise ToolInputError(PROPOSAL_ONLY_WRITE_REJECTION)


def _proposal_only_input_schema(input_schema: dict[str, Any]) -> dict[str, Any]:
    schema = deepcopy(input_schema)
    properties = schema.get(JSON_SCHEMA_PROPERTIES_KEY)
    if not isinstance(properties, dict):
        return schema
    for name in PROPOSAL_ONLY_WRITE_ARGUMENTS:
        properties.pop(name, None)
    required = schema.get(JSON_SCHEMA_REQUIRED_KEY)
    if isinstance(required, list):
        schema[JSON_SCHEMA_REQUIRED_KEY] = [
            name for name in required if name not in PROPOSAL_ONLY_WRITE_ARGUMENTS
        ]
    return schema


def _runtime_tool(protocol_tool: dict[str, Any]) -> AiRuntimeTool:
    annotations = protocol_tool.get("annotations") or {}
    read_only = bool(annotations.get(READ_ONLY_HINT) is True)
    destructive = bool(annotations.get(DESTRUCTIVE_HINT) is True)
    idempotent = bool(annotations.get(IDEMPOTENT_HINT) is True)
    return AiRuntimeTool(
        name=str(protocol_tool["name"]),
        description=_runtime_description(
            str(protocol_tool.get("description", "")),
            read_only=read_only,
        ),
        input_schema=(
            deepcopy(protocol_tool["inputSchema"])
            if read_only
            else _proposal_only_input_schema(protocol_tool["inputSchema"])
        ),
        read_only=read_only,
        destructive=destructive,
        idempotent=idempotent,
        approval_required=not read_only,
    )


def _runtime_description(description: str, *, read_only: bool) -> str:
    if read_only:
        return f"{description} {READ_ONLY_SAFETY_SUFFIX}"
    return f"{description} {WRITE_SAFETY_SUFFIX}"


def _gemini_schema(schema: dict[str, Any]) -> dict[str, Any]:
    converted: dict[str, Any] = {}
    for key, value in schema.items():
        if key == JSON_SCHEMA_TYPE_KEY and isinstance(value, str):
            converted[key] = value.upper()
        elif key == JSON_SCHEMA_PROPERTIES_KEY and isinstance(value, dict):
            converted[key] = {
                str(name): _gemini_schema(child)
                for name, child in value.items()
                if isinstance(child, dict)
            }
        elif key == JSON_SCHEMA_ITEMS_KEY and isinstance(value, dict):
            converted[key] = _gemini_schema(value)
        elif key in PROVIDER_SCHEMA_KEYS:
            converted[key] = deepcopy(value)
    return converted


__all__ = [
    "AiRuntimeMcpExecutor",
    "AiRuntimeTool",
    "AiRuntimeToolFormat",
    "ai_tool_registry_from_mcp",
    "ai_tool_registry_with_mcp",
    "anthropic_tools",
    "format_runtime_tools",
    "gemini_function_declarations",
    "management_client_from_auth",
    "management_client_from_env",
    "mcp_conversation_engine",
    "openai_tools",
    "tools_from_mcp_registry",
]
