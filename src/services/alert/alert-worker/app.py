"""alert-worker — alert.requested → alert.dispatched → optional command.requested."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import AsyncIterator
from dataclasses import dataclass
from typing import Any

import httpx

from domains.alert.delivery import post_alert_webhook
from domains.alert.evaluation import AlertEvaluationEngine
from domains.alert.events import AlertDispatchedBody, AlertRejectedBody, AlertRequestedBody
from domains.alert.incidents import persist_incident_alert_event, resolve_incident_alert_event
from domains.alert.measurements import (
    DEFAULT_MEASUREMENT_MAX_AGE_SECONDS,
    AlertRuleMeasurementLoader,
)
from domains.alert.repository import severity_matches
from domains.rca.events import IncidentDetectedBody, IncidentResolvedBody
from packages.config.environments import normalize_environment
from packages.config.logs import CONTEXT_KEY, get_logger
from packages.config.settings import env
from packages.contracts.alert.provider import AlertProvider
from packages.contracts.event_bus.bodies import EventBody
from packages.events.envelope import event
from packages.runtime.app import App, EventContext
from packages.runtime.async_db import AsyncDb, run_sync_with_uow_affinity
from packages.runtime.service import AsyncService
from packages.runtime.worker import WorkerRuntime
from packages.storage.database import Database, wait_for_database
from packages.storage.retry import to_thread_db_retry

app = App("alert-worker")
LOGGER = get_logger(__name__)

ALERT_DISPATCH_FAILED_REASON = "alert dispatch failed"
ALERT_PROVIDER_ENV = "ALERT_PROVIDER"
LOG_PROVIDER_NAME = "log"
WEBHOOK_PROVIDER_NAME = "webhook"
ALERT_WEBHOOK_URL_ENV = "ALERT_WEBHOOK_URL"
ALERT_HTTP_TIMEOUT_SECONDS_ENV = "ALERT_HTTP_TIMEOUT_SECONDS"  # 웹훅 타임아웃 초(기본 10)
DEFAULT_ALERT_HTTP_TIMEOUT_SECONDS = "10"
ALERT_BLOCKED_SEVERITIES_ENV = "ALERT_BLOCKED_SEVERITIES"
ALERT_AUTO_COMMAND_ENVIRONMENTS_ENV = "ALERT_AUTO_COMMAND_ENVIRONMENTS"
DEFAULT_ALERT_AUTO_COMMAND_ENVIRONMENTS = "sandbox,staging"
ALERT_EVALUATION_INTERVAL_SECONDS_ENV = "ALERT_EVALUATION_INTERVAL_SECONDS"
DEFAULT_ALERT_EVALUATION_INTERVAL_SECONDS = "5"
ALERT_MEASUREMENT_MAX_AGE_SECONDS_ENV = "ALERT_MEASUREMENT_MAX_AGE_SECONDS"
ALERT_SEVERITY_BLOCKED_REASON = "alert severity blocked by policy"
AUTO_COMMAND_ENVIRONMENT_DENIED_REASON = "auto command not allowed for environment"
ALERT_SELECTED_CHANNELS_UNAVAILABLE_REASON = "selected alert channels unavailable"
RULE_DELIVERY_SEVERITY = {
    "critical": "critical",
    "high": "critical",
    "medium": "warning",
    "low": "info",
    "warning": "warning",
    "info": "info",
}
# 웹훅 URL 부재는 부팅 실패가 아니라 요청 시점 실패 — 워커는 뜨고,
# 각 alert.requested 는 alert.rejected 경로로 흐름.
MISSING_WEBHOOK_URL_MESSAGE = (
    f"{ALERT_WEBHOOK_URL_ENV} 미설정 — webhook provider 는 전송 대상 URL 없이 "
    "알림을 전송할 수 없음. deploy env 에 웹훅 URL 을 설정해야 함"
)


def csv_values(value: str) -> set[str]:
    return {item.strip().lower() for item in value.split(",") if item.strip()}


@dataclass(frozen=True)
class AlertPolicyDecision:
    """알림 정책 판정 결과 — allowed=False 면 reason 으로 거부 이벤트를 냄."""

    allowed: bool
    reason: str = ""


def check_alert_policy(evt: AlertRequestedBody) -> AlertPolicyDecision:
    """전송 전 정책 검증 — env 정책으로 severity와 자동 명령 환경을 제한."""
    blocked_severities = csv_values(env(ALERT_BLOCKED_SEVERITIES_ENV, ""))
    if evt.severity.strip().lower() in blocked_severities:
        return AlertPolicyDecision(allowed=False, reason=ALERT_SEVERITY_BLOCKED_REASON)
    allowed_auto_command_envs = csv_values(
        env(ALERT_AUTO_COMMAND_ENVIRONMENTS_ENV, DEFAULT_ALERT_AUTO_COMMAND_ENVIRONMENTS)
    )
    if (
        evt.next_command is not None
        and normalize_environment(evt.environment) not in allowed_auto_command_envs
    ):
        return AlertPolicyDecision(
            allowed=False,
            reason=AUTO_COMMAND_ENVIRONMENT_DENIED_REASON,
        )
    return AlertPolicyDecision(allowed=True)


def dispatched_body(alert: AlertRequestedBody, channel: str, mode: str) -> AlertDispatchedBody:
    return AlertDispatchedBody(
        cluster_id=alert.cluster_id,
        namespace=alert.namespace,
        severity=alert.severity,
        channel=channel,
        mode=mode,
        workspace_id=alert.workspace_id,
        application_id=alert.application_id,
        workflow_run_id=alert.workflow_run_id,
        binding_id=alert.binding_id,
        environment=alert.environment,
    )


class LogAlertProvider:
    """AlertProvider 구현 — 구조화 로그를 최소한의 정직한 싱크로 쓰는 기본 provider."""

    async def dispatch(self, alert: AlertRequestedBody) -> AlertDispatchedBody:
        LOGGER.info(
            "alert delivered to log sink",
            extra={
                "context": {
                    "cluster_id": alert.cluster_id,
                    "namespace": alert.namespace,
                    "severity": alert.severity,
                    "message": alert.message,
                    "reason": alert.reason,
                    "workspace_id": alert.workspace_id,
                }
            },
        )
        return dispatched_body(alert, channel=LOG_PROVIDER_NAME, mode=LOG_PROVIDER_NAME)


class WebhookAlertProvider:
    """AlertProvider 구현 — alert JSON 을 ALERT_WEBHOOK_URL 로 POST 함."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def dispatch(self, alert: AlertRequestedBody) -> AlertDispatchedBody:
        url = env(ALERT_WEBHOOK_URL_ENV, "").strip()
        if not url:
            raise RuntimeError(MISSING_WEBHOOK_URL_MESSAGE)
        timeout = float(env(ALERT_HTTP_TIMEOUT_SECONDS_ENV, DEFAULT_ALERT_HTTP_TIMEOUT_SECONDS))
        async with httpx.AsyncClient(timeout=timeout, transport=self.transport) as client:
            response = await client.post(url, json=alert.to_body())
            response.raise_for_status()
        return dispatched_body(alert, channel=WEBHOOK_PROVIDER_NAME, mode=WEBHOOK_PROVIDER_NAME)


