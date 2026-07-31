"""Deterministic, context-bound alert action proposals for the AI facade."""

from __future__ import annotations

import re
from dataclasses import dataclass

from domains.alert.schemas import AlertRuleCreateRequest, AlertRuleScope
from packages.contracts.gateway.requests import AiAssistantContext
from packages.contracts.gateway.responses import AiChatAction

# G4 audience-load measurements may tune this value. Keep the proposal default
# in one place so the parser, response rationale, and tests cannot drift.
DEFAULT_ALERT_RULE_FOR_SECONDS = 20

_ALERT_NOUN = re.compile(r"알림|알람|\balert\b|\bnotify\b", re.IGNORECASE)
_NOTIFY_REQUEST = re.compile(r"알려", re.IGNORECASE)
_CPU = re.compile(r"(?<![A-Za-z])cpu(?![A-Za-z])|씨피유", re.IGNORECASE)
_MEMORY = re.compile(r"(?<![A-Za-z])mem(?:ory)?(?![A-Za-z])|메모리", re.IGNORECASE)
_PERCENT = re.compile(r"(?<![\d.])(\d+(?:\.\d+)?)\s*(?:%|퍼센트)")


@dataclass(frozen=True)
class AlertActionDecision:
    is_alert_intent: bool
    action: AiChatAction | None = None
    clarification: str | None = None


def propose_alert_rule_action(
    message: str,
    context: AiAssistantContext,
) -> AlertActionDecision:
    """Parse the single allowlisted AI action without executing it."""
    has_metric_term = _CPU.search(message) is not None or _MEMORY.search(message) is not None
    has_threshold = _PERCENT.search(message) is not None
    if _ALERT_NOUN.search(message) is None and not (
        _NOTIFY_REQUEST.search(message) is not None and has_metric_term and has_threshold
    ):
        return AlertActionDecision(is_alert_intent=False)

    metrics: list[tuple[str, str]] = []
    if _CPU.search(message) is not None:
        metrics.append(("cpu_pct", "CPU"))
    if _MEMORY.search(message) is not None:
        metrics.append(("mem_pct", "메모리"))
    if len(metrics) != 1:
        return AlertActionDecision(
            is_alert_intent=True,
            clarification="CPU와 메모리 중 어떤 사용률을 감시할까요?",
        )

    thresholds = [float(match) for match in _PERCENT.findall(message)]
    if not thresholds:
        return AlertActionDecision(
            is_alert_intent=True,
            clarification="몇 %를 넘을 때 알릴까요?",
        )
    if len(thresholds) != 1:
        return AlertActionDecision(
            is_alert_intent=True,
            clarification="알림 임계값을 하나의 % 값으로 알려 주세요.",
        )

    scope = AlertRuleScope(
        clusters=context.filters.clusters,
        namespaces=context.filters.namespaces,
        applications=context.filters.applications,
        labels=context.filters.labels,
    )
    if not any((scope.clusters, scope.namespaces, scope.applications, scope.labels)):
        return AlertActionDecision(
            is_alert_intent=True,
            clarification=(
                "알림 범위를 정하려면 화면에서 클러스터나 네임스페이스를 먼저 선택해 주세요."
            ),
        )

    metric, metric_label = metrics[0]
    threshold = thresholds[0]
    comparator = _comparator(message)
    threshold_label = f"{threshold:g}"
    payload = AlertRuleCreateRequest(
        name=f"파드 {metric_label} {threshold_label}% 알림",
        scope=scope,
        metric=metric,
        comparator=comparator,
        threshold=threshold,
        for_seconds=DEFAULT_ALERT_RULE_FOR_SECONDS,
        severity="high",
        channels=[],
        enabled=True,
    )
    rationale = (
        f"현재 화면의 필터 범위에서 파드 {metric_label} 사용률이 "
        f"{threshold_label}% 조건을 {DEFAULT_ALERT_RULE_FOR_SECONDS}초 동안 충족할 때 "
        "알리도록 제안했습니다."
    )
    return AlertActionDecision(
        is_alert_intent=True,
        action=AiChatAction(
            type="create_alert_rule",
            payload=payload.model_dump(mode="json"),
            rationale=rationale,
        ),
    )


def _comparator(message: str) -> str:
    if "이상" in message:
        return ">="
    if "이하" in message:
        return "<="
    if "미만" in message:
        return "<"
    return ">"
