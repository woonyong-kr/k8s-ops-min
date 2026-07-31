from __future__ import annotations

from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any

from commands.context import (
    CommandContext,
    CommandSpec,
    KubernetesClient,
    KubernetesCommandSpec,
    KubernetesScope,
    KubernetesVerb,
    PayloadModel,
)
from commands.kubernetes import KubernetesCommandPolicy
from packages.contracts.event_bus.interfaces import JsonObject

COMMAND_SPEC_ATTRIBUTE = "__target_agent_command_spec__"

CommandHandler = Callable[[CommandContext[Any]], Awaitable[JsonObject]]


@dataclass(frozen=True)
class RegisteredCommand:
    spec: CommandSpec
    handler: CommandHandler


class AgentCommandRegistry:
    def __init__(
        self,
        *,
        cluster_id: str,
        cluster_role: str,
        kubernetes: KubernetesClient,
        default_handler: CommandHandler,
        kubernetes_policy: KubernetesCommandPolicy | None = None,
    ) -> None:
        self.cluster_id = cluster_id
        self.cluster_role = cluster_role
        self.kubernetes = kubernetes
        self.default_handler = default_handler
        self.kubernetes_policy = kubernetes_policy or KubernetesCommandPolicy(cluster_role)
        self.handlers: dict[str, RegisteredCommand] = {}

    @classmethod
    def from_instance(
        cls,
        instance: object,
        *,
        cluster_id: str,
        cluster_role: str,
        kubernetes: KubernetesClient,
        default_handler: CommandHandler,
    ) -> AgentCommandRegistry:
        registry = cls(
            cluster_id=cluster_id,
            cluster_role=cluster_role,
            kubernetes=kubernetes,
            default_handler=default_handler,
        )
        for attribute_name in dir(instance):
            handler = getattr(instance, attribute_name)
            spec = command_spec(handler)
            if spec is not None:
                registry.register(spec, handler)
        return registry

    def register(self, spec: CommandSpec, handler: CommandHandler) -> None:
        if spec.action in self.handlers:
            raise ValueError(f"duplicate target-agent command handler: {spec.action}")
        self.handlers[spec.action] = RegisteredCommand(spec, handler)

    async def execute(
        self,
        action: str,
        payload: JsonObject,
        *,
        metadata: dict[str, object] | None = None,
    ) -> JsonObject:
        command = self.handlers.get(action)
        if command is None:
            spec = CommandSpec(action=action)
            handler = self.default_handler
        else:
            spec = command.spec
            handler = command.handler

        typed_payload = self.validate_payload(spec, payload)
        if spec.kubernetes is not None:
            self.kubernetes_policy.ensure_allowed(
                spec.kubernetes,
                typed_payload,
                direct_execution=bool((metadata or {}).get("direct_execution")),
            )

        context = CommandContext(
            action=action,
            cluster_id=self.cluster_id,
            cluster_role=self.cluster_role,
            payload=typed_payload,
            raw_payload=payload,
            kubernetes=self.kubernetes,
            spec=spec,
            metadata=metadata or {},
        )
        return await handler(context)

    def validate_payload(self, spec: CommandSpec, payload: JsonObject) -> object:
        if spec.payload_model is None:
            return payload
        return spec.payload_model.model_validate(payload)


def command_handler(
    action: str,
    *,
    payload_model: PayloadModel | None = None,
) -> Callable[[CommandHandler], CommandHandler]:
    def decorate(handler: CommandHandler) -> CommandHandler:
        setattr(
            handler,
            COMMAND_SPEC_ATTRIBUTE,
            CommandSpec(action=action, payload_model=payload_model),
        )
        return handler

    return decorate


def kubernetes_command(
    action: str,
    *,
    api_group: str,
    version: str,
    resource: str,
    verb: KubernetesVerb,
    scope: KubernetesScope = "target-agent",
    payload_model: PayloadModel | None = None,
) -> Callable[[CommandHandler], CommandHandler]:
    def decorate(handler: CommandHandler) -> CommandHandler:
        setattr(
            handler,
            COMMAND_SPEC_ATTRIBUTE,
            CommandSpec(
                action=action,
                payload_model=payload_model,
                kubernetes=KubernetesCommandSpec(
                    api_group=api_group,
                    version=version,
                    resource=resource,
                    verb=verb,
                    scope=scope,
                ),
            ),
        )
        return handler

    return decorate


def command_spec(handler: Any) -> CommandSpec | None:
    spec = getattr(handler, COMMAND_SPEC_ATTRIBUTE, None)
    if isinstance(spec, CommandSpec):
        return spec
    wrapped = getattr(handler, "__func__", None)
    wrapped_spec = getattr(wrapped, COMMAND_SPEC_ATTRIBUTE, None)
    return wrapped_spec if isinstance(wrapped_spec, CommandSpec) else None


class CommandDecorators:
    """에이전트 명령 등록 네임스페이스 — 규칙: 등록은 @command.<단어>.

    @command.handler(action)  일반 명령 실행 핸들러
    @command.k8s(action, api_group=..., verb=...)  k8s 리소스/권한 선언 포함 핸들러
    """

    handler = staticmethod(command_handler)
    k8s = staticmethod(kubernetes_command)


command = CommandDecorators()