def build_alert_provider(name: str | None = None) -> AlertProvider:
    """전송 전략 팩토리 — ALERT_PROVIDER env 로 선택(기본 log), 미지 값은 fail-fast.

    webhook 의 URL 부재는 부팅 실패가 아니라 요청 시점 alert.rejected 로 처리함.
    """
    provider = (name or env(ALERT_PROVIDER_ENV, LOG_PROVIDER_NAME)).strip().lower()
    if provider == LOG_PROVIDER_NAME:
        return LogAlertProvider()
    if provider == WEBHOOK_PROVIDER_NAME:
        return WebhookAlertProvider()
    raise RuntimeError(f"{ALERT_PROVIDER_ENV} 값이 지원되지 않음: {provider}")


# 전송 전략 주입 지점 — env 로 선택(log 기본, webhook 선택 가능).
ALERT_PROVIDER: AlertProvider = build_alert_provider()


@app.on(IncidentDetectedBody)
async def on_incident_detected(
    evt: IncidentDetectedBody,
    ctx: EventContext[object],
) -> None:
    """Store one in-app notification after RCA confirms an actual incident."""
    await persist_incident_alert_event(ctx.db, evt)


@app.on(IncidentResolvedBody)
async def on_incident_resolved(
    evt: IncidentResolvedBody,
    ctx: EventContext[object],
) -> None:
    """Close the matching app notification only after recovery is verified."""
    await resolve_incident_alert_event(ctx.db, evt)


