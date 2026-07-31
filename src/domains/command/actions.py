"""명령 카탈로그 — @command.action 이 "존재하는 명령 + 정책 메타데이터"의 단일 출처(handler 수정 없이 선언)."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass

from packages.config.environments import is_sandbox_environment


@dataclass(frozen=True)
class CommandActionSpec:
    action: str
    recovery_aliases: tuple[str, ...] = ()
    allowed_namespaces: tuple[str, ...] = ()  # 빈 튜플 = 네임스페이스 제한 없음
    requires_approval: bool = False
    requires_approval_outside_sandbox: bool = False
    # Control policy is declared with the action, never inferred from a route
    # name or browser button.  ``max_attempts`` includes the initial attempt.
    supports_cancel: bool = True
    supports_manual_retry: bool = False
    max_attempts: int = 1
    retry_delay_seconds: int = 0
    enforce_control_namespace: bool = True
    read_only: bool = False
    required_agent_capability: str = "command_receiver"

    def matches_recovery_action(self, value: str) -> bool:
        return value == self.action or value in self.recovery_aliases

    def allows_namespace(self, namespace: str) -> bool:
        return not self.allowed_namespaces or namespace in self.allowed_namespaces

    def requires_approval_for(self, namespace: str) -> bool:
        return self.requires_approval or (
            self.requires_approval_outside_sandbox and not is_sandbox_environment(namespace)
        )


class CommandCatalog:
    """명령 카탈로그 레지스트리. 충돌 등록은 즉시 예외, 동일 재선언은 멱등."""

    def __init__(self) -> None:
        self._specs: dict[str, CommandActionSpec] = {}

    def action(
        self,
        action: str,
        *,
        recovery_aliases: tuple[str, ...] = (),
        allowed_namespaces: tuple[str, ...] = (),
        requires_approval: bool = False,
        requires_approval_outside_sandbox: bool = False,
        supports_cancel: bool = True,
        supports_manual_retry: bool = False,
        max_attempts: int = 1,
        retry_delay_seconds: int = 0,
        enforce_control_namespace: bool = True,
        read_only: bool = False,
        required_agent_capability: str = "command_receiver",
    ) -> Callable[[type], type]:
        if max_attempts < 1 or retry_delay_seconds < 0:
            raise ValueError("command retry policy is invalid")
        if not required_agent_capability.strip():
            raise ValueError("command agent capability is required")
        if supports_manual_retry and max_attempts < 2:
            raise ValueError("retryable command requires at least two attempts")
        spec = CommandActionSpec(
            action=action,
            recovery_aliases=recovery_aliases,
            allowed_namespaces=allowed_namespaces,
            requires_approval=requires_approval,
            requires_approval_outside_sandbox=requires_approval_outside_sandbox,
            supports_cancel=supports_cancel,
            supports_manual_retry=supports_manual_retry,
            max_attempts=max_attempts,
            retry_delay_seconds=retry_delay_seconds,
            enforce_control_namespace=enforce_control_namespace,
            read_only=read_only,
            required_agent_capability=required_agent_capability,
        )

        def decorator(marker: type) -> type:
            existing = self._specs.get(action)
            if existing is not None and existing != spec:
                raise ValueError(f"duplicate command action: {action}")
            self._specs[action] = spec
            marker.__command_spec__ = spec  # type: ignore[attr-defined]
            return marker

        return decorator

    def actions(self) -> tuple[CommandActionSpec, ...]:
        import domains.command.builtin_actions  # noqa: F401  # 내장 액션 등록 유발

        return tuple(self._specs.values())

    def allowed_actions(self) -> tuple[str, ...]:
        return tuple(spec.action for spec in self.actions())

    def spec_for(self, action: str) -> CommandActionSpec | None:
        for spec in self.actions():
            if spec.action == action:
                return spec
        return None

    def action_for_recovery(self, value: str) -> str | None:
        for spec in self.actions():
            if spec.matches_recovery_action(value):
                return spec.action
        return None


command = CommandCatalog()

# 하위 호환 별칭(기존 소비자 유지) — 신규 코드는 @command.action / command.* 를 씀.
command_action = command.action
registered_command_actions = command.actions
allowed_command_actions = command.allowed_actions
command_action_for_recovery = command.action_for_recovery
command_action_spec = command.spec_for
