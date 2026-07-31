"""alert 도메인 repository — 알림 채널(라우팅 룰) CRUD 와 severity 매칭 기준."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from typing import Any

from sqlalchemy import case, delete, func, select, text
from sqlalchemy.dialects.postgresql import insert as pg_insert

from domains.alert.models import AlertChannel, AlertEvent, AlertRule, AlertRuleTargetState
from packages.contracts.event_bus.interfaces import JsonObject
from packages.storage.engine import DatabaseConnection, iso_or_none

# severity 순위 — 명확한 단일 기준. 미지 값은 warning 으로 취급(과소 통지 방지 절충).
SEVERITY_RANK: dict[str, int] = {"info": 0, "warning": 1, "critical": 2}
DEFAULT_SEVERITY_RANK = SEVERITY_RANK["warning"]
AlertTransitionStager = Callable[[Any, JsonObject], None]


def severity_rank(severity: str) -> int:
    return SEVERITY_RANK.get(severity.strip().lower(), DEFAULT_SEVERITY_RANK)


def severity_matches(min_severity: str, severity: str) -> bool:
    """채널의 min_severity 이상인 알림만 통과."""
    return severity_rank(severity) >= severity_rank(min_severity)


def serialize_alert_channel(row: JsonObject) -> JsonObject:
    item = dict(row)
    item["last_tested_at"] = iso_or_none(item.get("last_tested_at"))
    item["created_at"] = iso_or_none(item.get("created_at"))
    item["updated_at"] = iso_or_none(item.get("updated_at"))
    return item


def serialize_alert_rule(row: JsonObject) -> JsonObject:
    item = dict(row)
    item["last_fired_at"] = iso_or_none(item.get("last_fired_at"))
    item["created_at"] = iso_or_none(item.get("created_at"))
    item["updated_at"] = iso_or_none(item.get("updated_at"))
    return item


def serialize_alert_rule_target_state(row: JsonObject) -> JsonObject:
    item = dict(row)
    item["condition_since"] = iso_or_none(item.get("condition_since"))
    item["last_evaluated_at"] = iso_or_none(item.get("last_evaluated_at"))
    item["created_at"] = iso_or_none(item.get("created_at"))
    item["updated_at"] = iso_or_none(item.get("updated_at"))
    return item


def serialize_alert_event(row: JsonObject) -> JsonObject:
    item = dict(row)
    item["fired_at"] = iso_or_none(item.get("fired_at"))
    item["resolved_at"] = iso_or_none(item.get("resolved_at"))
    item["acknowledged_at"] = iso_or_none(item.get("acknowledged_at"))
    item["promoted_at"] = iso_or_none(item.get("promoted_at"))
    item["created_at"] = iso_or_none(item.get("created_at"))
    item["updated_at"] = iso_or_none(item.get("updated_at"))
    return item


class AlertChannelRepository(DatabaseConnection):
    def list_alert_channels(
        self, workspace_id: str, *, only_enabled: bool = False
    ) -> list[JsonObject]:
        table = AlertChannel.__table__
        statement = (
            select(table)
            .where(table.c.workspace_id == workspace_id)
            .order_by(table.c.name, table.c.channel_id)
        )
        if only_enabled:
            statement = statement.where(table.c.enabled.is_(True))
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [serialize_alert_channel(dict(row)) for row in rows]

    def get_alert_channel(self, workspace_id: str, channel_id: str) -> JsonObject | None:
        table = AlertChannel.__table__
        statement = (
            select(table)
            .where(table.c.workspace_id == workspace_id, table.c.channel_id == channel_id)
            .limit(1)
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        return serialize_alert_channel(dict(row)) if row else None

    def upsert_alert_channel(self, payload: JsonObject) -> JsonObject:
        table = AlertChannel.__table__
        channel_id = str(payload.get("channel_id") or f"chan-{uuid.uuid4().hex[:16]}")
        values = {
            "channel_id": channel_id,
            "workspace_id": str(payload["workspace_id"]),
            "name": str(payload["name"]),
            "kind": str(payload.get("kind") or "webhook"),
            "url": str(payload["url"]),
            "min_severity": str(payload.get("min_severity") or "warning").strip().lower(),
            "enabled": bool(payload.get("enabled", True)),
        }
        insert = pg_insert(table).values(**values, updated_at=func.now())
        statement = insert.on_conflict_do_update(
            index_elements=[table.c.channel_id],
            set_={
                "name": insert.excluded.name,
                "kind": insert.excluded.kind,
                "url": insert.excluded.url,
                "min_severity": insert.excluded.min_severity,
                "enabled": insert.excluded.enabled,
                # Delivery evidence is valid only for the exact tested webhook URL.
                "last_tested_at": case(
                    (table.c.url != insert.excluded.url, None), else_=table.c.last_tested_at
                ),
                "last_test_status": case(
                    (table.c.url != insert.excluded.url, None), else_=table.c.last_test_status
                ),
                "last_test_detail": case(
                    (table.c.url != insert.excluded.url, None), else_=table.c.last_test_detail
                ),
                "last_test_status_code": case(
                    (table.c.url != insert.excluded.url, None), else_=table.c.last_test_status_code
                ),
                "updated_at": func.now(),
            },
            where=table.c.workspace_id == insert.excluded.workspace_id,
        ).returning(table)
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        if row:
            return serialize_alert_channel(dict(row))
        raise LookupError("alert channel not found in workspace")

    def delete_alert_channel(self, workspace_id: str, channel_id: str) -> bool:
        table = AlertChannel.__table__
        statement = (
            delete(table)
            .where(table.c.workspace_id == workspace_id, table.c.channel_id == channel_id)
            .returning(table.c.channel_id)
        )
        with self.connection() as conn:
            row = conn.execute(statement).first()
        return row is not None

    def record_alert_channel_test(
        self,
        workspace_id: str,
        channel_id: str,
        *,
        status: str,
        detail: str,
        status_code: int | None = None,
    ) -> JsonObject:
        table = AlertChannel.__table__
        statement = (
            table.update()
            .where(table.c.workspace_id == workspace_id, table.c.channel_id == channel_id)
            .values(
                last_tested_at=func.now(),
                last_test_status=status,
                last_test_detail=detail,
                last_test_status_code=status_code,
                updated_at=func.now(),
            )
            .returning(table)
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        if row:
            return serialize_alert_channel(dict(row))
        raise LookupError("alert channel not found in workspace")


class AlertRuleRepository(DatabaseConnection):
    """Opsia 소유 알림 규칙 저장소. Git/클러스터 writer 경계를 타지 않는다."""

    def create_alert_rule(self, payload: JsonObject) -> JsonObject:
        table = AlertRule.__table__
        statement = (
            pg_insert(table)
            .values(
                rule_id=str(payload["rule_id"]),
                workspace_id=str(payload["workspace_id"]),
                name=str(payload["name"]),
                scope=dict(payload["scope"]),
                metric=str(payload["metric"]),
                comparator=str(payload["comparator"]),
                threshold=float(payload["threshold"]),
                for_seconds=int(payload["for_seconds"]),
                severity=str(payload["severity"]),
                channels=list(payload.get("channels") or []),
                enabled=bool(payload["enabled"]),
                created_by=str(payload["created_by"]),
                updated_at=func.now(),
            )
            .returning(table)
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().one()
        return serialize_alert_rule(dict(row))

    def list_alert_rules(self, workspace_id: str) -> list[JsonObject]:
        table = AlertRule.__table__
        statement = (
            select(table)
            .where(table.c.workspace_id == workspace_id)
            .order_by(table.c.updated_at.desc(), table.c.rule_id)
        )
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [serialize_alert_rule(dict(row)) for row in rows]

    def list_enabled_alert_rules(self) -> list[JsonObject]:
        table = AlertRule.__table__
        statement = (
            select(table)
            .where(table.c.enabled.is_(True))
            .order_by(table.c.workspace_id, table.c.rule_id)
        )
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [serialize_alert_rule(dict(row)) for row in rows]

    def update_alert_rule(
        self,
        workspace_id: str,
        rule_id: str,
        changes: JsonObject,
    ) -> JsonObject | None:
        table = AlertRule.__table__
        statement = (
            table.update()
            .where(table.c.workspace_id == workspace_id, table.c.rule_id == rule_id)
            .values(**changes, updated_at=func.now())
            .returning(table)
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        return serialize_alert_rule(dict(row)) if row is not None else None

    def delete_alert_rule(self, workspace_id: str, rule_id: str) -> bool:
        table = AlertRule.__table__
        statement = (
            delete(table)
            .where(table.c.workspace_id == workspace_id, table.c.rule_id == rule_id)
            .returning(table.c.rule_id)
        )
        with self.connection() as conn:
            row = conn.execute(statement).first()
        return row is not None

    def list_alert_events(
        self,
        workspace_id: str,
        *,
        from_time: object | None = None,
        to_time: object | None = None,
        rule_id: str | None = None,
        rule_name: str | None = None,
        source: str | None = None,
        incident_ids: tuple[str, ...] | None = None,
        event_ids: tuple[str, ...] | None = None,
        subject_key: str | None = None,
        severity: str | None = None,
        status: str | None = None,
        limit: int = 100,
    ) -> list[JsonObject]:
        """해소 이력을 삭제하지 않고 현재 워크스페이스의 발생 원장을 조회한다."""
        table = AlertEvent.__table__
        statement = select(table).where(table.c.workspace_id == workspace_id)
        if from_time is not None:
            statement = statement.where(table.c.fired_at >= from_time)
        if to_time is not None:
            statement = statement.where(table.c.fired_at <= to_time)
        if rule_id is not None:
            statement = statement.where(table.c.rule_id == rule_id)
        if rule_name is not None:
            statement = statement.where(table.c.rule_name == rule_name)
        if source is not None:
            statement = statement.where(table.c.source == source)
        if incident_ids is not None:
            normalized_incident_ids = tuple(
                sorted({value.strip() for value in incident_ids if value.strip()})
            )
            if not normalized_incident_ids:
                return []
            statement = statement.where(
                table.c.incident_id.in_(normalized_incident_ids)
            )
        if event_ids is not None:
            normalized_event_ids = tuple(
                sorted({value.strip() for value in event_ids if value.strip()})
            )
            if not normalized_event_ids:
                return []
            statement = statement.where(table.c.event_id.in_(normalized_event_ids))
        if subject_key is not None:
            statement = statement.where(table.c.subject_key == subject_key)
        if severity is not None:
            statement = statement.where(table.c.severity == severity)
        if status is not None:
            statement = statement.where(table.c.status == status)
        statement = statement.order_by(table.c.fired_at.desc(), table.c.event_id).limit(limit)
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [serialize_alert_event(dict(row)) for row in rows]

    def upsert_external_alert_event(self, payload: JsonObject) -> JsonObject:
        """Persist one Alertmanager occurrence using its deterministic event id.

        Alertmanager repeats firing notifications until an alert resolves.  The
        primary-key upsert keeps those repeats as one durable in-app alert and
        lets the eventual resolved webhook update that same record.
        """
        table = AlertEvent.__table__
        insert = pg_insert(table).values(**payload, updated_at=func.now())
        incident_changed = (
            insert.excluded.incident_id.is_not(None)
            & table.c.incident_id.is_not(None)
            & (insert.excluded.incident_id != table.c.incident_id)
        )
        resolved_is_terminal = (
            (table.c.status == "resolved")
            & (insert.excluded.status != "resolved")
            & ~incident_changed
        )
        statement = insert.on_conflict_do_update(
            index_elements=[table.c.event_id],
            set_={
                "workspace_id": insert.excluded.workspace_id,
                "rule_name": insert.excluded.rule_name,
                "source": insert.excluded.source,
                "severity": insert.excluded.severity,
                "subject_key": insert.excluded.subject_key,
                "subject": insert.excluded.subject,
                # Alertmanager retries can arrive out of order.  A delayed
                # ``firing`` notification must never resurrect an occurrence
                # already durably marked resolved.
                "resolved_at": case(
                    (resolved_is_terminal, table.c.resolved_at),
                    else_=insert.excluded.resolved_at,
                ),
                "status": case(
                    (resolved_is_terminal, table.c.status),
                    else_=insert.excluded.status,
                ),
                "observed_value": case(
                    (resolved_is_terminal, table.c.observed_value),
                    else_=insert.excluded.observed_value,
                ),
                "threshold": case(
                    (resolved_is_terminal, table.c.threshold),
                    else_=insert.excluded.threshold,
                ),
                "series_identity": func.coalesce(
                    insert.excluded.series_identity,
                    table.c.series_identity,
                ),
                "evidence": case(
                    (resolved_is_terminal, table.c.evidence),
                    else_=insert.excluded.evidence,
                ),
                "incident_id": func.coalesce(insert.excluded.incident_id, table.c.incident_id),
                "acknowledged_at": case(
                    (incident_changed, None),
                    else_=table.c.acknowledged_at,
                ),
                "acknowledged_by": case(
                    (incident_changed, None),
                    else_=table.c.acknowledged_by,
                ),
                "promoted_at": case(
                    (incident_changed, None),
                    else_=table.c.promoted_at,
                ),
                "promoted_by": case(
                    (incident_changed, None),
                    else_=table.c.promoted_by,
                ),
                "updated_at": func.now(),
            },
        ).returning(table)
        with self.connection() as conn:
            row = conn.execute(statement).mappings().one()
        return serialize_alert_event(dict(row))

    def upsert_incident_alert_event(self, payload: JsonObject) -> JsonObject:
        """Persist one confirmed incident without duplicating its external alert.

        Alertmanager writes its alert event before the asynchronous RCA pipeline
        confirms the incident.  Reuse that earlier row when both records carry
        the same incident id; otherwise create one replay-safe incident event.
        Existing rows are intentionally left untouched so an event-bus replay
        cannot undo an acknowledgement or a resolution.
        """
        table = AlertEvent.__table__
        workspace_id = str(payload["workspace_id"])
        incident_id = str(payload["incident_id"])
        event_id = str(payload["event_id"])
        with self.unit_of_work() as conn:
            existing = (
                conn.execute(
                    select(table)
                    .where(
                        table.c.workspace_id == workspace_id,
                        table.c.incident_id == incident_id,
                    )
                    .order_by(table.c.fired_at, table.c.event_id)
                    .limit(1)
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if existing is not None:
                return serialize_alert_event(dict(existing))

            inserted = (
                conn.execute(
                    pg_insert(table)
                    .values(**payload, updated_at=func.now())
                    .on_conflict_do_nothing(index_elements=[table.c.event_id])
                    .returning(table)
                )
                .mappings()
                .first()
            )
            if inserted is not None:
                return serialize_alert_event(dict(inserted))

            replayed = (
                conn.execute(
                    select(table)
                    .where(
                        table.c.workspace_id == workspace_id,
                        table.c.event_id == event_id,
                    )
                    .limit(1)
                )
                .mappings()
                .one()
            )
        return serialize_alert_event(dict(replayed))

    def acknowledge_alert_event(
        self,
        workspace_id: str,
        event_id: str,
        actor_id: str,
    ) -> JsonObject | None:
        """활성 발생을 확인하고 최초 확인 주체를 원장에 남긴다."""
        table = AlertEvent.__table__
        with self.unit_of_work() as conn:
            current = (
                conn.execute(
                    select(table)
                    .where(
                        table.c.workspace_id == workspace_id,
                        table.c.event_id == event_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if current is None:
                return None
            if current["status"] == "resolved":
                raise ValueError("resolved alert event cannot be acknowledged")
            if current["status"] == "acked":
                return serialize_alert_event(dict(current))
            row = (
                conn.execute(
                    table.update()
                    .where(
                        table.c.workspace_id == workspace_id,
                        table.c.event_id == event_id,
                    )
                    .values(
                        status="acked",
                        acknowledged_at=func.now(),
                        acknowledged_by=actor_id,
                        updated_at=func.now(),
                    )
                    .returning(table)
                )
                .mappings()
                .one()
            )
        return serialize_alert_event(dict(row))

    def resolve_incident_alert_events(
        self,
        workspace_id: str,
        incident_id: str,
    ) -> int:
        """Resolve every durable notification linked to one terminal incident."""
        table = AlertEvent.__table__
        statement = (
            table.update()
            .where(
                table.c.workspace_id == workspace_id,
                table.c.incident_id == incident_id,
                table.c.status.in_(("firing", "acked")),
            )
            .values(
                status="resolved",
                resolved_at=func.now(),
                updated_at=func.now(),
            )
        )
        with self.connection() as conn:
            result = conn.execute(statement)
        return int(result.rowcount or 0)

    def promote_alert_event(
        self,
        workspace_id: str,
        event_id: str,
        incident_id: str,
        actor_id: str,
    ) -> tuple[JsonObject, bool] | None:
        """발생을 한 번만 인시던트에 연결하고 승격 주체를 기록한다."""
        table = AlertEvent.__table__
        with self.unit_of_work() as conn:
            current = (
                conn.execute(
                    select(table)
                    .where(
                        table.c.workspace_id == workspace_id,
                        table.c.event_id == event_id,
                    )
                    .with_for_update()
                )
                .mappings()
                .first()
            )
            if current is None:
                return None
            if current["incident_id"]:
                return serialize_alert_event(dict(current)), False
            row = (
                conn.execute(
                    table.update()
                    .where(
                        table.c.workspace_id == workspace_id,
                        table.c.event_id == event_id,
                    )
                    .values(
                        incident_id=incident_id,
                        promoted_at=func.now(),
                        promoted_by=actor_id,
                        updated_at=func.now(),
                    )
                    .returning(table)
                )
                .mappings()
                .one()
            )
        return serialize_alert_event(dict(row)), True

    def get_alert_rule_target_state(
        self,
        workspace_id: str,
        rule_id: str,
        subject_key: str,
    ) -> JsonObject | None:
        table = AlertRuleTargetState.__table__
        statement = (
            select(table)
            .where(
                table.c.workspace_id == workspace_id,
                table.c.rule_id == rule_id,
                table.c.subject_key == subject_key,
            )
            .limit(1)
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        return serialize_alert_rule_target_state(dict(row)) if row is not None else None

    def upsert_alert_rule_target_state(self, payload: JsonObject) -> JsonObject:
        table = AlertRuleTargetState.__table__
        insert = pg_insert(table).values(**payload, updated_at=func.now())
        statement = insert.on_conflict_do_update(
            index_elements=[table.c.rule_id, table.c.subject_key],
            set_={
                "workspace_id": insert.excluded.workspace_id,
                "subject": insert.excluded.subject,
                "condition_since": insert.excluded.condition_since,
                "active_event_id": insert.excluded.active_event_id,
                "last_observed_value": insert.excluded.last_observed_value,
                "last_evidence": insert.excluded.last_evidence,
                "last_evaluated_at": insert.excluded.last_evaluated_at,
                "updated_at": func.now(),
            },
        ).returning(table)
        with self.connection() as conn:
            row = conn.execute(statement).mappings().one()
        return serialize_alert_rule_target_state(dict(row))

    def activate_alert_rule_event(
        self,
        state: JsonObject,
        event: JsonObject,
        *,
        stage_transition: AlertTransitionStager | None = None,
    ) -> tuple[JsonObject, bool]:
        state_table = AlertRuleTargetState.__table__
        event_table = AlertEvent.__table__
        rule_table = AlertRule.__table__
        with self.unit_of_work() as conn:
            insert = pg_insert(event_table).values(**event)
            saved_event = (
                conn.execute(
                    insert.on_conflict_do_nothing(
                        index_elements=[event_table.c.rule_id, event_table.c.subject_key],
                        index_where=text("status in ('firing', 'acked')"),
                    ).returning(event_table)
                )
                .mappings()
                .first()
            )
            created = saved_event is not None
            if saved_event is None:
                saved_event = (
                    conn.execute(
                        select(event_table).where(
                            event_table.c.workspace_id == state["workspace_id"],
                            event_table.c.rule_id == state["rule_id"],
                            event_table.c.subject_key == state["subject_key"],
                            event_table.c.status.in_(("firing", "acked")),
                        )
                    )
                    .mappings()
                    .one()
                )
            active_event_id = str(saved_event["event_id"])
            state_insert = pg_insert(state_table).values(
                **state,
                active_event_id=active_event_id,
                updated_at=func.now(),
            )
            conn.execute(
                state_insert.on_conflict_do_update(
                    index_elements=[state_table.c.rule_id, state_table.c.subject_key],
                    set_={
                        "workspace_id": state_insert.excluded.workspace_id,
                        "subject": state_insert.excluded.subject,
                        "condition_since": state_insert.excluded.condition_since,
                        "active_event_id": state_insert.excluded.active_event_id,
                        "last_observed_value": state_insert.excluded.last_observed_value,
                        "last_evidence": state_insert.excluded.last_evidence,
                        "last_evaluated_at": state_insert.excluded.last_evaluated_at,
                        "updated_at": func.now(),
                    },
                )
            )
            if created:
                conn.execute(
                    rule_table.update()
                    .where(
                        rule_table.c.workspace_id == state["workspace_id"],
                        rule_table.c.rule_id == state["rule_id"],
                    )
                    .values(
                        last_fired_at=event["fired_at"],
                        occurrence_count=rule_table.c.occurrence_count + 1,
                        updated_at=func.now(),
                    )
                )
            serialized = serialize_alert_event(dict(saved_event))
            if created and stage_transition is not None:
                # The rule state and delivery outbox must cross the commit boundary
                # together.  A staging failure raises out of this UoW so the next
                # evaluator cycle can retry the same transition.
                stage_transition(conn, serialized)
        return serialized, created

    def refresh_alert_rule_event(
        self,
        workspace_id: str,
        event_id: str,
        *,
        observed_value: float,
        evidence: list[JsonObject],
        evaluated_at: object,
    ) -> None:
        table = AlertEvent.__table__
        statement = (
            table.update()
            .where(
                table.c.workspace_id == workspace_id,
                table.c.event_id == event_id,
                table.c.status.in_(("firing", "acked")),
            )
            .values(
                observed_value=observed_value,
                evidence=evidence,
                updated_at=evaluated_at,
            )
        )
        with self.connection() as conn:
            conn.execute(statement)

    def resolve_alert_rule_event(
        self,
        state: JsonObject,
        *,
        observed_value: float,
        evidence: list[JsonObject],
        resolved_at: object,
        stage_transition: AlertTransitionStager | None = None,
    ) -> JsonObject | None:
        event_table = AlertEvent.__table__
        state_table = AlertRuleTargetState.__table__
        with self.unit_of_work() as conn:
            row = (
                conn.execute(
                    event_table.update()
                    .where(
                        event_table.c.workspace_id == state["workspace_id"],
                        event_table.c.event_id == state["active_event_id"],
                        event_table.c.status.in_(("firing", "acked")),
                    )
                    .values(
                        status="resolved",
                        observed_value=observed_value,
                        evidence=evidence,
                        resolved_at=resolved_at,
                        updated_at=resolved_at,
                    )
                    .returning(event_table)
                )
                .mappings()
                .first()
            )
            if row is None:
                return None
            conn.execute(
                state_table.update()
                .where(
                    state_table.c.workspace_id == state["workspace_id"],
                    state_table.c.rule_id == state["rule_id"],
                    state_table.c.subject_key == state["subject_key"],
                )
                .values(
                    condition_since=None,
                    active_event_id=None,
                    last_observed_value=observed_value,
                    last_evidence=evidence,
                    last_evaluated_at=resolved_at,
                    updated_at=func.now(),
                )
            )
            serialized = serialize_alert_event(dict(row))
            if stage_transition is not None:
                # Resolution delivery is just as durable as firing delivery: if
                # outbox staging fails, keep the event active and retry later.
                stage_transition(conn, serialized)
        return serialized
