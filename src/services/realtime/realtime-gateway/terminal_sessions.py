"""Authorized browser-to-agent broker for bounded Kubernetes pod exec sessions."""

from __future__ import annotations

import asyncio
import hashlib
import secrets
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any

from agent_connections import AgentConnection, AgentConnectionRegistry
from fastapi import WebSocket
from fastapi.websockets import WebSocketDisconnect

from domains.target.management_guard import (
    cluster_role_from_policy,
    is_management_registration,
    is_management_role,
)
from packages.config.terminal import pod_exec_namespace_allowed
from packages.contracts.gateway.fields import Gateway
from packages.contracts.identity import AccessResourceType, Permission, ServiceRole
from packages.contracts.terminal import (
    MAX_TERMINAL_INPUT_BYTES,
    MAX_TERMINAL_OUTPUT_BYTES,
    MAX_TERMINAL_OUTPUT_CHUNK_LENGTH,
    MAX_TERMINAL_SESSION_SECONDS,
    MAX_TERMINAL_SESSIONS_PER_USER,
    AgentTerminalEvent,
    TerminalClose,
    TerminalEnd,
    TerminalError,
    TerminalExec,
    TerminalInput,
    TerminalOutput,
    TerminalStart,
    parse_agent_terminal_event,
    parse_browser_terminal_request,
)
from packages.events.envelope import event
from packages.security.log_lines import redact_log_line

CLOSE_BAD_REQUEST = 4400
CLOSE_UNAUTHORIZED = 4401
TERMINAL_START_TIMEOUT_SECONDS = 15
TERMINAL_AUDIT_SOURCE = "realtime-gateway"
TERMINAL_BROWSER_QUEUE_MAX = max(
    4,
    MAX_TERMINAL_OUTPUT_BYTES // MAX_TERMINAL_OUTPUT_CHUNK_LENGTH,
)

TerminalAuthorizer = Callable[[Any, str, str, str, str, str], Awaitable[bool]]
TerminalAuditor = Callable[[str, str, dict[str, Any]], Awaitable[None]]


def database_terminal_authorizer(db: Any) -> TerminalAuthorizer:
    """Require exact inventory target plus service-admin or explicit pod.exec permission."""

    async def authorize(
        session: Any,
        workspace_id: str,
        cluster_id: str,
        namespace: str,
        pod: str,
        container: str,
    ) -> bool:
        if not all((workspace_id, cluster_id, namespace, pod, container)):
            return False
        if not pod_exec_namespace_allowed(namespace):
            return False
        user_id = _session_value(session, "user_id")
        if not user_id:
            return False
        try:
            registration = await asyncio.to_thread(
                db.get_cluster_registration, workspace_id, cluster_id
            )
            policy_reader = getattr(db, "get_cluster_policy", None)
            policy = (
                await asyncio.to_thread(policy_reader, workspace_id, cluster_id)
                if callable(policy_reader)
                else None
            )
            if (
                registration is None
                or is_management_registration(registration)
                or is_management_role(cluster_role_from_policy(policy))
            ):
                return False
            roles = _session_roles(session)
            if ServiceRole.SERVICE_ADMIN.value not in roles:
                allowed = await asyncio.to_thread(
                    db.can_access,
                    user_id,
                    workspace_id,
                    AccessResourceType.CLUSTER.value,
                    cluster_id,
                    Permission.POD_EXEC.value,
                )
                if not allowed:
                    return False
            resource = await asyncio.to_thread(
                db.get_inventory_resource,
                workspace_id=workspace_id,
                cluster_id=cluster_id,
                resource_type="pod",
                kind="Pod",
                namespace=namespace,
                name=pod,
            )
            return resource is not None and container in _container_names(resource)
        except Exception:
            return False

    return authorize


def database_terminal_auditor(db: Any) -> TerminalAuditor:
    async def record(subject: str, workspace_id: str, payload: dict[str, Any]) -> None:
        session_id = str(payload["session_id"])
        envelope = event(
            subject,
            TERMINAL_AUDIT_SOURCE,
            payload,
            correlation_id=session_id,
            workspace_id=workspace_id,
        )
        await asyncio.to_thread(db.append_audit_log, envelope)

    return record


