from __future__ import annotations

import json
import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from packages.config.logs import CONTEXT_KEY, get_logger
from packages.contracts.event_bus.interfaces import JsonObject

COMMAND_RESULT_STATUS_ABANDONED = "abandoned"
COMMAND_RESULT_STATUS_ACKED_FINALIZATION_PENDING = "acked_finalization_pending"
COMMAND_RESULT_STATUS_PENDING = "pending"
LOGGER = get_logger(__name__)


@dataclass(frozen=True)
class CommandResultRecord:
    command_id: str
    attempt_id: str
    workspace_id: str
    lease_id: str
    agent_id: str
    result: JsonObject
    attempt_count: int


class CommandResultOutbox:
    def __init__(self, db_path: str) -> None:
        Path(db_path).parent.mkdir(parents=True, exist_ok=True)
        self.conn: sqlite3.Connection | None = sqlite3.connect(db_path, timeout=5.0)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("pragma journal_mode = wal")
        self.conn.execute("pragma busy_timeout = 5000")
        self.init_schema()

    def __enter__(self) -> CommandResultOutbox:
        return self

    def __exit__(
        self,
        _exc_type: type[BaseException] | None,
        _exc: BaseException | None,
        _traceback: TracebackType | None,
    ) -> None:
        self.close()

    def __del__(self) -> None:
        self.close()

    def close(self) -> None:
        conn = getattr(self, "conn", None)
        if conn is None:
            return
        conn.close()
        self.conn = None

    def connection(self) -> sqlite3.Connection:
        if self.conn is None:
            raise RuntimeError("CommandResultOutbox is closed")
        return self.conn

    def init_schema(self) -> None:
        conn = self.connection()
        conn.execute(
            """
            create table if not exists command_results (
                command_id text not null,
                attempt_id text not null,
                workspace_id text not null,
                lease_id text not null,
                agent_id text not null,
                status text not null default 'pending',
                result_json text not null,
                attempt_count integer not null default 0,
                last_error text,
                created_at real not null,
                updated_at real not null,
                primary key (command_id, attempt_id)
            )
            """
        )
        self.ensure_columns()
        conn.execute(
            """
            create index if not exists idx_command_results_created
            on command_results(status, created_at)
            """
        )
        conn.commit()

    def ensure_columns(self) -> None:
        conn = self.connection()
        columns = {str(row["name"]) for row in conn.execute("pragma table_info(command_results)")}
        if "attempt_id" not in columns:
            conn.execute("alter table command_results rename to command_results_legacy")
            conn.execute(
                """
                create table command_results (
                    command_id text not null,
                    attempt_id text not null,
                    workspace_id text not null,
                    lease_id text not null,
                    agent_id text not null,
                    status text not null default 'pending',
                    result_json text not null,
                    attempt_count integer not null default 0,
                    last_error text,
                    created_at real not null,
                    updated_at real not null,
                    primary key (command_id, attempt_id)
                )
                """
            )
            conn.execute(
                """
                insert into command_results (
                    command_id, attempt_id, workspace_id, lease_id, agent_id,
                    status, result_json, attempt_count, last_error, created_at, updated_at
                )
                select command_id, 'legacy:' || command_id, workspace_id, lease_id, agent_id,
                       status, result_json, attempt_count, last_error, created_at, updated_at
                from command_results_legacy
                """
            )
            conn.execute("drop table command_results_legacy")
        if "status" not in columns:
            conn.execute(
                "alter table command_results add column status text not null default 'pending'"
            )
        conn.commit()

    def enqueue_result(
        self,
        *,
        command_id: str,
        workspace_id: str,
        lease_id: str,
        agent_id: str,
        result: JsonObject,
        attempt_id: str | None = None,
        now: float | None = None,
    ) -> None:
        timestamp = time.time() if now is None else now
        normalized_attempt_id = attempt_id or f"legacy:{command_id}"
        conn = self.connection()
        with conn:
            conn.execute(
                """
                insert into command_results (
                    command_id,
                    attempt_id,
                    workspace_id,
                    lease_id,
                    agent_id,
                    status,
                    result_json,
                    attempt_count,
                    last_error,
                    created_at,
                    updated_at
                )
                values (?, ?, ?, ?, ?, ?, ?, 0, null, ?, ?)
                on conflict (command_id, attempt_id) do update set
                    workspace_id = excluded.workspace_id,
                    lease_id = excluded.lease_id,
                    agent_id = excluded.agent_id,
                    status = excluded.status,
                    result_json = excluded.result_json,
                    updated_at = excluded.updated_at
                """,
                (
                    command_id,
                    normalized_attempt_id,
                    workspace_id,
                    lease_id,
                    agent_id,
                    COMMAND_RESULT_STATUS_PENDING,
                    json.dumps(result),
                    timestamp,
                    timestamp,
                ),
            )
        LOGGER.info(
            "agent_command_result_enqueued",
            extra={
                CONTEXT_KEY: {
                    **command_result_log_context(
                        command_id,
                        workspace_id,
                        lease_id,
                        agent_id,
                        normalized_attempt_id,
                    ),
                    **command_result_summary(result),
                }
            },
        )

    def next_result(self) -> CommandResultRecord | None:
        row = (
            self.connection()
            .execute(
                """
            select command_id, attempt_id, workspace_id, lease_id, agent_id, result_json, attempt_count
            from command_results
            where status = ?
            order by created_at
            limit 1
            """,
                (COMMAND_RESULT_STATUS_PENDING,),
            )
            .fetchone()
        )
        return command_result_record(row)

    def next_finalization(self) -> CommandResultRecord | None:
        """Return an ACKed uninstall result whose local self-cleanup is pending."""

        row = (
            self.connection()
            .execute(
                """
            select command_id, attempt_id, workspace_id, lease_id, agent_id, result_json, attempt_count
            from command_results
            where status = ?
            order by updated_at
            limit 1
            """,
                (COMMAND_RESULT_STATUS_ACKED_FINALIZATION_PENDING,),
            )
            .fetchone()
        )
        return command_result_record(row)

    def mark_acknowledged_for_finalization(
        self,
        command_id: str,
        attempt_id: str | None = None,
        now: float | None = None,
    ) -> None:
        """Durably record the server ACK before destructive local finalization."""

        timestamp = time.time() if now is None else now
        normalized_attempt_id = attempt_id or f"legacy:{command_id}"
        conn = self.connection()
        with conn:
            cursor = conn.execute(
                """
                update command_results
                set status = ?, last_error = null, updated_at = ?
                where command_id = ? and attempt_id = ? and status = ?
                """,
                (
                    COMMAND_RESULT_STATUS_ACKED_FINALIZATION_PENDING,
                    timestamp,
                    command_id,
                    normalized_attempt_id,
                    COMMAND_RESULT_STATUS_PENDING,
                ),
            )
            if cursor.rowcount != 1:
                raise RuntimeError("command result cannot enter finalization state")
        LOGGER.info(
            "agent_command_result_acknowledged_for_finalization",
            extra={
                CONTEXT_KEY: {
                    "command_id": command_id,
                    "attempt_id": normalized_attempt_id,
                }
            },
        )

    def mark_finalized(self, command_id: str, attempt_id: str | None = None) -> None:
        normalized_attempt_id = attempt_id or f"legacy:{command_id}"
        conn = self.connection()
        with conn:
            conn.execute(
                """
                delete from command_results
                where command_id = ? and attempt_id = ? and status = ?
                """,
                (
                    command_id,
                    normalized_attempt_id,
                    COMMAND_RESULT_STATUS_ACKED_FINALIZATION_PENDING,
                ),
            )
        LOGGER.info(
            "agent_command_result_finalized",
            extra={
                CONTEXT_KEY: {
                    "command_id": command_id,
                    "attempt_id": normalized_attempt_id,
                }
            },
        )

    def mark_sent(self, command_id: str, attempt_id: str | None = None) -> None:
        conn = self.connection()
        with conn:
            conn.execute(
                "delete from command_results where command_id = ? and attempt_id = ?",
                (command_id, attempt_id or f"legacy:{command_id}"),
            )
        LOGGER.info(
            "agent_command_result_outbox_sent",
            extra={CONTEXT_KEY: {"command_id": command_id}},
        )

    def record_failure(
        self,
        command_id: str,
        error: str,
        max_attempts: int,
        attempt_id: str | None = None,
        now: float | None = None,
    ) -> bool:
        timestamp = time.time() if now is None else now
        conn = self.connection()
        with conn:
            conn.execute(
                """
                update command_results
                set attempt_count = attempt_count + 1,
                    last_error = ?,
                    updated_at = ?
                where command_id = ? and attempt_id = ? and status = ?
                """,
                (
                    error,
                    timestamp,
                    command_id,
                    attempt_id or f"legacy:{command_id}",
                    COMMAND_RESULT_STATUS_PENDING,
                ),
            )
            row = conn.execute(
                """
                select attempt_count
                from command_results
                where command_id = ? and attempt_id = ?
                """,
                (command_id, attempt_id or f"legacy:{command_id}"),
            ).fetchone()
            attempt_count = 0 if row is None else int(row["attempt_count"])
            if attempt_count >= max(1, max_attempts):
                conn.execute(
                    """
                    update command_results
                    set status = ?, updated_at = ?
                    where command_id = ? and attempt_id = ?
                    """,
                    (
                        COMMAND_RESULT_STATUS_ABANDONED,
                        timestamp,
                        command_id,
                        attempt_id or f"legacy:{command_id}",
                    ),
                )
                LOGGER.warning(
                    "agent_command_result_outbox_abandoned",
                    extra={
                        CONTEXT_KEY: {
                            "command_id": command_id,
                            "attempt_count": attempt_count,
                            "max_attempts": max(1, max_attempts),
                        }
                    },
                )
                return True
        LOGGER.info(
            "agent_command_result_outbox_retry",
            extra={
                CONTEXT_KEY: {
                    "command_id": command_id,
                    "attempt_count": attempt_count,
                    "max_attempts": max(1, max_attempts),
                }
            },
        )
        return False

    def pending_count(self) -> int:
        row = (
            self.connection()
            .execute(
                "select count(*) as count from command_results where status = ?",
                (COMMAND_RESULT_STATUS_PENDING,),
            )
            .fetchone()
        )
        return 0 if row is None else int(row["count"])

    def abandoned_count(self) -> int:
        row = (
            self.connection()
            .execute(
                "select count(*) as count from command_results where status = ?",
                (COMMAND_RESULT_STATUS_ABANDONED,),
            )
            .fetchone()
        )
        return 0 if row is None else int(row["count"])

    def finalization_pending_count(self) -> int:
        row = (
            self.connection()
            .execute(
                "select count(*) as count from command_results where status = ?",
                (COMMAND_RESULT_STATUS_ACKED_FINALIZATION_PENDING,),
            )
            .fetchone()
        )
        return 0 if row is None else int(row["count"])


def command_result_record(row: sqlite3.Row | None) -> CommandResultRecord | None:
    if row is None:
        return None
    result = json.loads(row["result_json"])
    if not isinstance(result, dict):
        result = {"raw_result": result}
    return CommandResultRecord(
        command_id=str(row["command_id"]),
        attempt_id=str(row["attempt_id"]),
        workspace_id=str(row["workspace_id"]),
        lease_id=str(row["lease_id"]),
        agent_id=str(row["agent_id"]),
        result=result,
        attempt_count=int(row["attempt_count"]),
    )


def command_result_log_context(
    command_id: str,
    workspace_id: str,
    lease_id: str,
    agent_id: str,
    attempt_id: str | None = None,
) -> JsonObject:
    return {
        "command_id": command_id,
        "workspace_id": workspace_id,
        "lease_id": lease_id,
        "agent_id": agent_id,
        "attempt_id": attempt_id,
    }


def command_result_summary(result: JsonObject) -> JsonObject:
    return {
        "status": result.get("status"),
        "cluster_id": result.get("cluster_id"),
        "applied": result.get("applied"),
        "retryable": result.get("retryable"),
        "resource_count": len(result.get("resources") or [])
        if isinstance(result.get("resources"), list)
        else 0,
    }
