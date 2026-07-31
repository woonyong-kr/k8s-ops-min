"""AI 도구 레지스트리 — @ai.tool 이 "LLM 이 호출 가능한 플랫폼 능력"의 단일 출처.

규칙: 등록은 @ai.tool(...), 조회는 registered_ai_tools()/describe().
    @ai.tool(name="list_recent_incidents",
             description="최근 RCA 리포트 조회",
             parameters={"limit": {"type": "integer", "description": "최대 개수"}})
    async def list_recent_incidents(context: ToolContext, limit: int = 5) -> dict: ...

대화 엔진은 도구 목록을 직접 알지 않고 레지스트리만 읽음 — 새 도구 추가 =
도구 파일 1개(엔진 수정 없음). 중복 이름 등록은 즉시 예외(fail-fast),
동일 계약 재선언(모듈 재로딩)은 멱등. 이 모듈은 의존 없는 leaf 임(순환 import 방지).
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

# 도구 핸들러 시그니처: (context: ToolContext, **검증된 kwargs) -> JSON 직렬화 가능 값.
ToolHandler = Callable[..., Awaitable[Any]]


@dataclass(frozen=True)
class ToolContext:
    """도구 실행 시 주입되는 플랫폼 컨텍스트 — 도구는 이 외의 전역에 의존하지 않음."""

    db: Any
    workspace_id: str
    user_id: str
    cluster_id: str | None = None
    resource_type: str | None = None
    kind: str | None = None
    namespace: str | None = None
    name: str | None = None
    uid: str | None = None
    incident_id: str | None = None
    correlation_id: str | None = None
    symptom: str | None = None
    root_cause: str | None = None
    resource_context: dict[str, Any] | None = None
    locale: str | None = None


@dataclass(frozen=True)
class ToolSpec:
    """도구 1개의 계약. parameters 는 JSON Schema properties 스타일 매핑임."""

    name: str
    description: str
    parameters: dict[str, Any]  # {"인자명": {"type": ..., "description": ..., "required": ...}}
    handler: ToolHandler
    locales: tuple[str, ...] = ()  # 빈 튜플 = 모든 로케일 허용

    def required_parameters(self) -> tuple[str, ...]:
        return tuple(
            name for name, schema in self.parameters.items() if schema.get("required") is True
        )

    def validate_arguments(self, arguments: dict[str, Any]) -> dict[str, Any]:
        """인자 이름 검증 — 미지 인자/필수 누락은 즉시 예외(fail-fast)."""
        unknown = sorted(set(arguments) - set(self.parameters))
        if unknown:
            raise ValueError(f"unknown arguments for tool {self.name}: {', '.join(unknown)}")
        missing = sorted(set(self.required_parameters()) - set(arguments))
        if missing:
            raise ValueError(
                f"missing required arguments for tool {self.name}: {', '.join(missing)}"
            )
        return dict(arguments)


class ToolRegistry:
    """도구 계약의 주소록. 등록은 @ai.tool, 조회는 spec()/tools(), 실행은 execute()."""

    def __init__(self) -> None:
        self._specs: dict[str, ToolSpec] = {}

    def tool(
        self,
        *,
        name: str,
        description: str,
        parameters: dict[str, Any] | None = None,
        locales: tuple[str, ...] = (),
    ) -> Callable[[ToolHandler], ToolHandler]:
        """async 함수 데코레이터 — 도구 계약을 스스로 선언함."""

        def decorate(fn: ToolHandler) -> ToolHandler:
            spec = ToolSpec(name, description, dict(parameters or {}), fn, tuple(locales))
            existing = self._specs.get(name)
            if existing is not None and not _same_contract(existing, spec):
                raise ValueError(f"duplicate ai tool: {name}")
            # 동일 계약 재선언(모듈 재로딩)은 멱등 — 다른 계약만 차단함.
            self._specs[name] = spec
            fn.__tool_spec__ = spec  # type: ignore[attr-defined]
            return fn

        return decorate

    def spec(self, name: str) -> ToolSpec:
        try:
            return self._specs[name]
        except KeyError as exc:
            supported = ", ".join(self.tool_names()) or "(none)"
            raise ValueError(f"unknown ai tool: {name}; registered: {supported}") from exc

    def get(self, name: str) -> ToolSpec | None:
        return self._specs.get(name)

    def tools(self) -> tuple[ToolSpec, ...]:
        return tuple(self._specs[name] for name in self.tool_names())

    def tool_names(self) -> tuple[str, ...]:
        return tuple(sorted(self._specs))

    async def execute(self, name: str, context: ToolContext, arguments: dict[str, Any]) -> Any:
        """이름으로 도구 실행 — 인자 검증 후 핸들러 호출(미등록/인자 오류는 예외)."""
        spec = self.spec(name)
        kwargs = spec.validate_arguments(arguments)
        return await spec.handler(context, **kwargs)

    def describe(self) -> str:
        rows = ["AI TOOLS (한눈에 보기)", ""]
        for spec in self.tools():
            params = ", ".join(spec.parameters) or "-"
            rows.append(f"{spec.name:<28} params=({params})  {spec.description}")
        return "\n".join(rows)


def _same_contract(a: ToolSpec, b: ToolSpec) -> bool:
    """모듈 재로딩 시 함수 객체는 달라져도 계약(이름 기준)이 같으면 동일 선언.

    모듈 경로는 비교하지 않음 — 서비스 로컬 모듈은 로딩 방식에 따라
    "tools"/파일 로더 별칭 등 다른 이름으로 재실행될 수 있음.
    """
    return (
        a.description == b.description
        and a.parameters == b.parameters
        and a.locales == b.locales
        and a.handler.__qualname__ == b.handler.__qualname__
    )


ai = ToolRegistry()


def registered_ai_tools() -> tuple[ToolSpec, ...]:
    """조회 관례(registered_*) — 등록된 도구 계약 전체."""
    return ai.tools()


__all__ = [
    "ToolContext",
    "ToolHandler",
    "ToolRegistry",
    "ToolSpec",
    "ai",
    "registered_ai_tools",
]
