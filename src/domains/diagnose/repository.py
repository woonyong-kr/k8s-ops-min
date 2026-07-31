"""PostgreSQL authority for durable Diagnose runs and ordered transcript replay."""

from __future__ import annotations

from typing import Any

from sqlalchemy import delete, func, select, update
from sqlalchemy.dialects.postgresql import insert as pg_insert

from domains.diagnose.models import (
    DiagnoseConsentRecord,
    DiagnoseEventCursorRecord,
    DiagnoseEventRecord,
    DiagnoseRunRecord,
)
from packages.contracts.diagnose import (
    DiagnoseAgentSelection,
    DiagnoseEvent,
    DiagnoseEventDraft,
    DiagnoseEventReplay,
    DiagnoseRun,
    DiagnoseRunCreation,
    DiagnoseRunList,
    DiagnoseRunStatus,
    DiagnoseRunTransition,
    DiagnoseTarget,
)
from packages.contracts.parity import ClusterScope
from packages.storage.engine import DatabaseConnection

TERMINAL_STATUSES = frozenset({"completed", "failed", "stopped", "stale", "unavailable"})
REPLAY_LIMIT = 500


class DiagnoseRepository(DatabaseConnection):
    async def create_or_get_active(
        self,
        run: DiagnoseRun,
        initial_event: DiagnoseEventDraft,
    ) -> DiagnoseRunCreation:
        table = DiagnoseRunRecord.__table__
        with self.connection() as conn:
            inserted = (
                conn.execute(
                    pg_insert(table)
                    .values(**_run_values(run))
                    .on_conflict_do_nothing(
                        index_elements=[table.c.workspace_id, table.c.deduplication_key],
                        index_where=table.c.active.is_(True),
                    )
                    .returning(table)
                )
                .mappings()
                .first()
            )
            if inserted is None:
                existing = (
                    conn.execute(
                        select(table)
                        .where(
                            table.c.workspace_id == run.target.scope.workspace_id,
                            table.c.deduplication_key == run.deduplication_key,
                            table.c.active.is_(True),
                        )
                        .limit(1)
                    )
                    .mappings()
                    .first()
                )
                if existing is None:
                    raise RuntimeError("active Diagnose deduplication conflict was not readable")
                return DiagnoseRunCreation(run=_run_from_row(existing), created=False)

            persisted = _run_from_row(inserted)
            event = _append_event(conn, persisted, initial_event)
            return DiagnoseRunCreation(run=persisted, created=True, initial_event=event)

    async def get_run(self, *, scope: ClusterScope, run_id: str) -> DiagnoseRun | None:
        table = DiagnoseRunRecord.__table__
        with self.connection() as conn:
            row = (
                conn.execute(
                    select(table)
                    .where(
                        table.c.workspace_id == scope.workspace_id,
                        table.c.cluster_id == scope.cluster_id,
                        table.c.run_id == run_id,
                    )
                    .limit(1)
                )
                .mappings()
                .first()
            )
        return _run_from_row(row) if row is not None else None

    async def get_user_run(
        self,
        *,
        workspace_id: str,
        requested_by: str,
        run_id: str,
    ) -> DiagnoseRun | None:
        table = DiagnoseRunRecord.__table__
        with self.connection() as conn:
            row = (
                conn.execute(
                    select(table)
                    .where(
                        table.c.workspace_id == workspace_id,
                        table.c.requested_by == requested_by,
                        table.c.run_id == run_id,
                    )
                    .limit(1)
                )
                .mappings()
                .first()
            )
        return _run_from_row(row) if row is not None else None

    async def list_runs(
        self,
        *,
        workspace_id: str,
        requested_by: str,
        limit: int,
    ) -> DiagnoseRunList:
        effective_limit = max(1, min(limit, 100))
        table = DiagnoseRunRecord.__table__
        with self.connection() as conn:
            rows = (
                conn.execute(
                    select(table)
                    .where(
                        table.c.workspace_id == workspace_id,
                        table.c.requested_by == requested_by,
                    )
                    .order_by(table.c.updated_at.desc(), table.c.run_id.desc())
                    .limit(effective_limit + 1)
                )
                .mappings()
                .all()
            )
        complete = len(rows) <= effective_limit
        return DiagnoseRunList(
            runs=tuple(_run_from_row(row) for row in rows[:effective_limit]),
            complete=complete,
            reason_codes=() if complete else ("history_limit_reached",),
        )

    async def transition(
        self,
        run: DiagnoseRun,
        *,
        expected_statuses: tuple[DiagnoseRunStatus, ...],
        next_status: DiagnoseRunStatus,
        event: DiagnoseEventDraft,
        status_reason: str | None = None,
    ) -> DiagnoseRunTransition:
        table = DiagnoseRunRecord.__table__
        terminal = next_status in TERMINAL_STATUSES
        with self.connection() as conn:
            row = (
                conn.execute(
                    update(table)
                    .where(
                        table.c.workspace_id == run.target.scope.workspace_id,
                        table.c.run_id == run.run_id,
                        table.c.status.in_(expected_statuses),
                    )
                    .values(
                        status=next_status,
                        status_reason=status_reason,
                        active=not terminal,
                        updated_at=event.occurred_at,
                    )
                    .returning(table)
                )
                .mappings()
                .first()
            )
            if row is None:
                current = (
                    conn.execute(
                        select(table)
                        .where(
                            table.c.workspace_id == run.target.scope.workspace_id,
                            table.c.run_id == run.run_id,
                        )
                        .limit(1)
                    )
                    .mappings()
                    .first()
                )
                if current is None:
                    return DiagnoseRunTransition(run=run, changed=False)
                return DiagnoseRunTransition(run=_run_from_row(current), changed=False)
            persisted = _run_from_row(row)
            persisted_event = _append_event(conn, persisted, event)
            return DiagnoseRunTransition(
                run=persisted,
                changed=True,
                event=persisted_event,
            )

    async def append_event(
        self,
        run: DiagnoseRun,
        event: DiagnoseEventDraft,
    ) -> DiagnoseEvent:
        with self.connection() as conn:
            return _append_event(conn, run, event)

    async def replay(
        self,
        *,
        scope: ClusterScope,
        run_id: str,
        after_sequence: int,
    ) -> DiagnoseEventReplay:
        table = DiagnoseEventRecord.__table__
        with self.connection() as conn:
            bounds = (
                conn.execute(
                    select(
                        func.min(table.c.sequence).label("earliest"),
                        func.max(table.c.sequence).label("high_water"),
                    ).where(
                        table.c.workspace_id == scope.workspace_id,
                        table.c.run_id == run_id,
                    )
                )
                .mappings()
                .one()
            )
            earliest = int(bounds["earliest"]) if bounds["earliest"] is not None else None
            high_water = int(bounds["high_water"] or 0)
            if earliest is not None and after_sequence < earliest - 1:
                return DiagnoseEventReplay(
                    run_id=run_id,
                    state="resync_required",
                    requested_after_sequence=after_sequence,
                    next_cursor_sequence=after_sequence,
                    high_water_sequence=high_water,
                    earliest_available_sequence=earliest,
                )
            rows = (
                conn.execute(
                    select(table)
                    .where(
                        table.c.workspace_id == scope.workspace_id,
                        table.c.run_id == run_id,
                        table.c.sequence > after_sequence,
                    )
                    .order_by(table.c.sequence.asc())
                    .limit(REPLAY_LIMIT)
                )
                .mappings()
                .all()
            )
        events = tuple(_event_from_row(row) for row in rows)
        return DiagnoseEventReplay(
            run_id=run_id,
            state="available",
            requested_after_sequence=after_sequence,
            next_cursor_sequence=events[-1].sequence if events else after_sequence,
            high_water_sequence=high_water,
            events=events,
        )

    async def clear_finished(
        self,
        *,
        workspace_id: str,
        requested_by: str,
    ) -> int:
        table = DiagnoseRunRecord.__table__
        with self.connection() as conn:
            deleted = conn.execute(
                delete(table)
                .where(
                    table.c.workspace_id == workspace_id,
                    table.c.requested_by == requested_by,
                    table.c.active.is_(False),
                )
                .returning(table.c.run_id)
            ).all()
        return len(deleted)

    async def has_consent(
        self,
        *,
        workspace_id: str,
        requested_by: str,
        agent_id: str,
        disclosure_revision: str,
        surface: str,
    ) -> bool:
        table = DiagnoseConsentRecord.__table__
        with self.connection() as conn:
            found = conn.execute(
                select(table.c.agent_id)
                .where(
                    table.c.workspace_id == workspace_id,
                    table.c.requested_by == requested_by,
                    table.c.agent_id == agent_id,
                    table.c.disclosure_revision == disclosure_revision,
                    table.c.surface == surface,
                )
                .limit(1)
            ).scalar_one_or_none()
        return found is not None

    async def record_consent(
        self,
        *,
        workspace_id: str,
        requested_by: str,
        agent_id: str,
        disclosure_revision: str,
        surface: str,
    ) -> None:
        table = DiagnoseConsentRecord.__table__
        with self.connection() as conn:
            conn.execute(
                pg_insert(table)
                .values(
                    workspace_id=workspace_id,
                    requested_by=requested_by,
                    agent_id=agent_id,
                    disclosure_revision=disclosure_revision,
                    surface=surface,
                )
                .on_conflict_do_nothing(
                    index_elements=[
                        table.c.workspace_id,
                        table.c.requested_by,
                        table.c.agent_id,
                        table.c.disclosure_revision,
                        table.c.surface,
                    ]
                )
            )


