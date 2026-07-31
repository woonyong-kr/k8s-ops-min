"""카탈로그 읽기 전용 MCP 서버.

"이 자산의 스키마가 언제 바뀌었나"는 사람이 물어도, AI 가 물어도 같은
질문이다. 답은 이미 카탈로그 API 가 갖고 있고, 필요한 것은 질문과
엔드포인트를 잇는 얇은 계층이다.

설계 근거: docs/portfolio/catalog-api-mcp.md

세 가지를 지킨다.

1. DB 에 직접 붙지 않는다. 모든 도구가 HTTP 로 기존 API 를 호출하고
   호출자의 토큰을 전달한다. 직접 붙으면 API 의 인가를 우회한다.
2. 인자 스키마를 좁힌다. additionalProperties=false 로 열거·길이·범위를
   벗어난 인자를 서버가 거부한다.
3. 쓰기 도구가 없다. 카탈로그는 배치가 쓰고 사람과 AI 는 읽는다.
   쓰기 도구를 두지 않았으므로 "실수로 켜지는" 경로 자체가 없다.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass
from typing import Any

MAX_ITEMS = 50
MAX_BYTES = 64 * 1024

API_BASE = os.environ.get("CATALOG_API_BASE", "http://localhost:8000/v1/catalog")


@dataclass(frozen=True)
class Tool:
    name: str
    description: str
    path: str
    input_schema: dict[str, Any]


_SOURCE_ENUM = ["kubernetes", "prometheus", "loki", "tempo", "ops"]
_LIMIT = {"type": "integer", "minimum": 1, "maximum": MAX_ITEMS}
_CURSOR = {"type": "string", "maxLength": 512}


TOOLS: tuple[Tool, ...] = (
    Tool(
        name="list_data_sources",
        description="등록된 원천 시스템 목록. 어떤 시스템에서 데이터를 가져오는지 답한다.",
        path="/sources",
        input_schema={
            "type": "object",
            "properties": {"limit": _LIMIT, "cursor": _CURSOR},
            "additionalProperties": False,
        },
    ),
    Tool(
        name="search_assets",
        description="자산을 이름으로 검색한다. 이 이름이 들어간 자산이 있는지 답한다.",
        path="/assets",
        input_schema={
            "type": "object",
            "properties": {
                "query": {"type": "string", "maxLength": 128},
                "source": {"type": "string", "enum": _SOURCE_ENUM},
                "limit": _LIMIT,
                "cursor": _CURSOR,
            },
            "additionalProperties": False,
        },
    ),
    Tool(
        name="get_asset_schema",
        description="자산의 계약 이력. 이 자산 스키마가 언제 바뀌었는지 답한다.",
        path="/assets/{asset_id}/schema",
        input_schema={
            "type": "object",
            "properties": {"asset_id": {"type": "string", "maxLength": 256}},
            "required": ["asset_id"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="get_asset_lineage",
        description="자산의 upstream·downstream 경로. 이 데이터가 어디서 왔는지 답한다.",
        path="/assets/{asset_id}/lineage",
        input_schema={
            "type": "object",
            "properties": {"asset_id": {"type": "string", "maxLength": 256}},
            "required": ["asset_id"],
            "additionalProperties": False,
        },
    ),
    Tool(
        name="list_quality_issues",
        description="미해결 품질 이슈. 지금 문제 있는 자산이 무엇인지 답한다.",
        path="/quality/issues",
        input_schema={
            "type": "object",
            "properties": {
                "severity": {"type": "string", "enum": ["error", "warning"]},
                "limit": _LIMIT,
                "cursor": _CURSOR,
            },
            "additionalProperties": False,
        },
    ),
    Tool(
        name="get_run_status",
        description="실행 이력과 소스별 지표. 어제 배치가 잘 돌았는지 답한다.",
        path="/runs",
        input_schema={
            "type": "object",
            "properties": {
                "logical_date": {"type": "string", "maxLength": 10},
                "limit": _LIMIT,
                "cursor": _CURSOR,
            },
            "additionalProperties": False,
        },
    ),
)

TOOLS_BY_NAME = {t.name: t for t in TOOLS}

# 원천에서 온 값. 모델에게 데이터이지 지시가 아니라고 알린다.
UNTRUSTED_FIELDS = frozenset(
    {"qualified_name", "transformation", "observed_value", "expected_value", "finding", "name"}
)


class ToolError(Exception):
    def __init__(self, code: str, correlation_id: str | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.correlation_id = correlation_id


def validate_arguments(tool_name: str, arguments: dict[str, Any]) -> dict[str, Any]:
    """인자 스키마 검증.

    additionalProperties=false 가 핵심이다. 모델이 인자를 확대하려 해도
    서버에서 거부된다. 임의 URL·헤더·SQL 조각을 넘길 수 없다.
    """
    tool = TOOLS_BY_NAME.get(tool_name)
    if tool is None:
        raise ToolError("unknown_tool")

    schema = tool.input_schema
    props: dict[str, Any] = schema.get("properties", {})
    allowed = set(props)

    unknown = set(arguments) - allowed
    if unknown:
        raise ToolError("unknown_argument")

    for name in schema.get("required", []):
        if name not in arguments:
            raise ToolError("missing_argument")

    for key, value in arguments.items():
        spec = props[key]
        kind = spec.get("type")
        if kind == "string":
            if not isinstance(value, str):
                raise ToolError("invalid_argument")
            if len(value) > spec.get("maxLength", 256):
                raise ToolError("argument_too_long")
            if "enum" in spec and value not in spec["enum"]:
                raise ToolError("invalid_argument")
            if any(ord(c) < 32 for c in value):
                raise ToolError("invalid_argument")
        elif kind == "integer":
            if not isinstance(value, int) or isinstance(value, bool):
                raise ToolError("invalid_argument")
            if not (spec.get("minimum", 0) <= value <= spec.get("maximum", MAX_ITEMS)):
                raise ToolError("argument_out_of_range")
    return arguments


def mark_untrusted(item: dict[str, Any]) -> dict[str, Any]:
    """원천에서 온 값을 untrusted 블록으로 감싼다.

    이 절이 없으면 위의 권한 설계는 절반만 한 것이다. 클러스터에 pod 를
    만들거나 로그를 남길 수 있는 사람이면 qualified_name·observed_value 를
    통제할 수 있고, 그 문자열이 도구 결과라는 신뢰받는 옷을 입고
    모델 컨텍스트에 들어간다.

    완전히 막지는 못한다. 같은 세션에 다른 서버의 쓰기 도구가 붙어 있으면
    주입된 지시가 그쪽으로 갈 수 있다. 그건 에이전트 호스트의 세션 정책
    문제이지 이 서버가 혼자 풀 수 있는 문제가 아니다.
    """
    trusted = {k: v for k, v in item.items() if k not in UNTRUSTED_FIELDS}
    untrusted = {
        k: _sanitize(v) for k, v in item.items() if k in UNTRUSTED_FIELDS and v is not None
    }
    if untrusted:
        trusted["untrusted"] = untrusted
    return trusted


def _sanitize(value: Any) -> Any:
    if not isinstance(value, str):
        return value
    cleaned = "".join(c if ord(c) >= 32 else " " for c in value)
    return cleaned[:512]


def bound_response(
    items: list[dict[str, Any]], *, upstream_cursor: str | None = None
) -> dict[str, Any]:
    """응답을 제한하되 제한 사실을 남긴다.

    모델이 잘린 목록을 전체로 착각하면 "이슈가 3건뿐"이라고 답한다.
    수집 한도 문서의 규칙을 그대로 따른다.

    커서는 상위가 준 것을 그대로 넘긴다. 여기서 정수를 지어내면 모델이 그걸
    다시 넘겼을 때 상위 디코더가 거부한다. 숨기지 않는 것과 도달할 수 있게 하는
    것은 다르고, 도달 수단을 중간에서 바꿔치기하면 후자가 깨진다.
    """
    original = len(items)
    marked = [mark_untrusted(i) for i in items[:MAX_ITEMS]]

    payload = {"items": marked, "original_count": original, "returned_count": len(marked)}
    encoded = json.dumps(payload, ensure_ascii=False)
    while len(encoded.encode("utf-8")) > MAX_BYTES and marked:
        marked.pop()
        payload = {"items": marked, "original_count": original, "returned_count": len(marked)}
        encoded = json.dumps(payload, ensure_ascii=False)

    payload["truncated"] = payload["returned_count"] < original or upstream_cursor is not None
    if upstream_cursor:
        payload["next_cursor"] = upstream_cursor
    elif payload["returned_count"] < original:
        # 상위가 커서를 주지 않았는데 여기서 잘랐다면 나머지에 도달할 방법이 없다.
        # 그 사실을 숨기면 모델은 부분을 전체로 읽는다.
        payload["remainder_unreachable"] = True
    return payload


def list_tools() -> list[dict[str, Any]]:
    return [
        {"name": t.name, "description": t.description, "inputSchema": t.input_schema}
        for t in TOOLS
    ]


def main(argv: list[str] | None = None) -> int:
    """MCP 서버를 stdio 로 띄운다.

    --list-tools 를 주면 도구 목록만 출력하고 끝난다. 프로토콜을 말하지 않는
    확인용 경로다.
    """
    argv = sys.argv[1:] if argv is None else argv
    if "--list-tools" in argv:
        print(json.dumps({"tools": list_tools()}, ensure_ascii=False, indent=2))
        return 0

    from .stdio import serve_stdio

    return serve_stdio()


if __name__ == "__main__":
    raise SystemExit(main())
