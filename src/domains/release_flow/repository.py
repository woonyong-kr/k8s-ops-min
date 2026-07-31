"""Release-flow repository."""

from __future__ import annotations

import hashlib
import uuid
from collections import defaultdict
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import and_, delete, func, select
from sqlalchemy.dialects.postgresql import insert as pg_insert

from domains.gitops.models import WorkflowRun
from domains.release_flow.execution import execution_profile
from domains.release_flow.models import (
    ReleasePlan,
    ReleasePlanStep,
    ReleaseRun,
    ReleaseRunEvent,
    ReleaseRunStep,
)
from domains.release_flow.redaction import redact_release_value
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.identity import DEFAULT_WORKSPACE_ID
from packages.storage.engine import DatabaseConnection, iso_or_none
from packages.storage.schema import EventModel

DEFAULT_RELEASE_PLAN_STATUS = "draft"
DEFAULT_RELEASE_RUN_STATUS = "running"
PENDING_STEP_STATUS = "pending"
DISPATCHED_STEP_STATUS = "dispatched"
RUNNING_STEP_STATUS = "running"
WAITING_APPROVAL_STEP_STATUS = "waiting_for_approval"
SUCCEEDED_STEP_STATUS = "succeeded"
FAILED_STEP_STATUS = "failed"
TERMINAL_RUN_STATUSES = {"succeeded", "failed", "cancelled", "rollback_requested"}

# 릴리스 run 상태 전이 가드 — target 상태별 허용 source 집합.
# 상태 갱신은 현재 상태를 WHERE 에 넣은 CAS 로 수행해, 경합 시 "마지막 쓰기 승리"가
# 아니라 "먼저 종결한 쪽 승리"가 되도록 한다(예: cancel 직후 도착한 advance 가
# cancelled 를 running 으로 부활시키는 회귀 차단). 같은 상태 재기록은 멱등 허용.
RELEASE_RUN_ALLOWED_SOURCES: dict[str, frozenset[str]] = {
    "running": frozenset({"pending", "paused", "waiting_for_approval", "failed"}),
    "paused": frozenset({"running", "waiting_for_approval"}),
    "waiting_for_approval": frozenset({"running", "paused"}),
    "succeeded": frozenset({"running", "waiting_for_approval"}),
    "failed": frozenset({"running", "paused", "waiting_for_approval"}),
    "cancelled": frozenset({"pending", "running", "paused", "waiting_for_approval"}),
    "rollback_requested": frozenset({"running", "paused", "waiting_for_approval"}),
}


def release_run_transition_sources(status: str) -> frozenset[str]:
    """target 상태로 전이 가능한 source 상태 집합(같은 상태 재기록 포함)."""
    return RELEASE_RUN_ALLOWED_SOURCES.get(status, frozenset()) | {status}


class ReleasePlanWorkspaceMismatchError(LookupError):
    """A plan id already belongs to a different workspace."""


