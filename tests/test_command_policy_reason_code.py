from dataclasses import dataclass

from domains.command.policy import ModelLookup, NamespaceAllowlistRule, Policy
from packages.config.control import (
    CONTROL_ALLOWED_NAMESPACES_ENV,
    CONTROL_NAMESPACE_DENIED_CODE,
    CONTROL_NAMESPACE_DENIED_MESSAGE,
)


@dataclass(frozen=True)
class CommandTarget:
    namespace: str


def test_namespace_policy_returns_stable_reason_code(monkeypatch) -> None:
    monkeypatch.setenv(CONTROL_ALLOWED_NAMESPACES_ENV, "sandbox")
    policy = Policy(
        (
            NamespaceAllowlistRule(
                name=CONTROL_NAMESPACE_DENIED_CODE,
                field="namespace",
                default_namespace="sandbox",
                reason=CONTROL_NAMESPACE_DENIED_MESSAGE,
            ),
        )
    )

    result = policy.evaluate(ModelLookup(CommandTarget(namespace="target")))

    assert result.allowed is False
    assert result.reason_code == CONTROL_NAMESPACE_DENIED_CODE
    assert result.reason == CONTROL_NAMESPACE_DENIED_MESSAGE
