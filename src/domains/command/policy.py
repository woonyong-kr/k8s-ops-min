from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Protocol, cast

from packages.config.control import control_namespace_allowed
from packages.config.errors import require
from packages.config.settings import env

# 명령 리스/재시도 튜닝값 — env 미설정 시 기존 기본값과 동일한 기본값이 적용됨(배포 호환)
DEFAULT_COMMAND_LEASE_SECONDS_ENV = "COMMAND_LEASE_SECONDS"  # 명령 리스 유지 초(기본 60)
DEFAULT_COMMAND_LEASE_SECONDS = int(env(DEFAULT_COMMAND_LEASE_SECONDS_ENV, "60"))
DEFAULT_COMMAND_HEARTBEAT_INTERVAL_SECONDS_ENV = (
    "COMMAND_HEARTBEAT_INTERVAL_SECONDS"  # 리스 하트비트 간격 초(기본 20)
)
DEFAULT_COMMAND_HEARTBEAT_INTERVAL_SECONDS = int(
    env(DEFAULT_COMMAND_HEARTBEAT_INTERVAL_SECONDS_ENV, "20")
)
DEFAULT_COMMAND_RETRY_MAX_ATTEMPTS_ENV = "COMMAND_RETRY_MAX_ATTEMPTS"  # 재시도 최대 횟수(기본 3)
DEFAULT_COMMAND_RETRY_MAX_ATTEMPTS = int(env(DEFAULT_COMMAND_RETRY_MAX_ATTEMPTS_ENV, "3"))
DEFAULT_COMMAND_RETRY_DELAY_SECONDS_ENV = "COMMAND_RETRY_DELAY_SECONDS"  # 재시도 간격 초(기본 5)
DEFAULT_COMMAND_RETRY_DELAY_SECONDS = int(env(DEFAULT_COMMAND_RETRY_DELAY_SECONDS_ENV, "5"))


@dataclass(frozen=True)
class PolicyRuleConfig:
    name: str
    field: str
    reason: str
    expected: Any | None = None
    allowed_values: tuple[Any, ...] = ()
    default: Any = None


@dataclass(frozen=True)
class CommandConfig:
    service_name: str
    agent_route_channel: str
    policy_steps: tuple[str, ...]
    default_namespace: str
    default_cluster_id: str
    default_command_action: str
    command_status_queued: str
    lease_seconds: int
    heartbeat_interval_seconds: int
    retry_max_attempts: int
    retry_delay_seconds: int
    required_agent_capability: str
    policy_rules: tuple[PolicyRuleConfig, ...]


class Lookup(Protocol):
    """이름으로 값 읽기. 룰은 dict 가 아니라 이 인터페이스에 의존."""

    def value(self, field: str, default: Any = None) -> Any: ...


@dataclass(frozen=True)
class ModelLookup:
    """속성(model) 기반 Lookup 구현 (Pydantic/dataclass body)."""

    model: Any

    def value(self, field: str, default: Any = None) -> Any:
        return getattr(self.model, field, default)


class Rule(Protocol):
    name: str
    reason: str

    def allows(self, target: Lookup) -> bool: ...


@dataclass(frozen=True)
class EqualsRule:
    name: str
    field: str
    expected: Any
    reason: str
    default: Any = None

    def allows(self, target: Lookup) -> bool:
        return target.value(self.field, self.default) == self.expected

    @classmethod
    def build(cls, config: PolicyRuleConfig) -> EqualsRule:
        return cls(
            name=config.name,
            field=config.field,
            expected=config.expected,
            reason=config.reason,
            default=config.default,
        )


@dataclass(frozen=True)
class AllowedValuesRule:
    name: str
    field: str
    allowed_values: tuple[Any, ...]
    reason: str
    default: Any = None

    def allows(self, target: Lookup) -> bool:
        return target.value(self.field, self.default) in self.allowed_values

    @classmethod
    def build(cls, config: PolicyRuleConfig) -> AllowedValuesRule:
        return cls(
            name=config.name,
            field=config.field,
            allowed_values=config.allowed_values,
            reason=config.reason,
            default=config.default,
        )


@dataclass(frozen=True)
class NamespaceAllowlistRule:
    """제어 허용 네임스페이스 룰 — 기준은 packages.config.control 단일 소스.

    env 를 평가 시점마다 읽으므로 재기동 없이 정책이 반영되고 테스트가 쉽다.
    """

    name: str
    field: str
    default_namespace: str
    reason: str

    def allows(self, target: Lookup) -> bool:
        return control_namespace_allowed(str(target.value(self.field, self.default_namespace)))


@dataclass(frozen=True)
class Result:
    allowed: bool
    reason: str | None = None
    reason_code: str | None = None

    @classmethod
    def allow(cls) -> Result:
        return cls(True)

    @classmethod
    def reject(cls, reason: str, reason_code: str | None = None) -> Result:
        return cls(False, reason, reason_code)

    def require_reason(self) -> str:
        require(self.reason is not None, "정책 거부에 reason 필요")
        assert self.reason is not None  # require 가 보장(타입체커 내로잉)
        return self.reason


class Policy:
    def __init__(self, rules: Sequence[Rule]) -> None:
        self.rules: tuple[Rule, ...] = tuple(rules)

    @classmethod
    def build(cls, configs: tuple[PolicyRuleConfig, ...]) -> Policy:
        rules: list[Rule] = []
        for config in configs:
            if config.allowed_values:
                rules.append(cast(Rule, AllowedValuesRule.build(config)))
                continue
            rules.append(cast(Rule, EqualsRule.build(config)))
        return cls(rules)

    def evaluate(self, target: Lookup) -> Result:
        for rule in self.rules:
            if not rule.allows(target):
                return Result.reject(rule.reason, rule.name)
        return Result.allow()