class ReleaseFlowRepository(DatabaseConnection):
    def list_release_plans(self, workspace_id: str, *, limit: int = 100) -> list[JsonObject]:
        table = ReleasePlan.__table__
        step_table = ReleasePlanStep.__table__
        statement = (
            select(table)
            .where(table.c.workspace_id == workspace_id)
            .order_by(table.c.updated_at.desc(), table.c.name.asc())
            .limit(max(1, min(limit, 500)))
        )
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
            plan_ids = [str(row["plan_id"]) for row in rows]
            steps_by_plan: dict[str, list[JsonObject]] = defaultdict(list)
            if plan_ids:
                step_rows = (
                    conn.execute(
                        select(step_table)
                        .where(
                            step_table.c.workspace_id == workspace_id,
                            step_table.c.plan_id.in_(plan_ids),
                        )
                        .order_by(step_table.c.plan_id.asc(), step_table.c.position.asc())
                    )
                    .mappings()
                    .all()
                )
                for step_row in step_rows:
                    steps_by_plan[str(step_row["plan_id"])].append(serialize_release_step(step_row))
        return [
            serialize_release_plan(row, steps=steps_by_plan[str(row["plan_id"])]) for row in rows
        ]

    def get_release_plan(
        self,
        workspace_id: str,
        plan_id: str,
        *,
        for_update: bool = False,
    ) -> JsonObject | None:
        plan_table = ReleasePlan.__table__
        step_table = ReleasePlanStep.__table__
        plan_statement = (
            select(plan_table)
            .where(plan_table.c.workspace_id == workspace_id, plan_table.c.plan_id == plan_id)
            .limit(1)
        )
        if for_update:
            plan_statement = plan_statement.with_for_update()
        step_statement = (
            select(step_table)
            .where(step_table.c.workspace_id == workspace_id, step_table.c.plan_id == plan_id)
            .order_by(step_table.c.position.asc())
        )
        with self.connection() as conn:
            plan = conn.execute(plan_statement).mappings().first()
            if plan is None:
                return None
            steps = conn.execute(step_statement).mappings().all()
        return serialize_release_plan(plan, steps=[serialize_release_step(row) for row in steps])

    def get_release_plan_by_name(
        self,
        workspace_id: str,
        name: str,
        *,
        for_update: bool = False,
    ) -> JsonObject | None:
        if not workspace_id or not name:
            return None
        table = ReleasePlan.__table__
        statement = (
            select(table.c.plan_id)
            .where(
                table.c.workspace_id == workspace_id,
                table.c.name == name,
            )
            .limit(1)
        )
        if for_update:
            statement = statement.with_for_update()
        with self.connection() as conn:
            plan_id = conn.execute(statement).scalar_one_or_none()
        if plan_id is None:
            return None
        return self.get_release_plan(workspace_id, str(plan_id), for_update=for_update)

    def lock_release_plan_identity(self, workspace_id: str, name: str) -> None:
        """Serialize create/name mutation decisions for one workspace+name."""
        lock_key = release_plan_identity_lock_key(workspace_id, name)
        with self.connection() as conn:
            conn.execute(select(func.pg_advisory_xact_lock(lock_key)))

    def upsert_release_plan(self, payload: JsonObject) -> JsonObject:
        workspace_id = str(payload.get("workspace_id", DEFAULT_WORKSPACE_ID))
        plan_id = derive_release_plan_id({**payload, "workspace_id": workspace_id})
        plan_table = ReleasePlan.__table__
        insert = pg_insert(plan_table).values(
            plan_id=plan_id,
            workspace_id=workspace_id,
            name=str(payload["name"]),
            description=str(payload.get("description", "")),
            status=str(payload.get("status", DEFAULT_RELEASE_PLAN_STATUS)),
            settings=dict(payload.get("settings", {})),
            updated_at=func.now(),
        )
        statement = insert.on_conflict_do_update(
            index_elements=[plan_table.c.plan_id],
            set_={
                "name": insert.excluded.name,
                "description": insert.excluded.description,
                "status": insert.excluded.status,
                "settings": insert.excluded.settings,
                "updated_at": func.now(),
            },
            where=plan_table.c.workspace_id == insert.excluded.workspace_id,
        ).returning(plan_table.c.plan_id)
        with self.connection() as conn:
            persisted_plan_id = conn.execute(statement).scalar_one_or_none()
            if persisted_plan_id is None:
                raise ReleasePlanWorkspaceMismatchError("release plan not found")
            step_table = ReleasePlanStep.__table__
            conn.execute(
                delete(step_table).where(
                    step_table.c.workspace_id == workspace_id,
                    step_table.c.plan_id == plan_id,
                )
            )
            step_values = [
                release_step_values(workspace_id, plan_id, raw_step, index)
                for index, raw_step in enumerate(payload.get("steps", []))
                if isinstance(raw_step, Mapping)
            ]
            if step_values:
                conn.execute(pg_insert(step_table).values(step_values))
        return self.get_release_plan(workspace_id, plan_id) or {
            **payload,
            "workspace_id": workspace_id,
            "plan_id": plan_id,
        }

    def list_release_runs(
        self,
        workspace_id: str,
        *,
        plan_id: str | None = None,
        limit: int = 50,
    ) -> list[JsonObject]:
        table = ReleaseRun.__table__
        statement = (
            select(table)
            .where(table.c.workspace_id == workspace_id)
            .order_by(table.c.created_at.desc())
            .limit(max(1, min(limit, 200)))
        )
        if plan_id:
            statement = statement.where(table.c.plan_id == plan_id)
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [
            self.get_release_run(workspace_id, str(row["run_id"])) or serialize_release_run(row)
            for row in rows
        ]

    def summarize_release_runs(
        self,
        workspace_id: str,
        *,
        plan_id: str | None = None,
        recent: int = 10,
    ) -> dict[str, Any]:
        table = ReleaseRun.__table__
        statement = (
            select(
                table.c.run_id,
                table.c.plan_id,
                table.c.status,
            )
            .where(table.c.workspace_id == workspace_id)
            .order_by(table.c.created_at.desc())
        )
        if plan_id:
            statement = statement.where(table.c.plan_id == plan_id)
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        status_breakdown: dict[str, int] = defaultdict(int)
        plan_breakdown: dict[str, int] = defaultdict(int)
        for row in rows:
            status_breakdown[str(row["status"])] += 1
            plan_breakdown[str(row["plan_id"])] += 1
        recent_runs = rows[:recent]
        summary_runs = [
            {
                "run_id": str(item["run_id"]),
                "plan_id": str(item["plan_id"]),
                "status": str(item["status"]),
            }
            for item in recent_runs
        ]
        return {
            "total_runs": len(rows),
            "status_breakdown": dict(status_breakdown),
            "plan_breakdown": dict(plan_breakdown),
            "recent_runs": summary_runs,
        }

    def find_release_safe_pr_evidence(
        self,
        workspace_id: str,
        workflow_run_id: str,
        *,
        application_id: str | None = None,
    ) -> JsonObject | None:
        candidates = self.list_release_safe_pr_evidence(
            workspace_id,
            workflow_run_id,
            application_id=application_id,
            limit=1,
        )
        return candidates[0] if candidates else None

    def list_release_safe_pr_evidence(
        self,
        workspace_id: str,
        workflow_run_id: str,
        *,
        application_id: str | None = None,
        limit: int = 20,
    ) -> list[JsonObject]:
        workflow_run_id = workflow_run_id.strip()
        if not workflow_run_id:
            return []
        table = EventModel.__table__
        statement = (
            select(table.c.event_id, table.c.correlation_id, table.c.payload, table.c.created_at)
            .where(
                table.c.subject == "safe_pr.created",
                table.c.payload["workspace_id"].astext == workspace_id,
                table.c.payload["workflow_run_id"].astext == workflow_run_id,
            )
            .order_by(table.c.created_at.desc())
            .limit(max(1, min(limit, 50)))
        )
        if application_id:
            statement = statement.where(table.c.payload["application_id"].astext == application_id)
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [self._release_safe_pr_evidence_from_event(row) for row in rows]

    def list_release_safe_pr_diff_events(
        self,
        workspace_id: str,
        workflow_run_id: str,
        *,
        application_id: str | None = None,
        limit: int = 20,
    ) -> list[JsonObject]:
        workflow_run_id = workflow_run_id.strip()
        if not workflow_run_id:
            return []
        table = EventModel.__table__
        subjects = (
            "safe_pr.patch_prepared",
            "diff.explained",
            "safe_pr.ready_for_creation",
            "safe_pr.failed",
            "safe_pr.created",
        )
        statement = (
            select(
                table.c.event_id,
                table.c.correlation_id,
                table.c.subject,
                table.c.payload,
                table.c.created_at,
            )
            .where(
                table.c.subject.in_(subjects),
                table.c.payload["workspace_id"].astext == workspace_id,
                table.c.payload["workflow_run_id"].astext == workflow_run_id,
            )
            .order_by(table.c.created_at.desc())
            .limit(max(1, min(limit, 50)))
        )
        if application_id:
            statement = statement.where(table.c.payload["application_id"].astext == application_id)
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [
            {
                "event_id": str(row["event_id"]),
                "correlation_id": str(row["correlation_id"]),
                "subject": str(row["subject"]),
                "payload": dict(row.get("payload") or {}),
                "created_at": iso_or_none(row.get("created_at")),
            }
            for row in rows
        ]

    def _release_safe_pr_evidence_from_event(self, row: Mapping[str, Any]) -> JsonObject:
        payload = dict(row.get("payload") or {})
        return {
            "event_id": str(row["event_id"]),
            "correlation_id": str(row["correlation_id"]),
            "workflow_run_id": str(payload.get("workflow_run_id") or ""),
            "pr_url": str(payload.get("pr_url") or ""),
            "provider": str(payload.get("provider") or ""),
            "repo_ref": str(payload.get("repo_ref") or ""),
            "base_branch": str(payload.get("base_branch") or ""),
            "environment": str(payload.get("environment") or ""),
            "manifest_path": str(payload.get("manifest_path") or ""),
            "commit_sha": str(payload.get("commit_sha") or ""),
            "patch_sha256": str(payload.get("patch_sha256") or ""),
            "created_at": iso_or_none(row.get("created_at")),
        }

    def list_release_audit_events(
        self,
        workspace_id: str,
        *,
        plan_id: str | None = None,
        run_id: str | None = None,
        event_type: str | None = None,
        limit: int = 200,
    ) -> list[JsonObject]:
        event_table = ReleaseRunEvent.__table__
        run_table = ReleaseRun.__table__
        step_table = ReleaseRunStep.__table__
        statement = (
            select(
                event_table.c.audit_id,
                event_table.c.workspace_id,
                event_table.c.run_id,
                event_table.c.event_type,
                event_table.c.message,
                event_table.c.actor,
                event_table.c.details,
                event_table.c.created_at,
                run_table.c.plan_id,
                run_table.c.plan_name,
                run_table.c.status.label("run_status"),
            )
            .join(
                run_table,
                and_(
                    event_table.c.workspace_id == run_table.c.workspace_id,
                    event_table.c.run_id == run_table.c.run_id,
                ),
            )
            .where(event_table.c.workspace_id == workspace_id)
            .order_by(event_table.c.created_at.desc())
            .limit(max(1, min(limit, 1000)))
        )
        if plan_id:
            statement = statement.where(run_table.c.plan_id == plan_id)
        if run_id:
            statement = statement.where(event_table.c.run_id == run_id)
        if event_type:
            exact, prefix = release_audit_event_type_filter(event_type)
            if prefix:
                statement = statement.where(event_table.c.event_type.like(f"{prefix}%"))
            elif exact:
                statement = statement.where(event_table.c.event_type == exact)
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
            run_ids = sorted({str(row["run_id"]) for row in rows})
            steps_by_run: dict[str, list[JsonObject]] = defaultdict(list)
            if run_ids:
                step_rows = (
                    conn.execute(
                        select(step_table)
                        .where(
                            step_table.c.workspace_id == workspace_id,
                            step_table.c.run_id.in_(run_ids),
                        )
                        .order_by(step_table.c.run_id.asc(), step_table.c.wave.asc())
                    )
                    .mappings()
                    .all()
                )
                for step_row in step_rows:
                    steps_by_run[str(step_row["run_id"])].append(
                        serialize_release_run_step(step_row)
                    )
        return [
            serialize_release_audit_event(row, steps=steps_by_run[str(row["run_id"])])
            for row in rows
        ]

    def get_release_run(self, workspace_id: str, run_id: str) -> JsonObject | None:
        run_table = ReleaseRun.__table__
        step_table = ReleaseRunStep.__table__
        event_table = ReleaseRunEvent.__table__
        run_statement = (
            select(run_table)
            .where(run_table.c.workspace_id == workspace_id, run_table.c.run_id == run_id)
            .limit(1)
        )
        step_statement = (
            select(step_table)
            .where(step_table.c.workspace_id == workspace_id, step_table.c.run_id == run_id)
            .order_by(step_table.c.wave.asc(), step_table.c.created_at.asc())
        )
        event_statement = (
            select(event_table)
            .where(event_table.c.workspace_id == workspace_id, event_table.c.run_id == run_id)
            .order_by(event_table.c.created_at.asc())
        )
        with self.connection() as conn:
            run = conn.execute(run_statement).mappings().first()
            if run is None:
                return None
            step_rows = conn.execute(step_statement).mappings().all()
            workflow_ids = [
                str(row["workflow_run_id"]) for row in step_rows if row.get("workflow_run_id")
            ]
            workflows: dict[str, Mapping[str, Any]] = {}
            if workflow_ids:
                workflow_rows = (
                    conn.execute(
                        select(WorkflowRun.__table__).where(
                            WorkflowRun.__table__.c.workflow_run_id.in_(workflow_ids)
                        )
                    )
                    .mappings()
                    .all()
                )
                workflows = {str(row["workflow_run_id"]): row for row in workflow_rows}
            events = conn.execute(event_statement).mappings().all()
        steps = [
            serialize_release_run_step(row, workflow=workflows.get(str(row.get("workflow_run_id"))))
            for row in step_rows
        ]
        return serialize_release_run(
            run,
            steps=steps,
            events=[serialize_release_run_event(row) for row in events],
        )

    def create_release_run(self, payload: JsonObject) -> JsonObject:
        workspace_id = str(payload.get("workspace_id", DEFAULT_WORKSPACE_ID))
        run_id = derive_release_run_id(payload)
        plan = mapping_value(payload.get("plan"))
        preview = mapping_value(payload.get("preview"))
        settings = dict(mapping_value(plan.get("settings")))
        profile = execution_profile(plan)
        settings.setdefault("runtime_mode", profile.runtime_mode)
        settings.setdefault("provider_mode", profile.provider_mode)
        plan_id = str(
            plan.get("plan_id") or derive_release_plan_id({**plan, "workspace_id": workspace_id})
        )
        table = ReleaseRun.__table__
        insert = pg_insert(table).values(
            run_id=run_id,
            workspace_id=workspace_id,
            plan_id=plan_id,
            plan_name=str(plan.get("name") or "Release plan"),
            status=str(payload.get("status", DEFAULT_RELEASE_RUN_STATUS)),
            current_wave=int(payload.get("current_wave", 1)),
            total_waves=len(list_value(preview.get("waves"))),
            started_by=payload.get("started_by"),
            settings=settings,
            github=github_release_metadata(plan),
            rollback=rollback_metadata(plan),
            health=release_health_summary([], preview),
            updated_at=func.now(),
        )
        statement = insert.on_conflict_do_update(
            index_elements=[table.c.run_id],
            set_={
                "status": insert.excluded.status,
                "current_wave": insert.excluded.current_wave,
                "total_waves": insert.excluded.total_waves,
                "settings": insert.excluded.settings,
                "github": insert.excluded.github,
                "rollback": insert.excluded.rollback,
                "health": insert.excluded.health,
                "updated_at": func.now(),
            },
        )
        with self.connection() as conn:
            conn.execute(statement)
            run_step_values = release_run_steps_from_plan(workspace_id, run_id, plan, preview)
            if run_step_values:
                step_table = ReleaseRunStep.__table__
                step_insert = pg_insert(step_table).values(run_step_values)
                conn.execute(
                    step_insert.on_conflict_do_update(
                        index_elements=[step_table.c.run_step_id],
                        set_={
                            "step_id": step_insert.excluded.step_id,
                            "name": step_insert.excluded.name,
                            "wave": step_insert.excluded.wave,
                            "rollback": step_insert.excluded.rollback,
                            "details": step_insert.excluded.details,
                        },
                        where=and_(
                            step_table.c.workspace_id == step_insert.excluded.workspace_id,
                            step_table.c.run_id == step_insert.excluded.run_id,
                            step_table.c.application_id == step_insert.excluded.application_id,
                        ),
                    )
                )
            conn.execute(
                pg_insert(ReleaseRunEvent.__table__).values(
                    **release_run_event_values(
                        workspace_id,
                        run_id,
                        "release.started",
                        "Release run created.",
                        payload.get("started_by"),
                        {
                            "plan_id": plan_id,
                            "plan_name": plan.get("name"),
                            "total_waves": len(list_value(preview.get("waves"))),
                            "approval_policy": settings.get("approval_policy"),
                            "rollback_policy": settings.get("rollback_policy"),
                            "execution_profile": profile.to_body(),
                        },
                    )
                )
            )
        return self.get_release_run(workspace_id, run_id) or {
            "run_id": run_id,
            "workspace_id": workspace_id,
            "plan_id": plan_id,
        }

    def archive_release_plan(
        self,
        workspace_id: str,
        plan_id: str,
        *,
        reason: str,
        actor: str,
    ) -> JsonObject | None:
        plan = self.get_release_plan(workspace_id, plan_id)
        if plan is None:
            return None
        table = ReleasePlan.__table__
        archive = {
            "reason": reason,
            "archived_by": actor,
            "archived_at": datetime.now(UTC).isoformat(),
            "previous_status": str(plan.get("status") or DEFAULT_RELEASE_PLAN_STATUS),
        }
        values = {"status": "archived", "settings": table.c.settings.op("||")({"archive": archive})}
        statement = (
            table.update()
            .where(table.c.workspace_id == workspace_id, table.c.plan_id == plan_id)
            .values(**values, updated_at=func.now())
        )
        with self.connection() as conn:
            conn.execute(statement)
        return self.get_release_plan(workspace_id, plan_id)

    def restore_release_plan(
        self,
        workspace_id: str,
        plan_id: str,
        *,
        reason: str,
        actor: str,
    ) -> JsonObject | None:
        plan = self.get_release_plan(workspace_id, plan_id)
        if plan is None:
            return None
        settings = dict(plan.get("settings") or {})
        archive = dict(settings.get("archive") or {})
        previous_status = str(archive.get("previous_status") or DEFAULT_RELEASE_PLAN_STATUS)
        if previous_status not in {"draft", "active", "paused"}:
            previous_status = DEFAULT_RELEASE_PLAN_STATUS
        archive.update(
            {
                "restore_reason": reason,
                "restored_by": actor,
                "restored_at": datetime.now(UTC).isoformat(),
            }
        )
        table = ReleasePlan.__table__
        statement = (
            table.update()
            .where(table.c.workspace_id == workspace_id, table.c.plan_id == plan_id)
            .values(
                status=previous_status,
                settings=table.c.settings.op("||")({"archive": archive}),
                updated_at=func.now(),
            )
        )
        with self.connection() as conn:
            conn.execute(statement)
        return self.get_release_plan(workspace_id, plan_id)

    def has_active_release_runs(self, workspace_id: str, plan_id: str) -> bool:
        table = ReleaseRun.__table__
        statement = (
            select(func.count())
            .select_from(table)
            .where(
                table.c.workspace_id == workspace_id,
                table.c.plan_id == plan_id,
                table.c.status.not_in(TERMINAL_RUN_STATUSES),
            )
        )
        with self.connection() as conn:
            value = conn.execute(statement).scalar_one()
            return int(value or 0) > 0

    def delete_release_plan(self, workspace_id: str, plan_id: str) -> bool:
        plan_table = ReleasePlan.__table__
        plan_step_table = ReleasePlanStep.__table__
        run_table = ReleaseRun.__table__
        run_step_table = ReleaseRunStep.__table__
        run_event_table = ReleaseRunEvent.__table__

        with self.connection() as conn:
            plan = conn.execute(
                select(plan_table.c.plan_id)
                .where(plan_table.c.workspace_id == workspace_id, plan_table.c.plan_id == plan_id)
                .limit(1)
            ).first()
            if plan is None:
                return False

            run_ids = [
                str(row["run_id"])
                for row in conn.execute(
                    select(run_table.c.run_id).where(
                        run_table.c.workspace_id == workspace_id,
                        run_table.c.plan_id == plan_id,
                    )
                ).all()
            ]
            if run_ids:
                conn.execute(delete(run_event_table).where(run_event_table.c.run_id.in_(run_ids)))
                conn.execute(delete(run_step_table).where(run_step_table.c.run_id.in_(run_ids)))
                conn.execute(delete(run_table).where(run_table.c.run_id.in_(run_ids)))

            conn.execute(delete(plan_step_table).where(plan_step_table.c.plan_id == plan_id))
            conn.execute(delete(plan_table).where(plan_table.c.plan_id == plan_id))
        return True

    def delete_release_run(self, workspace_id: str, run_id: str) -> bool:
        run_table = ReleaseRun.__table__
        run_step_table = ReleaseRunStep.__table__
        run_event_table = ReleaseRunEvent.__table__

        with self.connection() as conn:
            run = conn.execute(
                select(run_table.c.run_id).where(
                    run_table.c.workspace_id == workspace_id,
                    run_table.c.run_id == run_id,
                )
            ).first()
            if run is None:
                return False
            conn.execute(delete(run_event_table).where(run_event_table.c.run_id == run_id))
            conn.execute(delete(run_step_table).where(run_step_table.c.run_id == run_id))
            conn.execute(delete(run_table).where(run_table.c.run_id == run_id))
        return True

    def mark_release_run_step_dispatched(
        self,
        workspace_id: str,
        run_id: str,
        application_id: str,
        *,
        workflow_run_id: str,
        event_id: str,
        correlation_id: str,
        actor: str | None = None,
        details: JsonObject | None = None,
    ) -> None:
        step_table = ReleaseRunStep.__table__
        where_clause = (
            step_table.c.workspace_id == workspace_id,
            step_table.c.run_id == run_id,
            step_table.c.application_id == application_id,
        )
        merged_details = dict(details or {})
        with self.connection() as conn:
            existing_details = conn.execute(
                select(step_table.c.details).where(*where_clause).limit(1)
            ).scalar_one_or_none()
            if isinstance(existing_details, dict):
                merged_details = {**existing_details, **merged_details}
            conn.execute(
                step_table.update()
                .where(*where_clause)
                .values(
                    status=DISPATCHED_STEP_STATUS,
                    workflow_run_id=workflow_run_id,
                    event_id=event_id,
                    correlation_id=correlation_id,
                    details=merged_details,
                    updated_at=func.now(),
                )
            )
            conn.execute(
                pg_insert(ReleaseRunEvent.__table__).values(
                    **release_run_event_values(
                        workspace_id,
                        run_id,
                        "wave.dispatched",
                        f"Application {application_id} dispatched to GitOps.",
                        actor,
                        {
                            "application_id": application_id,
                            "workflow_run_id": workflow_run_id,
                            "event_id": event_id,
                            "correlation_id": correlation_id,
                            **dict(details or {}),
                        },
                    )
                )
            )

    def project_release_workflow_event(self, payload: JsonObject) -> JsonObject | None:
        workspace_id = str(payload.get("workspace_id", DEFAULT_WORKSPACE_ID))
        workflow_run_id = str(payload.get("workflow_run_id") or "")
        if not workflow_run_id:
            return None

        step_table = ReleaseRunStep.__table__
        run_table = ReleaseRun.__table__
        step_status = str(payload.get("step_status") or "")
        health_status = str(payload.get("health_status") or "")
        approval_id = str(payload.get("approval_id") or "")
        event_type = str(payload.get("event_type") or "workflow.projected")
        message = str(payload.get("message") or event_type)
        details = dict(mapping_value(payload.get("details")))

        with self.connection() as conn:
            step_row = (
                conn.execute(
                    select(step_table)
                    .where(
                        step_table.c.workspace_id == workspace_id,
                        step_table.c.workflow_run_id == workflow_run_id,
                    )
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if step_row is None:
                return None

            run_id = str(step_row["run_id"])
            current_details = dict(step_row.get("details") or {})
            current_health = dict(step_row.get("health") or {})
            merged_details = merge_projection_details(current_details, details)
            merged_health = current_health
            if health_status:
                merged_health = {**current_health, "status": health_status}

            values: JsonObject = {
                "details": merged_details,
                "updated_at": func.now(),
            }
            if step_status:
                values["status"] = step_status
            if health_status:
                values["health"] = merged_health
            if approval_id:
                values["approval_id"] = approval_id

            conn.execute(
                step_table.update()
                .where(
                    step_table.c.workspace_id == workspace_id,
                    step_table.c.workflow_run_id == workflow_run_id,
                )
                .values(**values)
            )
            conn.execute(
                pg_insert(ReleaseRunEvent.__table__).values(
                    **release_run_event_values(
                        workspace_id,
                        run_id,
                        event_type,
                        message,
                        payload.get("actor"),
                        {
                            "workflow_run_id": workflow_run_id,
                            "application_id": str(
                                payload.get("application_id") or step_row["application_id"]
                            ),
                            **details,
                        },
                    )
                )
            )
            step_rows = (
                conn.execute(
                    select(step_table).where(
                        step_table.c.workspace_id == workspace_id,
                        step_table.c.run_id == run_id,
                    )
                )
                .mappings()
                .all()
            )
            run_row = (
                conn.execute(
                    select(run_table)
                    .where(run_table.c.workspace_id == workspace_id, run_table.c.run_id == run_id)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            if run_row is not None:
                next_status = projected_release_status(run_row, step_rows)
                conn.execute(
                    run_table.update()
                    .where(
                        run_table.c.workspace_id == workspace_id,
                        run_table.c.run_id == run_id,
                        # 종결된 run 은 지연·재배달 스텝 이벤트로 부활하지 않는다 —
                        # terminal 상태와 그 시점의 health 를 그대로 동결한다.
                        run_table.c.status.not_in(sorted(TERMINAL_RUN_STATUSES)),
                    )
                    .values(
                        status=next_status,
                        health=release_health_summary([dict(row) for row in step_rows]),
                        updated_at=func.now(),
                    )
                )
        return self.get_release_run(workspace_id, run_id)

    def update_release_run_status(
        self,
        workspace_id: str,
        run_id: str,
        status: str,
        *,
        current_wave: int | None = None,
        actor: str | None = None,
        message: str | None = None,
        details: JsonObject | None = None,
    ) -> JsonObject | None:
        table = ReleaseRun.__table__
        values: JsonObject = {"status": status, "updated_at": func.now()}
        if current_wave is not None:
            values["current_wave"] = current_wave
        statement = (
            table.update()
            .where(
                table.c.workspace_id == workspace_id,
                table.c.run_id == run_id,
                table.c.status.in_(sorted(release_run_transition_sources(status))),
            )
            .values(**values)
            .returning(table.c.run_id)
        )
        with self.connection() as conn:
            updated = conn.execute(statement).first()
            if updated is None:
                # 전이 거부(이미 terminal 이거나 허용되지 않은 source) — 상태를
                # 바꾸지 않았으므로 감사 이벤트도 남기지 않고 현재 행을 반환한다.
                return self.get_release_run(workspace_id, run_id)
            conn.execute(
                pg_insert(ReleaseRunEvent.__table__).values(
                    **release_run_event_values(
                        workspace_id,
                        run_id,
                        f"release.{status}",
                        message or f"Release run marked {status}.",
                        actor,
                        details or {},
                    )
                )
            )
        return self.get_release_run(workspace_id, run_id)

    def request_release_run_rollback(
        self,
        workspace_id: str,
        run_id: str,
        *,
        actor: str | None,
        reason: str,
    ) -> JsonObject | None:
        table = ReleaseRun.__table__
        current = self.get_release_run(workspace_id, run_id)
        if current is None:
            return None
        rollback = {
            **dict(current.get("rollback") or {}),
            "requested": True,
            "requested_by": actor,
            "reason": reason,
        }
        with self.connection() as conn:
            conn.execute(
                table.update()
                .where(table.c.workspace_id == workspace_id, table.c.run_id == run_id)
                .values(status="rollback_requested", rollback=rollback, updated_at=func.now())
            )
            conn.execute(
                pg_insert(ReleaseRunEvent.__table__).values(
                    **release_run_event_values(
                        workspace_id,
                        run_id,
                        "rollback.requested",
                        "Rollback requested for release run.",
                        actor,
                        rollback,
                    )
                )
            )
        return self.get_release_run(workspace_id, run_id)

    def record_release_run_event(
        self,
        workspace_id: str,
        run_id: str,
        event_type: str,
        message: str,
        *,
        actor: str | None = None,
        details: JsonObject | None = None,
    ) -> JsonObject | None:
        with self.connection() as conn:
            conn.execute(
                pg_insert(ReleaseRunEvent.__table__).values(
                    **release_run_event_values(
                        workspace_id,
                        run_id,
                        event_type,
                        message,
                        actor,
                        details or {},
                    )
                )
            )
        return self.get_release_run(workspace_id, run_id)

    def mark_release_run_retry(
        self,
        workspace_id: str,
        run_id: str,
        wave: int,
        attempt: int,
        status: str,
        *,
        actor: str | None,
        reason: str,
    ) -> JsonObject | None:
        table = ReleaseRun.__table__
        event_type = f"release.retry.wave.{wave}.attempt.{attempt}.{status}"
        with self.connection() as conn:
            updated = conn.execute(
                table.update()
                .where(
                    table.c.workspace_id == workspace_id,
                    table.c.run_id == run_id,
                    table.c.status.in_(sorted(release_run_transition_sources(status))),
                )
                .values(status=status, current_wave=wave, updated_at=func.now())
                .returning(table.c.run_id)
            ).first()
            if updated is None:
                # 전이 거부 — retry 마킹이 경합에서 밀렸으므로 감사 이벤트도 남기지 않는다.
                return self.get_release_run(workspace_id, run_id)
            conn.execute(
                pg_insert(ReleaseRunEvent.__table__).values(
                    **release_run_event_values(
                        workspace_id,
                        run_id,
                        event_type,
                        f"Retry attempt {attempt} for release wave {wave} {status}.",
                        actor,
                        {
                            "wave": wave,
                            "attempt": attempt,
                            "status": status,
                            "reason": reason,
                        },
                    )
                )
            )
        return self.get_release_run(workspace_id, run_id)


def derive_release_plan_id(payload: JsonObject) -> str:
    explicit = payload.get("plan_id")
    if explicit:
        return str(explicit)
    raw = "|".join(
        [
            str(payload.get("workspace_id", DEFAULT_WORKSPACE_ID)),
            str(payload.get("name", "")),
        ]
    )
    return f"release-plan-{hashlib.sha256(raw.encode()).hexdigest()[:24]}"


def release_plan_identity_lock_key(workspace_id: str, name: str) -> int:
    raw = f"release-plan\0{workspace_id}\0{name}".encode()
    return int.from_bytes(hashlib.sha256(raw).digest()[:8], byteorder="big", signed=True)


def derive_release_step_id(plan_id: str, payload: Mapping[str, Any], position: int) -> str:
    explicit = payload.get("step_id")
    if explicit:
        return str(explicit)
    raw = "|".join([plan_id, str(payload.get("application_id", "")), str(position)])
    return f"release-step-{hashlib.sha256(raw.encode()).hexdigest()[:24]}"


def derive_release_run_id(payload: JsonObject) -> str:
    explicit = payload.get("run_id")
    if explicit:
        return str(explicit)
    return f"release-run-{uuid.uuid4().hex[:24]}"


def derive_release_run_step_id(run_id: str, application_id: str) -> str:
    raw = f"{run_id}|{application_id}"
    return f"release-run-step-{hashlib.sha256(raw.encode()).hexdigest()[:24]}"


def derive_release_run_event_id(run_id: str, event_type: str) -> str:
    raw = f"{run_id}|{event_type}|{uuid.uuid4().hex}"
    return f"release-audit-{hashlib.sha256(raw.encode()).hexdigest()[:24]}"


def release_step_values(
    workspace_id: str,
    plan_id: str,
    payload: Mapping[str, Any],
    fallback_position: int,
) -> JsonObject:
    position = int(payload.get("position", fallback_position))
    application_id = str(payload["application_id"])
    name = str(payload.get("name") or application_id)
    depends_on = payload.get("depends_on", [])
    if not isinstance(depends_on, list):
        depends_on = []
    config = payload.get("config", {})
    if not isinstance(config, Mapping):
        config = {}
    return {
        "step_id": derive_release_step_id(plan_id, payload, position),
        "workspace_id": workspace_id,
        "plan_id": plan_id,
        "application_id": application_id,
        "name": name,
        "position": position,
        "depends_on": [str(item) for item in depends_on],
        "config": dict(config),
        "updated_at": func.now(),
    }


def release_run_steps_from_plan(
    workspace_id: str,
    run_id: str,
    plan: Mapping[str, Any],
    preview: Mapping[str, Any],
) -> list[JsonObject]:
    preview_by_app = {
        str(item.get("application_id")): item
        for item in list_value(preview.get("steps"))
        if isinstance(item, Mapping)
    }
    profile = execution_profile(plan)
    values: list[JsonObject] = []
    for index, raw_step in enumerate(list_value(plan.get("steps"))):
        if not isinstance(raw_step, Mapping):
            continue
        application_id = str(raw_step.get("application_id") or "")
        if not application_id:
            continue
        preview_step = preview_by_app.get(application_id, {})
        config = mapping_value(raw_step.get("config"))
        wave = int_like(preview_step.get("wave"), 1)
        values.append(
            {
                "run_step_id": derive_release_run_step_id(run_id, application_id),
                "workspace_id": workspace_id,
                "run_id": run_id,
                "step_id": str(
                    raw_step.get("step_id") or preview_step.get("step_id") or f"step-{index}"
                ),
                "application_id": application_id,
                "name": str(raw_step.get("name") or preview_step.get("name") or application_id),
                "wave": wave,
                "status": PENDING_STEP_STATUS,
                "workflow_run_id": None,
                "approval_id": None,
                "event_id": None,
                "correlation_id": None,
                "health": {
                    "status": "pending",
                    "path": str(config.get("health_check_path") or "/readyz"),
                    "timeout_seconds": int_like(config.get("timeout_seconds"), 600),
                },
                "rollback": {
                    "policy": str(
                        mapping_value(plan.get("settings")).get("rollback_policy") or "manual"
                    ),
                    "safe_pr_ready": str(
                        mapping_value(plan.get("settings")).get("rollback_policy") or ""
                    )
                    == "safe_pr",
                },
                "details": {
                    "runtime_mode": profile.runtime_mode,
                    "provider_mode": profile.provider_mode,
                    "side_effects": profile.side_effects,
                    "gate": str(
                        preview_step.get("gate") or config.get("approval_gate") or "inherit"
                    ),
                    "strategy": str(
                        preview_step.get("strategy") or config.get("strategy") or "rolling"
                    ),
                    "environment": str(
                        preview_step.get("environment") or config.get("environment") or ""
                    ),
                    "config": dict(config),
                    "github": github_step_metadata(config),
                },
                "updated_at": func.now(),
            }
        )
    return values


def release_run_event_values(
    workspace_id: str,
    run_id: str,
    event_type: str,
    message: str,
    actor: Any,
    details: JsonObject,
) -> JsonObject:
    return {
        "audit_id": derive_release_run_event_id(run_id, event_type),
        "workspace_id": workspace_id,
        "run_id": run_id,
        "event_type": event_type,
        "message": message,
        "actor": str(actor) if actor is not None else None,
        "details": dict(details),
    }


def release_audit_event_type_filter(value: str | None) -> tuple[str | None, str | None]:
    normalized = str(value or "").strip()
    if not normalized:
        return None, None
    if normalized.endswith("*"):
        return None, normalized[:-1]
    return normalized, None


def github_release_metadata(plan: Mapping[str, Any]) -> JsonObject:
    settings = mapping_value(plan.get("settings"))
    steps = [step for step in list_value(plan.get("steps")) if isinstance(step, Mapping)]
    tag = str(settings.get("release_tag") or settings.get("git_tag") or "").strip()
    first_repo = ""
    commits: list[JsonObject] = []
    for step in steps:
        config = mapping_value(step.get("config"))
        item = github_step_metadata(config)
        if item:
            commits.append({**item, "application_id": str(step.get("application_id") or "")})
            first_repo = first_repo or str(config.get("repo_ref") or "")
    release_url = str(settings.get("release_url") or "")
    if not release_url and tag and github_repo_ref_ok(first_repo):
        release_url = f"https://github.com/{first_repo}/releases/tag/{tag}"
    return {
        "tag": tag,
        "release_url": release_url,
        "notes_url": str(settings.get("release_notes_url") or ""),
        "commits": commits,
    }


def github_step_metadata(config: Mapping[str, Any]) -> JsonObject:
    repo_ref = str(config.get("repo_ref") or "")
    commit_sha = str(config.get("commit_sha") or "")
    branch = str(config.get("branch") or "")
    item: JsonObject = {"repo_ref": repo_ref, "branch": branch, "commit_sha": commit_sha}
    if github_repo_ref_ok(repo_ref) and commit_sha:
        item["commit_url"] = f"https://github.com/{repo_ref}/commit/{commit_sha}"
    return item


def github_repo_ref_ok(repo_ref: str) -> bool:
    return repo_ref.count("/") == 1 and all(part.strip() for part in repo_ref.split("/"))


def rollback_metadata(plan: Mapping[str, Any]) -> JsonObject:
    settings = mapping_value(plan.get("settings"))
    policy = str(settings.get("rollback_policy") or "manual")
    return {
        "policy": policy,
        "requested": False,
        "safe_pr_enabled": policy == "safe_pr",
        "restart_last_successful_enabled": policy == "restart_last_successful",
    }


def release_health_summary(
    steps: list[JsonObject], _preview: Mapping[str, Any] | None = None
) -> JsonObject:
    if not steps:
        return {"status": "pending", "healthy": 0, "unhealthy": 0, "pending": 0}
    healthy = sum(
        1 for step in steps if mapping_value(step.get("health")).get("status") == "healthy"
    )
    unhealthy = sum(
        1 for step in steps if mapping_value(step.get("health")).get("status") == "unhealthy"
    )
    pending = len(steps) - healthy - unhealthy
    status = "unhealthy" if unhealthy else "healthy" if pending == 0 else "progressing"
    return {"status": status, "healthy": healthy, "unhealthy": unhealthy, "pending": pending}


def serialize_release_plan(row: Mapping[str, Any], *, steps: list[JsonObject]) -> JsonObject:
    item = dict(row)
    item["settings"] = dict(item.get("settings") or {})
    item["steps"] = steps
    item["created_at"] = iso_or_none(item.get("created_at"))
    item["updated_at"] = iso_or_none(item.get("updated_at"))
    return item


def serialize_release_step(row: Mapping[str, Any]) -> JsonObject:
    item = dict(row)
    depends_on = item.get("depends_on", [])
    item["depends_on"] = list(depends_on) if isinstance(depends_on, list) else []
    item["config"] = dict(item.get("config") or {})
    item["created_at"] = iso_or_none(item.get("created_at"))
    item["updated_at"] = iso_or_none(item.get("updated_at"))
    return item


def serialize_release_run(
    row: Mapping[str, Any],
    *,
    steps: list[JsonObject] | None = None,
    events: list[JsonObject] | None = None,
) -> JsonObject:
    item = dict(row)
    item["settings"] = dict(item.get("settings") or {})
    item["github"] = dict(item.get("github") or {})
    item["rollback"] = dict(item.get("rollback") or {})
    item["health"] = dict(item.get("health") or {})
    item["steps"] = steps or []
    item["events"] = events or []
    if steps is not None:
        item["health"] = release_health_summary(steps)
        item["derived_status"] = derived_release_status(item, steps)
        item["attention"] = release_attention_summary(item, steps)
    item["created_at"] = iso_or_none(item.get("created_at"))
    item["updated_at"] = iso_or_none(item.get("updated_at"))
    return item


def serialize_release_run_step(
    row: Mapping[str, Any],
    *,
    workflow: Mapping[str, Any] | None = None,
) -> JsonObject:
    item = dict(row)
    item["health"] = dict(item.get("health") or {})
    item["rollback"] = dict(item.get("rollback") or {})
    item["details"] = dict(item.get("details") or {})
    if workflow:
        workflow_status = str(workflow.get("status") or "")
        item["workflow"] = {
            "workflow_run_id": str(workflow.get("workflow_run_id") or ""),
            "status": workflow_status,
            "current_step": str(workflow.get("current_step") or ""),
            "summary": workflow.get("summary"),
            "updated_at": iso_or_none(workflow.get("updated_at")),
        }
        status_upper = workflow_status.upper()
        if status_upper in {"SUCCEEDED", "COMPLETED"}:
            item["status"] = "succeeded"
            item["health"] = {**item["health"], "status": "healthy"}
        elif status_upper in {"FAILED", "REJECTED"}:
            item["status"] = "failed"
            item["health"] = {**item["health"], "status": "unhealthy"}
        elif workflow_status:
            item["status"] = "running"
            item["health"] = {**item["health"], "status": "progressing"}
    item["created_at"] = iso_or_none(item.get("created_at"))
    item["updated_at"] = iso_or_none(item.get("updated_at"))
    return item


def serialize_release_run_event(row: Mapping[str, Any]) -> JsonObject:
    item = dict(row)
    item["details"] = redact_release_value(dict(item.get("details") or {}))
    item["created_at"] = iso_or_none(item.get("created_at"))
    return item


def serialize_release_audit_event(
    row: Mapping[str, Any],
    *,
    steps: list[JsonObject],
) -> JsonObject:
    item = serialize_release_run_event(row)
    item["plan_id"] = str(row.get("plan_id") or "")
    item["plan_name"] = str(row.get("plan_name") or "")
    item["run_status"] = str(row.get("run_status") or "")
    item["application_ids"] = sorted(
        {str(step.get("application_id") or "") for step in steps if step.get("application_id")}
    )
    item["_steps"] = steps
    return item


def derived_release_status(run: Mapping[str, Any], steps: list[JsonObject]) -> str:
    stored = str(run.get("status") or "")
    if stored in TERMINAL_RUN_STATUSES or stored == "paused":
        return stored
    statuses = {str(step.get("status") or "") for step in steps}
    if FAILED_STEP_STATUS in statuses:
        return FAILED_STEP_STATUS
    if steps and statuses <= {SUCCEEDED_STEP_STATUS}:
        return SUCCEEDED_STEP_STATUS
    if WAITING_APPROVAL_STEP_STATUS in statuses:
        return WAITING_APPROVAL_STEP_STATUS
    if RUNNING_STEP_STATUS in statuses or DISPATCHED_STEP_STATUS in statuses:
        return "running"
    return stored or "pending"


def release_attention_summary(run: Mapping[str, Any], steps: list[JsonObject]) -> JsonObject:
    status = str(run.get("derived_status") or run.get("status") or "")
    health = mapping_value(run.get("health"))
    stale = release_stale_summary(run, steps, status)
    reasons: list[str] = []
    if status == FAILED_STEP_STATUS:
        reasons.append("Release run has failed steps.")
    elif status == WAITING_APPROVAL_STEP_STATUS:
        reasons.append("Release run is waiting for approval.")
    elif status == "rollback_requested":
        reasons.append("Rollback has been requested for this release run.")
    elif status == "paused":
        reasons.append("Release run is paused by an operator.")
    if stale["stale"]:
        reasons.append(
            "No release progress recorded for "
            f"{stale['age_minutes']} minute(s); timeout is {stale['timeout_minutes']} minute(s)."
        )
    if str(health.get("status") or "") == "unhealthy":
        reasons.append("Release health is unhealthy.")
    for step in steps:
        name = str(step.get("name") or step.get("application_id") or "release step")
        step_status = str(step.get("status") or "")
        step_health = mapping_value(step.get("health"))
        if step_status == FAILED_STEP_STATUS:
            reasons.append(f"{name} failed.")
        elif step_status == WAITING_APPROVAL_STEP_STATUS:
            reasons.append(f"{name} is waiting for approval.")
        if str(step_health.get("status") or "") == "unhealthy":
            reasons.append(f"{name} health is unhealthy.")
    unique_reasons = list(dict.fromkeys(reason for reason in reasons if reason))
    return {"required": bool(unique_reasons), "reasons": unique_reasons[:6], **stale}


def release_stale_summary(
    run: Mapping[str, Any],
    steps: list[JsonObject],
    status: str,
) -> JsonObject:
    if status in TERMINAL_RUN_STATUSES or status == "paused":
        return {"stale": False, "age_minutes": 0, "timeout_minutes": 0}
    updated_at = datetime_value(run.get("updated_at") or run.get("created_at"))
    if updated_at is None:
        return {"stale": False, "age_minutes": 0, "timeout_minutes": 0}
    timeout_seconds = release_timeout_seconds(run, steps)
    age_seconds = max(0, int((datetime.now(UTC) - updated_at).total_seconds()))
    timeout_minutes = max(1, (timeout_seconds + 59) // 60)
    age_minutes = (age_seconds + 59) // 60
    return {
        "stale": age_seconds > timeout_seconds,
        "age_minutes": age_minutes,
        "timeout_minutes": timeout_minutes,
    }


def release_timeout_seconds(run: Mapping[str, Any], steps: list[JsonObject]) -> int:
    settings = mapping_value(run.get("settings"))
    candidates = [int_like(settings.get("health_timeout_seconds"), 600)]
    for step in steps:
        health = mapping_value(step.get("health"))
        details = mapping_value(step.get("details"))
        config = mapping_value(details.get("config"))
        candidates.append(int_like(health.get("timeout_seconds"), 0))
        candidates.append(int_like(config.get("timeout_seconds"), 0))
    return max(30, max(candidates))


def datetime_value(value: Any) -> datetime | None:
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=UTC)
    if isinstance(value, str) and value.strip():
        raw = value.strip()
        if raw.endswith("Z"):
            raw = f"{raw[:-1]}+00:00"
        try:
            parsed = datetime.fromisoformat(raw)
        except ValueError:
            return None
        return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)
    return None


def projected_release_status(
    run: Mapping[str, Any],
    steps: list[Mapping[str, Any]],
) -> str:
    stored = str(run.get("status") or "")
    if stored in {"paused", "cancelled", "rollback_requested"}:
        return stored
    statuses = {str(step.get("status") or "") for step in steps}
    if FAILED_STEP_STATUS in statuses:
        return FAILED_STEP_STATUS
    if steps and statuses <= {SUCCEEDED_STEP_STATUS}:
        return SUCCEEDED_STEP_STATUS
    if WAITING_APPROVAL_STEP_STATUS in statuses:
        return WAITING_APPROVAL_STEP_STATUS
    if RUNNING_STEP_STATUS in statuses or DISPATCHED_STEP_STATUS in statuses:
        return "running"
    return stored or "pending"


def merge_projection_details(
    current: Mapping[str, Any],
    incoming: Mapping[str, Any],
) -> JsonObject:
    merged = dict(current)
    for key, value in incoming.items():
        if (
            key == "release_guard"
            and isinstance(value, Mapping)
            and isinstance(merged.get(key), Mapping)
        ):
            merged[key] = merge_release_guard_projection(
                mapping_value(merged.get(key)),
                value,
            )
        elif isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = {**dict(merged[key]), **dict(value)}
        else:
            merged[key] = value
    return merged


def merge_release_guard_projection(
    current: Mapping[str, Any],
    incoming: Mapping[str, Any],
) -> JsonObject:
    merged = dict(current)
    for key, value in incoming.items():
        if (
            key == "verification_jobs"
            and isinstance(value, Mapping)
            and isinstance(merged.get(key), Mapping)
        ):
            merged[key] = merge_verification_jobs_projection(
                mapping_value(merged.get(key)),
                value,
            )
        elif isinstance(value, Mapping) and isinstance(merged.get(key), Mapping):
            merged[key] = {**dict(merged[key]), **dict(value)}
        else:
            merged[key] = value
    return merged


def merge_verification_jobs_projection(
    current: Mapping[str, Any],
    incoming: Mapping[str, Any],
) -> JsonObject:
    merged = dict(current)
    updates = [dict(item) for item in list_value(incoming.get("jobs")) if isinstance(item, Mapping)]
    if not updates:
        return {**merged, **dict(incoming)}
    current_jobs = [
        dict(item) for item in list_value(current.get("jobs")) if isinstance(item, Mapping)
    ]
    next_jobs = [merge_verification_job_update(job, updates) for job in current_jobs]
    known_keys = {verification_job_match_key(job) for job in current_jobs}
    for update in updates:
        if verification_job_match_key(update) not in known_keys:
            next_jobs.append(update)
    merged.update(dict(incoming))
    merged["jobs"] = next_jobs
    merged["job_count"] = len(next_jobs)
    merged["scheduled"] = bool(next_jobs)
    return merged


def merge_verification_job_update(
    job: JsonObject,
    updates: list[JsonObject],
) -> JsonObject:
    job_key = verification_job_match_key(job)
    for update in updates:
        if verification_job_match_key(update) == job_key:
            return {**job, **update}
    return job


def verification_job_match_key(job: Mapping[str, Any]) -> str:
    return (
        str(job.get("job_id") or "")
        or str(job.get("evidence_key") or "")
        or ":".join(
            [
                str(job.get("application_id") or ""),
                str(job.get("kind") or ""),
            ]
        )
    )


def mapping_value(value: Any) -> Mapping[str, Any]:
    return value if isinstance(value, Mapping) else {}


def list_value(value: Any) -> list[Any]:
    return value if isinstance(value, list) else []


def int_like(value: Any, default: int) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default