def _append_event(conn: Any, run: DiagnoseRun, draft: DiagnoseEventDraft) -> DiagnoseEvent:
    cursor = DiagnoseEventCursorRecord.__table__
    table = DiagnoseEventRecord.__table__
    conn.execute(
        pg_insert(cursor)
        .values(
            run_id=run.run_id,
            workspace_id=run.target.scope.workspace_id,
            last_sequence=0,
        )
        .on_conflict_do_nothing(index_elements=[cursor.c.run_id])
    )
    sequence = conn.execute(
        update(cursor)
        .where(
            cursor.c.run_id == run.run_id,
            cursor.c.workspace_id == run.target.scope.workspace_id,
        )
        .values(last_sequence=cursor.c.last_sequence + 1, updated_at=draft.occurred_at)
        .returning(cursor.c.last_sequence)
    ).scalar_one()
    row = (
        conn.execute(
            pg_insert(table)
            .values(
                run_id=run.run_id,
                sequence=int(sequence),
                workspace_id=run.target.scope.workspace_id,
                kind=draft.kind,
                payload=dict(draft.payload),
                occurred_at=draft.occurred_at,
            )
            .returning(table)
        )
        .mappings()
        .one()
    )
    return _event_from_row(row)


def _run_values(run: DiagnoseRun) -> dict[str, Any]:
    return {
        "run_id": run.run_id,
        "workspace_id": run.target.scope.workspace_id,
        "cluster_id": run.target.scope.cluster_id,
        "requested_by": run.requested_by,
        "status": run.status,
        "status_reason": run.status_reason,
        "target_key": run.target_key,
        "deduplication_key": run.deduplication_key,
        "target": run.target.model_dump(mode="json"),
        "agent": run.agent.model_dump(mode="json", exclude_none=True),
        "active": run.status not in TERMINAL_STATUSES,
        "created_at": run.created_at,
        "updated_at": run.updated_at,
    }


def _run_from_row(row: Any) -> DiagnoseRun:
    return DiagnoseRun(
        run_id=str(row["run_id"]),
        target=DiagnoseTarget.model_validate(row["target"]),
        agent=DiagnoseAgentSelection.model_validate(row["agent"]),
        requested_by=str(row["requested_by"]),
        status=str(row["status"]),
        status_reason=row["status_reason"],
        target_key=str(row["target_key"]),
        deduplication_key=str(row["deduplication_key"]),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _event_from_row(row: Any) -> DiagnoseEvent:
    return DiagnoseEvent(
        run_id=str(row["run_id"]),
        sequence=int(row["sequence"]),
        kind=str(row["kind"]),
        payload=dict(row["payload"]),
        occurred_at=row["occurred_at"],
    )
