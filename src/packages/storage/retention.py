"""Bounded storage retention policies executed by command-janitor."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from packages.config.environments import normalize_environment
from packages.config.logs import get_logger
from packages.config.settings import env

OUTBOX_SENT_RETENTION_HOURS_ENV = "OUTBOX_SENT_RETENTION_HOURS"
UNSENT_OUTBOX_DLQ_AFTER_DAYS_ENV = "UNSENT_OUTBOX_DLQ_AFTER_DAYS"
EVENT_RETENTION_DAYS_ENV = "EVENT_RETENTION_DAYS"
AUDIT_LOG_RETENTION_DAYS_ENV = "AUDIT_LOG_RETENTION_DAYS"
AUDIT_LOG_RETENTION_ENABLED_ENV = "AUDIT_LOG_RETENTION_ENABLED"
DB_RETENTION_DELETE_LIMIT_ENV = "DB_RETENTION_DELETE_LIMIT"

DEMO_DATA_RETENTION_ENABLED_ENV = "DEMO_DATA_RETENTION_ENABLED"
DEMO_DATA_RETENTION_HOURS_ENV = "DEMO_DATA_RETENTION_HOURS"
DEMO_DATA_RETENTION_SCOPES_ENV = "DEMO_DATA_RETENTION_SCOPES"
DEMO_DATA_RETENTION_DELETE_LIMIT_ENV = "DEMO_DATA_RETENTION_DELETE_LIMIT"

DEFAULT_OUTBOX_SENT_RETENTION_HOURS = "24"
# 정상 환경에서는 이 나이까지 미발행 outbox 가 존재하지 않는다 — relay 부재/장애
# 환경에서만 동작하는 안전망이므로 보수적으로 7일.
DEFAULT_UNSENT_OUTBOX_DLQ_AFTER_DAYS = "7"
DEFAULT_EVENT_RETENTION_DAYS = "7"
DEFAULT_AUDIT_LOG_RETENTION_DAYS = "7"
DEFAULT_DB_RETENTION_DELETE_LIMIT = "1000"
DEFAULT_DEMO_DATA_RETENTION_HOURS = "24"
DEFAULT_DEMO_DATA_RETENTION_DELETE_LIMIT = "500"

DEMO_RUNTIME_ENVIRONMENTS = frozenset({"dev", "development", "demo", "sandbox", "local"})
DEMO_RETENTION_SCOPES = frozenset(
    {
        "observations",
        "events",
        "incidents",
        "rca",
        "evidence",
        "timeline",
        "commands",
        "projections",
    }
)

# These authorities are intentionally not expressible as demo retention scopes.
# Audit deletion remains a separate, explicit legal-policy switch.
PROTECTED_RETENTION_AUTHORITIES = (
    "identity",
    "workspace",
    "cluster-registration",
    "gitops-configuration",
    "audit-log",
    "alert-history",
)

LOGGER = get_logger(__name__)


@dataclass(frozen=True)
class DemoRetentionPolicy:
    enabled: bool
    retention_hours: int
    delete_limit: int
    scopes: tuple[str, ...]


@dataclass(frozen=True)
class RetentionSweepResult:
    outbox_sent: int = 0
    events: int = 0
    audit_log: int = 0
    # 장기 미발행 outbox 의 DLQ 이동 건수(삭제 아님 — 재처리 가능한 격리).
    outbox_unsent_dead_lettered: int = 0
    demo_deleted: tuple[tuple[str, int], ...] = ()
    # 실패한 단계 이름들 — 한 단계의 실패가 나머지 단계를 막지 않았음을 관측 가능하게 남긴다.
    errors: tuple[str, ...] = ()

    @property
    def total(self) -> int:
        return (
            self.outbox_sent
            + self.events
            + self.audit_log
            + self.outbox_unsent_dead_lettered
            + sum(count for _table, count in self.demo_deleted)
        )

    def metrics(self) -> dict[str, int]:
        return {
            "outbox_sent": self.outbox_sent,
            "events": self.events,
            "audit_log": self.audit_log,
            "outbox_unsent_dead_lettered": self.outbox_unsent_dead_lettered,
            **{f"demo_{table}": count for table, count in self.demo_deleted},
            "errors": len(self.errors),
            "total": self.total,
        }


def demo_retention_policy() -> DemoRetentionPolicy:
    """Return a fail-closed policy requiring environment, enablement, and scopes."""

    retention_hours = _positive_int(
        DEMO_DATA_RETENTION_HOURS_ENV,
        DEFAULT_DEMO_DATA_RETENTION_HOURS,
    )
    delete_limit = _positive_int(
        DEMO_DATA_RETENTION_DELETE_LIMIT_ENV,
        DEFAULT_DEMO_DATA_RETENTION_DELETE_LIMIT,
    )
    app_env = normalize_environment(env("APP_ENV", ""))
    requested = _enabled(env(DEMO_DATA_RETENTION_ENABLED_ENV, "false"))
    scopes = _demo_scopes(env(DEMO_DATA_RETENTION_SCOPES_ENV, ""))
    return DemoRetentionPolicy(
        enabled=requested and app_env in DEMO_RUNTIME_ENVIRONMENTS and bool(scopes),
        retention_hours=retention_hours,
        delete_limit=delete_limit,
        scopes=scopes,
    )


async def sweep_storage_retention(db: Any, *, now: datetime | None = None) -> RetentionSweepResult:
    """Delete at most one bounded batch per configured retention target.

    각 retention 단계는 개별 격리된다: 한 단계의 실패(미구현 메서드, 일시적
    DB 경합 등)가 나머지 단계의 정리까지 막으면 전체 retention 이 조용히
    마비되므로, 실패한 단계는 기록하고 다음 단계를 계속 진행한다.
    """

    observed_at = now or datetime.now(UTC)
    limit = _positive_int(DB_RETENTION_DELETE_LIMIT_ENV, DEFAULT_DB_RETENTION_DELETE_LIMIT)
    outbox_cutoff = observed_at - timedelta(
        hours=_positive_int(
            OUTBOX_SENT_RETENTION_HOURS_ENV,
            DEFAULT_OUTBOX_SENT_RETENTION_HOURS,
        )
    )
    event_cutoff = observed_at - timedelta(
        days=_positive_int(EVENT_RETENTION_DAYS_ENV, DEFAULT_EVENT_RETENTION_DAYS)
    )
    policy = demo_retention_policy()
    errors: list[str] = []
    demo_deleted: dict[str, int] = {}
    if policy.enabled:
        # The demo transaction removes event-processing children before the
        # legacy event-ledger sweep below removes their logical parent records.
        # 이 순서 의존은 demo 단계 성공 시의 삭제 순서 보장이며, demo 단계가
        # 실패해도 아래 단계들은 각자의 cutoff 기준으로 독립적으로 안전하다.
        try:
            demo_deleted = (
                await db.delete_demo_data_older_than(
                    observed_at - timedelta(hours=policy.retention_hours),
                    scopes=policy.scopes,
                    limit=policy.delete_limit,
                )
                or {}
            )
        except Exception:
            LOGGER.exception("retention_step_failed", extra={"context": {"step": "demo"}})
            errors.append("demo")

    outbox_count = 0
    try:
        outbox_count = await db.delete_sent_outbox_older_than(outbox_cutoff, limit=limit)
    except Exception:
        LOGGER.exception("retention_step_failed", extra={"context": {"step": "outbox"}})
        errors.append("outbox")

    unsent_dead_lettered = 0
    try:
        unsent_cutoff = observed_at - timedelta(
            days=_positive_int(
                UNSENT_OUTBOX_DLQ_AFTER_DAYS_ENV,
                DEFAULT_UNSENT_OUTBOX_DLQ_AFTER_DAYS,
            )
        )
        unsent_dead_lettered = await db.dead_letter_unsent_outbox_older_than(
            unsent_cutoff, limit=limit
        )
    except Exception:
        LOGGER.exception("retention_step_failed", extra={"context": {"step": "outbox_unsent"}})
        errors.append("outbox_unsent")

    event_count = 0
    try:
        event_count = await db.delete_events_older_than(event_cutoff, limit=limit)
    except Exception:
        LOGGER.exception("retention_step_failed", extra={"context": {"step": "events"}})
        errors.append("events")

    audit_count = 0
    if _enabled(env(AUDIT_LOG_RETENTION_ENABLED_ENV, "false")):
        audit_cutoff = observed_at - timedelta(
            days=_positive_int(
                AUDIT_LOG_RETENTION_DAYS_ENV,
                DEFAULT_AUDIT_LOG_RETENTION_DAYS,
            )
        )
        try:
            audit_count = await db.delete_audit_logs_older_than(audit_cutoff, limit=limit)
        except Exception:
            LOGGER.exception("retention_step_failed", extra={"context": {"step": "audit_log"}})
            errors.append("audit_log")

    return RetentionSweepResult(
        outbox_sent=outbox_count,
        events=event_count,
        audit_log=audit_count,
        outbox_unsent_dead_lettered=unsent_dead_lettered,
        demo_deleted=tuple(sorted((name, int(count)) for name, count in demo_deleted.items())),
        errors=tuple(errors),
    )


def _demo_scopes(value: str) -> tuple[str, ...]:
    scopes = tuple(sorted({item.strip().casefold() for item in value.split(",") if item.strip()}))
    unknown = set(scopes) - DEMO_RETENTION_SCOPES
    if unknown:
        raise ValueError(f"unknown demo retention scopes: {', '.join(sorted(unknown))}")
    return scopes


def _positive_int(name: str, default: str) -> int:
    try:
        value = int(env(name, default))
    except ValueError as exc:
        raise ValueError(f"{name} must be a positive integer") from exc
    if value < 1:
        raise ValueError(f"{name} must be a positive integer")
    return value


def _enabled(value: str) -> bool:
    normalized = value.strip().casefold()
    if normalized in {"1", "true", "yes", "on"}:
        return True
    if normalized in {"", "0", "false", "no", "off"}:
        return False
    raise ValueError("retention enablement must be a boolean")
