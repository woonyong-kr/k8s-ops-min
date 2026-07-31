from __future__ import annotations

from collections.abc import Collection
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Protocol

from packages.contracts.event_bus.interfaces import EventEnvelope, EventRecorder, JsonObject

CommandRecord = dict[str, Any]


@dataclass(frozen=True)
class EventProcessingRecord:
    status: str
    attempts: int


class InitializableStore(Protocol):
    def init(self) -> None: ...

    def verify_schema(self) -> None: ...


class EventProcessingStore(EventRecorder, Protocol):
    def begin_event_processing(
        self, evt: EventEnvelope, consumer: str
    ) -> EventProcessingRecord: ...

    def finish_event_processing(
        self, evt: EventEnvelope, consumer: str, duration_ms: int | None = None
    ) -> None: ...

    def fail_event_processing(
        self,
        evt: EventEnvelope,
        consumer: str,
        error: str,
        status: str,
        duration_ms: int | None = None,
    ) -> None: ...

    def unit_of_work(self) -> AbstractContextManager[Any]: ...  # 트랜잭션 컨텍스트

    def stage_events(self, conn: Any, events: list[EventEnvelope]) -> None: ...  # outbox 적재


class DeadLetterStore(Protocol):
    def record_dead_letter(
        self, evt: EventEnvelope, consumer: str, error: str, attempts: int
    ) -> JsonObject: ...

    def record_raw_dead_letter(self, raw: bytes, consumer: str, error: str) -> JsonObject: ...

    def list_dead_letters(self, limit: int) -> list[JsonObject]: ...

    def get_dead_letter(self, dead_letter_id: int) -> JsonObject | None: ...

    def mark_dead_letter_replayed(self, dead_letter_id: int, replay_event_id: str) -> bool: ...


class UserStore(Protocol):
    def get_user_by_email(self, email: str) -> JsonObject | None: ...

    def get_user_by_id(self, user_id: str) -> JsonObject | None: ...

    def create_user(
        self,
        user_id: str,
        email: str,
        password_hash: str,
        display_name: str,
        status: str,
        role: str,
    ) -> JsonObject | None: ...

    def complete_email_verification(self, user_id: str) -> JsonObject | None: ...

    def approve_user(self, user_id: str, workspace_id: str) -> JsonObject | None: ...

    def get_default_workspace_id_for_user(self, user_id: str) -> str | None: ...

    def list_authorized_workspaces(
        self, user_id: str, *, service_admin: bool
    ) -> list[JsonObject]: ...

    def list_active_group_ids_for_user(self, user_id: str, workspace_id: str) -> list[str]: ...

    def grant_resource_access(self, payload: JsonObject) -> JsonObject: ...

    def can_access(
        self,
        user_id: str,
        organization_id: str,
        resource_type: str,
        resource_id: str,
        permission: str,
    ) -> bool: ...

    def user_has_resource_access(
        self,
        user_id: str,
        workspace_id: str,
        resource_type: str,
        resource_id: str,
        action: str,
    ) -> bool: ...

    def accessible_resource_ids(
        self,
        user_id: str,
        workspace_id: str,
        resource_type: str,
        action: str,
    ) -> set[str] | None: ...


class EvidenceQueryStore(Protocol):
    """인가된 cluster 집합 밖의 raw evidence를 읽지 않는 동기 저장소 계약."""

    def list_evidence(
        self,
        workspace_id: str,
        allowed_cluster_ids: Collection[str] | None,
        *,
        correlation_id: str | None = None,
        kind: str | None = None,
        since: datetime | None = None,
        until: datetime | None = None,
        limit: int = 50,
        offset: int = 0,
        cursor: tuple[datetime, int] | None = None,
    ) -> list[JsonObject]: ...

    def list_evidence_windows(
        self,
        workspace_id: str,
        allowed_cluster_ids: Collection[str] | None,
        *,
        limit: int,
        offset: int = 0,
    ) -> list[JsonObject]: ...

    def get_evidence(
        self,
        workspace_id: str,
        evidence_key: str,
        allowed_cluster_ids: Collection[str] | None,
    ) -> JsonObject | None: ...


class SessionStore(Protocol):
    async def create_session(
        self,
        user_id: str,
        roles: list[str] | None = None,
        workspace_id: str | None = None,
        display_name: str | None = None,
        email: str | None = None,
        auth_mode: str = "password",
    ) -> Any: ...

    async def get_session(self, token: str | None) -> Any | None: ...

    async def touch_session(self, token: str | None) -> bool: ...

    async def delete_session(self, token: str) -> None: ...

    async def check_rate_limit(
        self,
        key: str,
        limit: int | None = None,
        window_seconds: int | None = None,
    ) -> None: ...

    async def check_escalating_rate_limit(
        self,
        key: str,
        limit: int,
        window_seconds: int,
        lock_steps_seconds: tuple[int, ...],
        strike_ttl_seconds: int,
    ) -> None: ...

    async def create_email_verification_token(self, user_id: str, email: str) -> str: ...

    async def consume_email_verification_token(self, token: str | None) -> JsonObject | None: ...


class ManagementPlaneClient(Protocol):
    async def register_agent(
        self, cluster_id: str, agent_id: str, capabilities: list[str]
    ) -> None: ...

    async def report_agent_status(
        self,
        cluster_id: str,
        agent_id: str,
        capabilities: list[str],
        details: JsonObject,
    ) -> None: ...

    async def poll_command(
        self, cluster_id: str, workspace_id: str, agent_id: str, timeout_seconds: int
    ) -> CommandRecord | None: ...

    async def start_command(
        self,
        command_id: str,
        cluster_id: str,
        workspace_id: str,
        lease_id: str,
        agent_id: str,
        attempt_id: str | None = None,
    ) -> None: ...

    async def heartbeat_command(
        self,
        command_id: str,
        cluster_id: str,
        workspace_id: str,
        lease_id: str,
        agent_id: str,
        attempt_id: str | None = None,
        observed_cancel_generation: int | None = None,
        progress: JsonObject | None = None,
    ) -> JsonObject: ...

    async def complete_command(
        self,
        command_id: str,
        workspace_id: str,
        lease_id: str,
        agent_id: str,
        result: JsonObject,
        attempt_id: str | None = None,
    ) -> None: ...

    async def schedule_evidence_jobs(
        self,
        source_id: str,
        window_start: str,
        provider_keys: list[str],
    ) -> JsonObject: ...

    async def poll_evidence_job(
        self,
        provider_key: str,
        agent_id: str,
        timeout_seconds: int,
    ) -> JsonObject | None: ...

    async def complete_evidence_job(
        self,
        job_id: str,
        agent_id: str,
        lease_id: str,
        status: str,
        result: JsonObject,
        error: str,
    ) -> JsonObject: ...

    async def record_inventory_snapshot(self, payload: JsonObject) -> JsonObject: ...

    async def fetch_policy(self, cluster_id: str, generation: int) -> JsonObject | None: ...

    async def report_policy_status(self, status: JsonObject) -> None: ...

    async def fetch_prometheus_integration(self, revision: str) -> JsonObject: ...

    async def report_prometheus_integration_status(self, status: JsonObject) -> None: ...

    async def report_reconcile_status(self, status: JsonObject) -> None: ...


class OutboxReader(Protocol):
    async def unsent_events(self, limit: int, source: str | None) -> list[EventEnvelope]: ...

    async def mark_events_sent(self, event_ids: list[str]) -> None: ...

    async def mark_events_dead_lettered(
        self, events: list[EventEnvelope], consumer: str, error: str
    ) -> None: ...
