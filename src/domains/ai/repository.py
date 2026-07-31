"""AI 대화 repository."""

from __future__ import annotations

from datetime import datetime

from sqlalchemy import and_, func, select, tuple_
from sqlalchemy.dialects.postgresql import insert as pg_insert

from domains.ai.models import AiConversation, AiConversationMessage, AiLlmInvocationMetric
from packages.ai.metrics import LlmInvocationMetric
from packages.contracts.ai_conversation import (
    DEFAULT_CONVERSATION_MESSAGE_LIMIT,
    MAX_CONVERSATION_MESSAGE_LIMIT,
)
from packages.contracts.event_bus.interfaces import JsonObject
from packages.storage.engine import DatabaseConnection, row_dict

STATUS_ACTIVE = "active"
STATUS_WAITING = "waiting"
STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
ROLE_USER = "user"
ROLE_ASSISTANT = "assistant"


class AiConversationRepository(DatabaseConnection):
    conversation_table = AiConversation.__table__
    message_table = AiConversationMessage.__table__
    llm_metric_table = AiLlmInvocationMetric.__table__

    def create_ai_conversation(self, payload: JsonObject) -> JsonObject:
        values = {
            "conversation_id": payload["conversation_id"],
            "workspace_id": payload["workspace_id"],
            "user_id": payload["user_id"],
            "title": payload["title"],
            "agent": payload["agent"],
            "status": payload.get("status", STATUS_ACTIVE),
            "context": payload.get("context") or {},
            "updated_at": func.now(),
        }
        statement = (
            pg_insert(self.conversation_table)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[self.conversation_table.c.conversation_id])
            .returning(self.conversation_table)
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        return dict(row) if row is not None else payload

    def append_ai_message(self, payload: JsonObject) -> JsonObject:
        values = {
            "message_id": payload["message_id"],
            "conversation_id": payload["conversation_id"],
            "workspace_id": payload["workspace_id"],
            "role": payload["role"],
            "content": payload["content"],
            "agent": payload["agent"],
            "correlation_id": payload.get("correlation_id"),
            "metadata": payload.get("metadata") or {},
        }
        statement = (
            pg_insert(self.message_table)
            .values(**values)
            .on_conflict_do_nothing(index_elements=[self.message_table.c.message_id])
            .returning(self.message_table)
        )
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        return dict(row) if row is not None else payload

    def mark_ai_conversation_status(
        self, workspace_id: str, conversation_id: str, status: str
    ) -> bool:
        statement = (
            self.conversation_table.update()
            .where(
                self.conversation_table.c.workspace_id == workspace_id,
                self.conversation_table.c.conversation_id == conversation_id,
            )
            .values(status=status, updated_at=func.now())
        )
        with self.connection() as conn:
            result = conn.execute(statement)
        return bool(result.rowcount)

    def record_ai_response(self, payload: JsonObject) -> bool:
        with self.unit_of_work():
            if not self.mark_ai_conversation_status(
                str(payload["workspace_id"]),
                str(payload["conversation_id"]),
                STATUS_COMPLETED,
            ):
                return False
            self.append_ai_message(
                {
                    "message_id": payload["response_message_id"],
                    "conversation_id": payload["conversation_id"],
                    "workspace_id": payload["workspace_id"],
                    "role": ROLE_ASSISTANT,
                    "content": payload["content"],
                    "agent": payload["agent"],
                    "correlation_id": payload.get("correlation_id"),
                    "metadata": payload.get("metadata") or {},
                }
            )
        return True

    def record_ai_failure(self, payload: JsonObject) -> bool:
        with self.unit_of_work():
            if not self.mark_ai_conversation_status(
                str(payload["workspace_id"]),
                str(payload["conversation_id"]),
                STATUS_FAILED,
            ):
                return False
            response_message_id = payload.get("response_message_id")
            content = payload.get("content")
            if response_message_id and content:
                # The deterministic response ID and append_ai_message's conflict guard make
                # event redelivery idempotent while keeping the conversation truthfully failed.
                self.append_ai_message(
                    {
                        "message_id": response_message_id,
                        "conversation_id": payload["conversation_id"],
                        "workspace_id": payload["workspace_id"],
                        "role": ROLE_ASSISTANT,
                        "content": content,
                        "agent": payload["agent"],
                        "correlation_id": payload.get("correlation_id"),
                        "metadata": payload.get("metadata") or {},
                    }
                )
        return True

    def list_ai_conversations(
        self, workspace_id: str, *, user_id: str | None = None, limit: int = 100
    ) -> list[JsonObject]:
        table = self.conversation_table
        predicates = [table.c.workspace_id == workspace_id]
        if user_id is not None:
            predicates.append(table.c.user_id == user_id)
        statement = (
            select(table.c.conversation_id, table.c.title, table.c.status, table.c.updated_at)
            .where(*predicates)
            .order_by(table.c.updated_at.desc(), table.c.conversation_id.desc())
            .limit(max(1, min(limit, 200)))
        )
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return [row_dict(r) for r in rows]

    def get_ai_conversation(
        self, workspace_id: str, conversation_id: str, *, user_id: str | None = None
    ) -> JsonObject | None:
        predicates = [
            self.conversation_table.c.workspace_id == workspace_id,
            self.conversation_table.c.conversation_id == conversation_id,
        ]
        if user_id is not None:
            predicates.append(self.conversation_table.c.user_id == user_id)
        statement = select(self.conversation_table).where(*predicates).limit(1)
        with self.connection() as conn:
            row = conn.execute(statement).mappings().first()
        return row_dict(row) if row is not None else None

    def get_ai_conversation_page(
        self,
        workspace_id: str,
        conversation_id: str,
        *,
        user_id: str,
        limit: int = DEFAULT_CONVERSATION_MESSAGE_LIMIT,
        before: tuple[datetime, str] | None = None,
    ) -> JsonObject | None:
        """Read one authorized conversation and a newest-first keyset window in one transaction."""
        effective_limit = max(1, min(int(limit), MAX_CONVERSATION_MESSAGE_LIMIT))
        conversation = self.conversation_table
        message = self.message_table
        conversation_scope = (
            conversation.c.workspace_id == workspace_id,
            conversation.c.conversation_id == conversation_id,
            conversation.c.user_id == user_id,
        )
        conversation_statement = select(conversation).where(*conversation_scope).limit(1)
        message_statement = (
            select(message)
            .select_from(
                message.join(
                    conversation,
                    and_(
                        conversation.c.workspace_id == message.c.workspace_id,
                        conversation.c.conversation_id == message.c.conversation_id,
                    ),
                )
            )
            .where(
                *conversation_scope,
                message.c.workspace_id == workspace_id,
                message.c.conversation_id == conversation_id,
            )
            .order_by(message.c.created_at.desc(), message.c.message_id.desc())
            .limit(effective_limit + 1)
        )
        if before is not None:
            message_statement = message_statement.where(
                tuple_(message.c.created_at, message.c.message_id) < tuple_(before[0], before[1])
            )
        with self.connection() as conn:
            conversation_row = conn.execute(conversation_statement).mappings().first()
            if conversation_row is None:
                return None
            rows = [row_dict(row) for row in conn.execute(message_statement).mappings().all()]
        has_more = len(rows) > effective_limit
        selected = rows[:effective_limit]
        messages = list(reversed(selected))
        oldest = messages[0] if has_more and messages else None
        return {
            "conversation": row_dict(conversation_row),
            "messages": messages,
            "limit": effective_limit,
            "has_more": has_more,
            "next_position": (
                {
                    "ordered_at": oldest["created_at"],
                    "tie_breaker": str(oldest["message_id"]),
                }
                if oldest is not None
                else None
            ),
        }

    def delete_ai_conversation(
        self, workspace_id: str, conversation_id: str, *, user_id: str | None = None
    ) -> bool:
        predicates = [
            self.conversation_table.c.workspace_id == workspace_id,
            self.conversation_table.c.conversation_id == conversation_id,
        ]
        if user_id is not None:
            predicates.append(self.conversation_table.c.user_id == user_id)
        statement = (
            self.conversation_table.delete()
            .where(*predicates)
            .returning(self.conversation_table.c.conversation_id)
        )
        with self.connection() as conn:
            row = conn.execute(statement).first()
        return row is not None

    def delete_ai_conversations(
        self,
        workspace_id: str,
        *,
        user_id: str,
    ) -> int:
        statement = self.conversation_table.delete().where(
            self.conversation_table.c.workspace_id == workspace_id,
            self.conversation_table.c.user_id == user_id,
        )
        with self.connection() as conn:
            result = conn.execute(statement)
        return int(result.rowcount or 0)

    def list_ai_messages(
        self,
        workspace_id: str,
        conversation_id: str,
        *,
        newest: int | None = None,
    ) -> list[JsonObject]:
        """대화 메시지 목록. newest 지정 시 최근 N개만(시간 오름차순 반환)."""
        statement = (
            select(self.message_table)
            .where(
                self.message_table.c.workspace_id == workspace_id,
                self.message_table.c.conversation_id == conversation_id,
            )
            .order_by(self.message_table.c.created_at, self.message_table.c.message_id)
        )
        if newest is not None:
            statement = (
                statement.order_by(None)
                .order_by(
                    self.message_table.c.created_at.desc(),
                    self.message_table.c.message_id.desc(),
                )
                .limit(newest)
            )
        with self.connection() as conn:
            rows = [row_dict(row) for row in conn.execute(statement).mappings()]
        return list(reversed(rows)) if newest is not None else rows

    def record_llm_invocation_metric(self, sample: LlmInvocationMetric) -> None:
        statement = pg_insert(self.llm_metric_table).values(
            workspace_id=sample.workspace_id,
            provider=sample.provider,
            model=sample.model,
            operation=sample.operation,
            status=sample.status,
            latency_ms=sample.latency_ms,
            prompt_tokens=sample.prompt_tokens,
            completion_tokens=sample.completion_tokens,
            total_tokens=sample.total_tokens,
            estimated_cost_micros=sample.estimated_cost_micros,
            event_id=sample.event_id,
            correlation_id=sample.correlation_id,
            causation_id=sample.causation_id,
            error_type=sample.error_type,
        )
        with self.connection() as conn:
            conn.execute(statement)

    def llm_invocation_latency_avg_ms_by_provider_model_operation_status(
        self,
    ) -> dict[tuple[str, str, str, str], float]:
        table = self.llm_metric_table
        statement = select(
            table.c.provider,
            table.c.model,
            table.c.operation,
            table.c.status,
            func.avg(table.c.latency_ms).label("value"),
        ).group_by(table.c.provider, table.c.model, table.c.operation, table.c.status)
        return self._llm_metric_float_values(statement)

    def llm_invocation_latency_max_ms_by_provider_model_operation_status(
        self,
    ) -> dict[tuple[str, str, str, str], int]:
        table = self.llm_metric_table
        statement = select(
            table.c.provider,
            table.c.model,
            table.c.operation,
            table.c.status,
            func.max(table.c.latency_ms).label("value"),
        ).group_by(table.c.provider, table.c.model, table.c.operation, table.c.status)
        return {key: int(value) for key, value in self._llm_metric_float_values(statement).items()}

    def llm_invocation_total_tokens_by_provider_model_operation_status(
        self,
    ) -> dict[tuple[str, str, str, str], int]:
        table = self.llm_metric_table
        statement = select(
            table.c.provider,
            table.c.model,
            table.c.operation,
            table.c.status,
            func.sum(table.c.total_tokens).label("value"),
        ).group_by(table.c.provider, table.c.model, table.c.operation, table.c.status)
        return {key: int(value) for key, value in self._llm_metric_float_values(statement).items()}

    def llm_invocation_estimated_cost_micros_by_provider_model_operation_status(
        self,
    ) -> dict[tuple[str, str, str, str], int]:
        table = self.llm_metric_table
        statement = select(
            table.c.provider,
            table.c.model,
            table.c.operation,
            table.c.status,
            func.sum(table.c.estimated_cost_micros).label("value"),
        ).group_by(table.c.provider, table.c.model, table.c.operation, table.c.status)
        return {key: int(value) for key, value in self._llm_metric_float_values(statement).items()}

    def _llm_metric_float_values(self, statement: object) -> dict[tuple[str, str, str, str], float]:
        with self.connection() as conn:
            rows = conn.execute(statement).mappings().all()
        return {
            (
                str(row["provider"]),
                str(row["model"]),
                str(row["operation"]),
                str(row["status"]),
            ): float(row["value"] or 0)
            for row in rows
        }