async def dispatch_to_channel(
    alert: AlertRequestedBody, channel: dict[str, object]
) -> AlertDispatchedBody:
    """워크스페이스 채널 1개로 webhook 발송 — 채널 이름이 dispatched.channel 이 된다."""
    result = await post_alert_webhook(str(channel["url"]), alert)
    if not result.delivered:
        raise RuntimeError(result.error or "alert webhook failed")
    return dispatched_body(alert, channel=str(channel["name"]), mode=WEBHOOK_PROVIDER_NAME)


async def matching_channels(
    evt: AlertRequestedBody, ctx: EventContext[object]
) -> list[dict[str, object]]:
    """워크스페이스의 enabled 채널 중 min_severity 를 충족하는 것 — 저장소 없으면 빈 목록."""
    lister = getattr(ctx.db, "list_alert_channels", None)
    if lister is None:
        return []
    # 위치 인자만 사용 — 테스트 대역(범용 spy)과의 호환을 위해 kwargs 를 강제하지 않는다.
    loaded = lister(evt.workspace_id)
    channels = await loaded if inspect.isawaitable(loaded) else loaded
    channels = channels or []
    selected = set(evt.channel_ids) if evt.channel_ids is not None else None
    return [
        dict(channel)
        for channel in channels
        if bool(channel.get("enabled", True))
        and severity_matches(str(channel.get("min_severity", "warning")), evt.severity)
        and (selected is None or str(channel.get("channel_id") or "") in selected)
    ]


@app.on(AlertRequestedBody)
async def on_alert_requested(
    evt: AlertRequestedBody, ctx: EventContext[object]
) -> AsyncIterator[EventBody]:
    decision = check_alert_policy(evt)
    if not decision.allowed:
        LOGGER.warning("alert rejected", extra={"context": {"reason": decision.reason}})
        yield AlertRejectedBody(reason=decision.reason, requested=evt.to_body())
        return

    # 라우팅 룰 — 워크스페이스 채널이 있으면 severity 매칭 채널 전부로 발송.
    # 채널이 하나도 없으면 기존 전역 provider(env) 폴백: 도입 전과 동작 동일.
    channels = await matching_channels(evt, ctx)
    if channels:
        delivered = 0
        for channel in channels:
            try:
                yield await dispatch_to_channel(evt, channel)
                delivered += 1
            except Exception as exc:  # noqa: BLE001 - 채널별 실패는 다른 채널을 막지 않음
                LOGGER.warning(
                    "alert channel dispatch failed",
                    extra={
                        "context": {
                            "channel": channel.get("name"),
                            "severity": evt.severity,
                            "exception_type": type(exc).__name__,
                        }
                    },
                )
        if delivered == 0:
            # 전 채널 실패 — 전송 확인 없이는 next_command 를 이어주지 않음(fail-closed).
            yield AlertRejectedBody(reason=ALERT_DISPATCH_FAILED_REASON, requested=evt.to_body())
            return
        if evt.next_command is not None:
            yield evt.next_command
        return

    if evt.channel_ids is not None:
        # 규칙 전이는 선택 채널 밖으로 새지 않는다. 삭제·비활성·severity 불일치는
        # 전역 provider 폴백이 아니라 명시적 거부 이벤트로 남긴다.
        yield AlertRejectedBody(
            reason=ALERT_SELECTED_CHANNELS_UNAVAILABLE_REASON,
            requested=evt.to_body(),
        )
        return

    try:
        dispatched = await ALERT_PROVIDER.dispatch(evt)
    except Exception as exc:  # noqa: BLE001 - 외부 전송은 무엇이든 실패 가능
        # 전송 확인 없이는 next_command 를 이어주지 않음(fail-closed).
        LOGGER.warning(
            "alert dispatch failed",
            extra={
                "context": {
                    "cluster_id": evt.cluster_id,
                    "severity": evt.severity,
                    "exception_type": type(exc).__name__,
                    "detail": str(exc),
                }
            },
        )
        yield AlertRejectedBody(reason=ALERT_DISPATCH_FAILED_REASON, requested=evt.to_body())
        return

    LOGGER.info(
        "alert dispatched",
        extra={
            "context": {
                "cluster_id": dispatched.cluster_id,
                "severity": dispatched.severity,
                "channel": dispatched.channel,
                "mode": dispatched.mode,
            }
        },
    )
    yield dispatched
    if evt.next_command is not None:
        yield evt.next_command