@dataclass
class BrowserTerminalSession:
    session_id: str
    workspace_id: str
    cluster_id: str
    namespace: str
    pod: str
    container: str
    user_id: str
    command_hash: str
    command_length: int
    browser: WebSocket
    agent: AgentConnection
    started_at: float = field(default_factory=time.monotonic)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    outbound: asyncio.Queue[AgentTerminalEvent] = field(
        default_factory=lambda: asyncio.Queue(maxsize=TERMINAL_BROWSER_QUEUE_MAX)
    )
    input_bytes: int = 0
    output_bytes: int = 0
    terminal_recorded: bool = False
    terminal_receipt_queued: bool = False

    def offer_browser(self, message: AgentTerminalEvent) -> bool:
        try:
            self.outbound.put_nowait(message)
        except asyncio.QueueFull:
            return False
        return True


class TerminalSessionBroker:
    def __init__(
        self,
        *,
        authorize: TerminalAuthorizer,
        audit: TerminalAuditor,
        connections: AgentConnectionRegistry,
    ) -> None:
        self.authorize = authorize
        self.audit = audit
        self.connections = connections
        self.sessions: dict[str, BrowserTerminalSession] = {}

    async def agent_disconnected(
        self,
        cluster_id: str,
        connection: AgentConnection,
    ) -> None:
        affected = [
            session
            for session in self.sessions.values()
            if session.cluster_id == cluster_id and session.agent is connection
        ]
        for session in affected:
            await self._send_error(
                session,
                code="agent_unavailable",
                message="Target agent disconnected.",
                retryable=True,
            )

    async def handle_agent_payload(
        self,
        cluster_id: str,
        connection: AgentConnection,
        payload: object,
    ) -> bool | None:
        if not isinstance(payload, dict) or not str(payload.get("type") or "").startswith(
            "terminal."
        ):
            return None
        try:
            message = parse_agent_terminal_event(payload)
        except ValueError:
            return False
        session_id = message.session_id
        session = self.sessions.get(session_id or "")
        # A browser may close just before the target reports its final frame.
        # The frame is still valid terminal.v1 traffic and must not tear down the
        # shared agent socket merely because its session was already collected.
        if session is None:
            return True
        if session.cluster_id != cluster_id:
            return False
        if session.agent is not connection:
            # A replaced agent socket may still have buffered frames. They are
            # stale for every session bound to the authoritative connection.
            return True
        if isinstance(message, TerminalOutput):
            redacted = redact_log_line(message.data)
            next_size = session.output_bytes + len(redacted.encode("utf-8"))
            if next_size > MAX_TERMINAL_OUTPUT_BYTES:
                await session.agent.send_json(TerminalClose(session_id=session.session_id))
                await self._finish(
                    session,
                    TerminalEnd(
                        session_id=session.session_id,
                        exit_code=None,
                        reason="output_limit",
                    ),
                )
                return True
            session.output_bytes = next_size
            message = message.model_copy(update={"data": redacted})
        if isinstance(message, (TerminalEnd, TerminalError)):
            if session.outbound.full():
                with suppress(Exception):
                    await session.agent.send_json(TerminalClose(session_id=session.session_id))
                message = TerminalEnd(
                    session_id=session.session_id,
                    exit_code=None,
                    reason="output_limit",
                )
            await self._finish(session, message)
        elif not session.offer_browser(message):
            await self._terminate_slow_browser(session)
        return True

    async def serve_browser(self, websocket: WebSocket, session_identity: Any) -> None:
        params = websocket.query_params
        workspace_id = params.get(Gateway.WORKSPACE_ID, "")
        cluster_id = params.get(Gateway.CLUSTER_ID, "")
        namespace = params.get(Gateway.NAMESPACE, "")
        pod = params.get("pod", "")
        container = params.get("container", "")
        if not all((workspace_id, cluster_id, namespace, pod, container)):
            await websocket.close(code=CLOSE_BAD_REQUEST)
            return
        if workspace_id != _session_value(session_identity, Gateway.WORKSPACE_ID):
            await websocket.close(code=CLOSE_UNAUTHORIZED)
            return
        if not await self.authorize(
            session_identity, workspace_id, cluster_id, namespace, pod, container
        ):
            await websocket.close(code=CLOSE_UNAUTHORIZED)
            return
        user_id = _session_value(session_identity, "user_id")
        if self._user_session_count(workspace_id, user_id) >= MAX_TERMINAL_SESSIONS_PER_USER:
            await self._send_unbound_error(
                websocket, "session_limit", "Terminal session limit reached."
            )
            return
        agent = self.connections.current(cluster_id)
        if agent is None:
            await self._send_unbound_error(
                websocket, "agent_unavailable", "Target agent is offline.", True
            )
            return
        try:
            raw_start = await asyncio.wait_for(
                websocket.receive_json(), timeout=TERMINAL_START_TIMEOUT_SECONDS
            )
            start = parse_browser_terminal_request(raw_start)
        except (TimeoutError, ValueError, WebSocketDisconnect):
            await websocket.close(code=CLOSE_BAD_REQUEST)
            return
        if not isinstance(start, TerminalStart):
            await websocket.close(code=CLOSE_BAD_REQUEST)
            return

        terminal = BrowserTerminalSession(
            session_id=secrets.token_urlsafe(24),
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            namespace=namespace,
            pod=pod,
            container=container,
            user_id=user_id,
            command_hash=hashlib.sha256(start.command.encode("utf-8")).hexdigest(),
            command_length=len(start.command),
            browser=websocket,
            agent=agent,
        )
        try:
            await self._audit_started(terminal)
        except Exception:
            await self._send_unbound_error(
                websocket, "audit_unavailable", "Audit record unavailable.", True
            )
            return
        self.sessions[terminal.session_id] = terminal
        sender = asyncio.create_task(self._browser_sender(terminal))
        try:
            await agent.send_json(
                TerminalExec(
                    session_id=terminal.session_id,
                    namespace=namespace,
                    pod=pod,
                    container=container,
                    command=start.command,
                    timeout_seconds=MAX_TERMINAL_SESSION_SECONDS,
                    tty=False,
                )
            )
            await self._browser_receive_loop(terminal)
        except WebSocketDisconnect:
            await self._browser_closed(terminal)
        except Exception:
            await self._send_error(
                terminal,
                code="agent_unavailable",
                message="Terminal transport failed.",
                retryable=True,
            )
        finally:
            if terminal.terminal_receipt_queued:
                with suppress(Exception):
                    await asyncio.wait_for(sender, timeout=1)
            sender.cancel()
            with suppress(asyncio.CancelledError, Exception):
                await sender

    async def _browser_sender(self, session: BrowserTerminalSession) -> None:
        while True:
            message = await session.outbound.get()
            await session.browser.send_json(message.model_dump(mode="json"))
            if isinstance(message, (TerminalEnd, TerminalError)):
                return

    async def _terminate_slow_browser(self, session: BrowserTerminalSession) -> None:
        with suppress(Exception):
            await session.agent.send_json(TerminalClose(session_id=session.session_id))
        await self._finish(
            session,
            TerminalEnd(
                session_id=session.session_id,
                exit_code=None,
                reason="output_limit",
            ),
        )

    async def _browser_receive_loop(self, session: BrowserTerminalSession) -> None:
        deadline = session.started_at + MAX_TERMINAL_SESSION_SECONDS
        while not session.done.is_set():
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                await session.agent.send_json(TerminalClose(session_id=session.session_id))
                await self._finish(
                    session,
                    TerminalEnd(session_id=session.session_id, exit_code=None, reason="timeout"),
                )
                return
            try:
                raw = await asyncio.wait_for(
                    session.browser.receive_json(), timeout=min(1, remaining)
                )
            except TimeoutError:
                continue
            message = parse_browser_terminal_request(raw)
            if isinstance(message, TerminalStart) or message.session_id != session.session_id:
                await session.browser.close(code=CLOSE_BAD_REQUEST)
                await self._browser_closed(session)
                return
            if isinstance(message, TerminalInput):
                next_size = session.input_bytes + len(message.data.encode("utf-8"))
                if next_size > MAX_TERMINAL_INPUT_BYTES:
                    await session.agent.send_json(TerminalClose(session_id=session.session_id))
                    await self._finish(
                        session,
                        TerminalEnd(
                            session_id=session.session_id,
                            exit_code=None,
                            reason="closed",
                        ),
                    )
                    return
                session.input_bytes = next_size
                await session.agent.send_json(message)
            else:
                await session.agent.send_json(message)
                await self._browser_closed(session)

    async def _browser_closed(self, session: BrowserTerminalSession) -> None:
        if session.done.is_set():
            return
        with suppress(Exception):
            await session.agent.send_json(TerminalClose(session_id=session.session_id))
        await self._record_finished(session, exit_code=None, reason="closed", error_code=None)
        session.done.set()
        self.sessions.pop(session.session_id, None)

    async def _send_error(
        self,
        session: BrowserTerminalSession,
        *,
        code: str,
        message: str,
        retryable: bool,
    ) -> None:
        await self._finish(
            session,
            TerminalError(
                session_id=session.session_id,
                code=code,
                message=message,
                retryable=retryable,
            ),
        )

    async def _finish(
        self,
        session: BrowserTerminalSession,
        message: TerminalEnd | TerminalError,
    ) -> None:
        if session.done.is_set():
            return
        if not session.offer_browser(message):
            while not session.outbound.empty():
                with suppress(asyncio.QueueEmpty):
                    session.outbound.get_nowait()
            session.offer_browser(message)
        session.terminal_receipt_queued = True
        await self._record_finished(
            session,
            exit_code=message.exit_code if isinstance(message, TerminalEnd) else None,
            reason=message.reason if isinstance(message, TerminalEnd) else "error",
            error_code=message.code if isinstance(message, TerminalError) else None,
        )
        session.done.set()
        self.sessions.pop(session.session_id, None)

    async def _audit_started(self, session: BrowserTerminalSession) -> None:
        await self.audit(
            "terminal.session.started",
            session.workspace_id,
            {
                "session_id": session.session_id,
                "cluster_id": session.cluster_id,
                "namespace": session.namespace,
                "resource_kind": "Pod",
                "resource_name": session.pod,
                "container": session.container,
                "requested_by": session.user_id,
                "command_sha256": session.command_hash,
                "command_length": session.command_length,
                "tty": False,
            },
        )

    async def _record_finished(
        self,
        session: BrowserTerminalSession,
        *,
        exit_code: int | None,
        reason: str,
        error_code: str | None,
    ) -> None:
        if session.terminal_recorded:
            return
        session.terminal_recorded = True
        try:
            await self.audit(
                "terminal.session.finished",
                session.workspace_id,
                {
                    "session_id": session.session_id,
                    "cluster_id": session.cluster_id,
                    "namespace": session.namespace,
                    "resource_kind": "Pod",
                    "resource_name": session.pod,
                    "container": session.container,
                    "requested_by": session.user_id,
                    "command_sha256": session.command_hash,
                    "command_length": session.command_length,
                    "exit_code": exit_code,
                    "reason": reason,
                    "error_code": error_code,
                    "input_bytes": session.input_bytes,
                    "output_bytes": session.output_bytes,
                    "duration_ms": int((time.monotonic() - session.started_at) * 1000),
                },
            )
        except Exception:
            # The start record is fail-closed. A terminal audit sink outage after
            # execution cannot be repaired by exposing raw command/output in logs.
            pass

    async def _send_unbound_error(
        self,
        websocket: WebSocket,
        code: str,
        message: str,
        retryable: bool = False,
    ) -> None:
        await websocket.send_json(
            TerminalError(
                session_id=None,
                code=code,
                message=message,
                retryable=retryable,
            ).model_dump(mode="json")
        )

    def _user_session_count(self, workspace_id: str, user_id: str) -> int:
        return sum(
            1
            for session in self.sessions.values()
            if session.workspace_id == workspace_id and session.user_id == user_id
        )


def _container_names(resource: dict[str, Any]) -> set[str]:
    summary = resource.get("summary")
    containers = summary.get("containers", []) if isinstance(summary, dict) else []
    return {
        str(container["name"])
        for container in containers
        if isinstance(container, dict) and isinstance(container.get("name"), str)
    }


def _session_roles(session: Any) -> set[str]:
    raw = session.get("roles", []) if isinstance(session, dict) else getattr(session, "roles", [])
    if not isinstance(raw, list | tuple | set):
        return set()
    return {str(role) for role in raw}


def _session_value(session: Any, name: str) -> str:
    if session is None:
        return ""
    value = session.get(name, "") if isinstance(session, dict) else getattr(session, name, "")
    return str(value or "")
