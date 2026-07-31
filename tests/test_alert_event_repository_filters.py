from contextlib import contextmanager
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.dialects import postgresql

from domains.alert.repository import AlertRuleRepository


class EmptyMappingsResult:
    def mappings(self) -> "EmptyMappingsResult":
        return self

    def all(self) -> list[dict[str, Any]]:
        return []


class CapturingConnection:
    def __init__(self) -> None:
        self.statement: object | None = None

    def execute(self, statement: object) -> EmptyMappingsResult:
        self.statement = statement
        return EmptyMappingsResult()


def repository_with_capture() -> tuple[AlertRuleRepository, CapturingConnection]:
    repository = object.__new__(AlertRuleRepository)
    connection = CapturingConnection()

    @contextmanager
    def open_connection():
        yield connection

    repository.connection = open_connection  # type: ignore[method-assign]
    return repository, connection


def test_alert_event_query_scopes_recovery_lookup_before_limit() -> None:
    repository, connection = repository_with_capture()

    rows = repository.list_alert_events(
        "workspace-1",
        rule_name="OpsiaSliFailureRatioHigh",
        source="alertmanager",
        incident_ids=("incident-1", "correlation-1", "incident-1"),
        limit=10,
    )

    assert rows == []
    assert connection.statement is not None
    sql = str(
        connection.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "alert_events.workspace_id = 'workspace-1'" in sql
    assert "alert_events.rule_name = 'OpsiaSliFailureRatioHigh'" in sql
    assert "alert_events.source = 'alertmanager'" in sql
    assert "alert_events.incident_id IN ('correlation-1', 'incident-1')" in sql
    assert "LIMIT 10" in sql


def test_alert_event_query_with_empty_incident_scope_fails_closed() -> None:
    repository, connection = repository_with_capture()

    rows = repository.list_alert_events(
        "workspace-1",
        incident_ids=("", "  "),
        limit=10,
    )

    assert rows == []
    assert connection.statement is None


def test_alert_event_query_scopes_original_and_refire_series() -> None:
    repository, connection = repository_with_capture()

    repository.list_alert_events(
        "workspace-1",
        from_time=datetime(2026, 7, 24, 1, tzinfo=UTC),
        rule_name="OpsiaSliFailureRatioHigh",
        source="alertmanager",
        event_ids=("alert-original",),
        subject_key="sandbox:Deployment:api-server",
        limit=500,
    )

    assert connection.statement is not None
    sql = str(
        connection.statement.compile(
            dialect=postgresql.dialect(),
            compile_kwargs={"literal_binds": True},
        )
    )
    assert "alert_events.fired_at >= '2026-07-24 01:00:00+00:00'" in sql
    assert "alert_events.event_id IN ('alert-original')" in sql
    assert (
        "alert_events.subject_key = 'sandbox:Deployment:api-server'"
        in sql
    )
    assert "LIMIT 500" in sql