async def run_alert_evaluation(
    engine: AlertEvaluationEngine,
    stopping: asyncio.Event,
) -> None:
    """Evaluate real measurements continuously without killing delivery on one bad cycle."""
    while not stopping.is_set():
        try:
            transitions = await engine.evaluate_once()
            if transitions:
                LOGGER.info(
                    "alert evaluation transitions",
                    extra={
                        CONTEXT_KEY: {
                            "count": len(transitions),
                            "transitions": [
                                str(transition.get("transition") or "")
                                for transition in transitions
                            ],
                        }
                    },
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # noqa: BLE001 - 다음 주기에 자동 복구해야 하는 운영 루프
            LOGGER.warning(
                "alert evaluation cycle failed",
                extra={CONTEXT_KEY: {"exception_type": type(exc).__name__}},
                exc_info=exc,
            )
        try:
            await asyncio.wait_for(stopping.wait(), timeout=engine.interval_seconds)
        except TimeoutError:
            continue


@dataclass(frozen=True)
class AlertRuleTransitionNotifier:
    """Stage rule transitions into the existing alert.requested delivery chain."""

    db: Database

    async def __call__(self, transition: dict[str, object]) -> None:
        def stage() -> None:
            with self.db.unit_of_work() as connection:
                self.stage(connection, transition)

        await run_sync_with_uow_affinity(stage, thread_runner=to_thread_db_retry)

    def stage(self, connection: Any, transition: dict[str, object]) -> None:
        """Join the evaluator's UoW so state and external delivery are atomic."""
        body = alert_request_for_rule_transition(transition)
        if body.channel_ids == []:
            # A rule with no selected external channels remains a valid in-app
            # alert.  Do not manufacture an alert.rejected outbox entry for it.
            return
        envelope = event(
            body.__subject__,
            app.name,
            body.to_body(),
            correlation_id=str(transition["event_id"]),
            workspace_id=body.workspace_id,
        )
        self.db.record_event(envelope)
        self.db.stage_events(connection, [envelope])


def alert_request_for_rule_transition(transition: dict[str, object]) -> AlertRequestedBody:
    subject = transition.get("subject")
    subject = subject if isinstance(subject, dict) else {}
    state = str(transition.get("transition") or "")
    state_label = "해소" if state == "resolved" else "발생"
    rule_name = str(transition.get("rule_name") or transition.get("rule_id") or "알림 규칙")
    channel_ids = transition.get("channel_ids")
    selected = (
        [str(channel_id) for channel_id in channel_ids]
        if isinstance(channel_ids, list | tuple)
        else None
    )
    return AlertRequestedBody(
        cluster_id=str(subject.get("cluster") or "unknown"),
        namespace=str(subject.get("namespace") or ""),
        severity=RULE_DELIVERY_SEVERITY.get(
            str(transition.get("severity") or "warning").strip().lower(),
            "warning",
        ),
        message=f"{rule_name} · {state_label}",
        reason=f"alert rule {state or 'firing'}",
        workspace_id=str(transition.get("workspace_id") or "default"),
        channel_ids=selected,
    )


async def serve_alert_worker() -> None:
    """Run the NATS delivery consumer and the DB-backed rule evaluator as one service."""
    evaluation_store = Database()
    await wait_for_database(evaluation_store)
    evaluation_db = AsyncDb(evaluation_store)
    interval_seconds = float(
        env(
            ALERT_EVALUATION_INTERVAL_SECONDS_ENV,
            DEFAULT_ALERT_EVALUATION_INTERVAL_SECONDS,
        )
    )
    measurement_max_age_seconds = float(
        env(
            ALERT_MEASUREMENT_MAX_AGE_SECONDS_ENV,
            str(DEFAULT_MEASUREMENT_MAX_AGE_SECONDS),
        )
    )
    loader = AlertRuleMeasurementLoader(
        evaluation_db,
        max_age_seconds=measurement_max_age_seconds,
    )
    engine = AlertEvaluationEngine(
        evaluation_db,
        load_measurements=loader,
        notify=AlertRuleTransitionNotifier(evaluation_store),
        interval_seconds=interval_seconds,
    )
    stopping = asyncio.Event()
    evaluation_task = asyncio.create_task(
        run_alert_evaluation(engine, stopping),
        name="alert-rule-evaluation",
    )
    try:
        await WorkerRuntime(app.handler_spec()).run()
    finally:
        stopping.set()
        await evaluation_task
        await evaluation_store.dispose_async()
        evaluation_store.dispose()


if __name__ == "__main__":
    AsyncService(app.name, serve_alert_worker).run()
