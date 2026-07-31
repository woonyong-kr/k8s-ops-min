"""Authorized desktop-to-agent broker for bounded TCP port-forward sessions."""

from __future__ import annotations

import asyncio
import json
import secrets
import time
from collections.abc import Awaitable, Callable, Mapping
from contextlib import suppress
from dataclasses import dataclass, field
from types import SimpleNamespace
from typing import Any

from agent_connections import AgentConnection, AgentConnectionRegistry
from fastapi import WebSocket
from fastapi.websockets import WebSocketDisconnect

from domains.service_access.router import (
    POD_SERVICE_REQUEST_UNSUPPORTED_REASON,
)
from domains.service_access.router import (
    _availability as port_forward_service_availability,
)
from domains.service_access.router import (
    _capability_revision as port_forward_capability_revision,
)
from domains.service_access.router import (
    _local_forward_availability as port_forward_local_availability,
)
from domains.service_access.router import (
    _require_service_access as require_port_forward_access,
)
from domains.service_access.router import (
    _resource_ports as port_forward_resource_ports,
)
from domains.service_access.router import (
    _scope_and_resource as port_forward_scope_and_resource,
)
from domains.target.management_guard import (
    cluster_role_from_policy,
    is_management_registration,
    is_management_role,
)
from packages.config.terminal import pod_exec_namespace_allowed
from packages.contracts.gateway.fields import Gateway
from packages.contracts.port_forward import (
    MAX_PORT_FORWARD_BYTES_PER_DIRECTION,
    MAX_PORT_FORWARD_CONNECTIONS_PER_SESSION,
    MAX_PORT_FORWARD_FRAME_BYTES,
    MAX_PORT_FORWARD_IDLE_SECONDS,
    MAX_PORT_FORWARD_OPEN_SECONDS,
    MAX_PORT_FORWARD_SESSION_SECONDS,
    MAX_PORT_FORWARD_SESSIONS_PER_AGENT,
    MAX_PORT_FORWARD_SESSIONS_PER_USER,
    PORT_FORWARD_CREDIT_WINDOW_BYTES,
    AgentPortForwardEvent,
    PortForwardClose,
    PortForwardConnectionClose,
    PortForwardConnectionEnd,
    PortForwardConnectionOpen,
    PortForwardConnectionOpened,
    PortForwardEnd,
    PortForwardError,
    PortForwardHalfClose,
    PortForwardOpen,
    PortForwardOpened,
    PortForwardStart,
    PortForwardWindow,
    decode_port_forward_data,
    parse_agent_port_forward_event,
    parse_desktop_port_forward_request,
)
from packages.events.envelope import event

CLOSE_BAD_REQUEST = 4400
CLOSE_UNAUTHORIZED = 4401
PORT_FORWARD_AUDIT_SOURCE = "realtime-gateway"
PORT_FORWARD_BROWSER_QUEUE_MAX = max(
    8,
    MAX_PORT_FORWARD_CONNECTIONS_PER_SESSION
    * (PORT_FORWARD_CREDIT_WINDOW_BYTES // MAX_PORT_FORWARD_FRAME_BYTES + 4),
)

PortForwardAuthorizer = Callable[[Any, str, str, PortForwardStart], Awaitable[bool]]
PortForwardAuditor = Callable[[str, str, dict[str, Any]], Awaitable[None]]


def database_port_forward_authorizer(db: Any) -> PortForwardAuthorizer:
    """Recompute the exact service-access capability before opening a tunnel."""

    async def authorize(
        session: Any,
        workspace_id: str,
        cluster_id: str,
        start: PortForwardStart,
    ) -> bool:
        if not workspace_id or not cluster_id or not _session_value(session, "user_id"):
            return False
        namespace = start.resource.namespace or ""
        if not pod_exec_namespace_allowed(namespace):
            return False
        try:
            return await asyncio.to_thread(
                _authorize_port_forward_sync,
                db,
                _session_view(session),
                workspace_id,
                cluster_id,
                start,
            )
        except Exception:
            return False

    return authorize


def _authorize_port_forward_sync(
    db: Any,
    session: Any,
    workspace_id: str,
    cluster_id: str,
    start: PortForwardStart,
) -> bool:
    registration = db.get_cluster_registration(workspace_id, cluster_id)
    policy_reader = getattr(db, "get_cluster_policy", None)
    policy = policy_reader(workspace_id, cluster_id) if callable(policy_reader) else None
    if (
        registration is None
        or is_management_registration(registration)
        or is_management_role(cluster_role_from_policy(policy))
    ):
        return False

    require_port_forward_access(db, session, workspace_id, cluster_id)
    resource_type = start.resource.kind.casefold()
    reader = getattr(db, "get_inventory_resource_by_api_version", None)
    if not callable(reader):
        return False
    row = reader(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource_type=resource_type,
        api_version="v1",
        kind="Pod" if resource_type == "pod" else "Service",
        namespace=start.resource.namespace,
        name=start.resource.name,
    )
    if not isinstance(row, Mapping):
        return False
    ports, discovery, discovery_reason = port_forward_resource_ports(row)
    scope, resource = port_forward_scope_and_resource(
        workspace_id,
        row,
        freshness="partial" if discovery == "partial" else "live",
    )
    if scope.cluster_id != cluster_id or resource != start.resource:
        return False
    if resource_type == "service":
        availability, reason = port_forward_service_availability(
            db, workspace_id, cluster_id, row, ports
        )
    else:
        availability, reason = "unavailable", POD_SERVICE_REQUEST_UNSUPPORTED_REASON
    local_forward, local_reason = port_forward_local_availability(
        db, workspace_id, cluster_id, row, ports
    )
    if local_forward != "desktop_required":
        return False
    if start.remote_port not in {port.port for port in ports}:
        return False
    revision = port_forward_capability_revision(
        current=session,
        scope=scope,
        resource=resource,
        inventory=row,
        ports=ports,
        availability=availability,
        reason=reason,
        local_port_forward=local_forward,
        local_port_forward_reason=local_reason,
        port_discovery=discovery,
        port_discovery_reason=discovery_reason,
    )
    return secrets.compare_digest(revision, start.capability_revision)


def database_port_forward_auditor(db: Any) -> PortForwardAuditor:
    async def record(subject: str, workspace_id: str, payload: dict[str, Any]) -> None:
        session_id = str(payload["session_id"])
        envelope = event(
            subject,
            PORT_FORWARD_AUDIT_SOURCE,
            payload,
            correlation_id=session_id,
            workspace_id=workspace_id,
        )
        await asyncio.to_thread(db.append_audit_log, envelope)

    return record


@dataclass
class PortForwardConnectionState:
    connection_id: int
    opened: bool = False
    closed: bool = False
    close_requested: bool = False
    desktop_credit: int = 0
    target_credit: int = PORT_FORWARD_CREDIT_WINDOW_BYTES
    next_desktop_sequence: int = 0
    next_target_sequence: int = 0
    desktop_to_target_bytes: int = 0
    target_to_desktop_bytes: int = 0


BrowserOutbound = bytes | AgentPortForwardEvent


@dataclass
class BrowserPortForwardSession:
    session_id: str
    generation: int
    workspace_id: str
    cluster_id: str
    user_id: str
    start: PortForwardStart
    browser: WebSocket
    agent: AgentConnection
    started_at: float = field(default_factory=time.monotonic)
    last_activity_at: float = field(default_factory=time.monotonic)
    done: asyncio.Event = field(default_factory=asyncio.Event)
    outbound: asyncio.Queue[BrowserOutbound] = field(
        default_factory=lambda: asyncio.Queue(maxsize=PORT_FORWARD_BROWSER_QUEUE_MAX)
    )
    connections: dict[int, PortForwardConnectionState] = field(default_factory=dict)
    opened_target: PortForwardOpened | None = None
    desktop_to_target_bytes: int = 0
    target_to_desktop_bytes: int = 0
    connection_count: int = 0
    terminal_recorded: bool = False

    def offer(self, message: BrowserOutbound) -> bool:
        try:
            self.outbound.put_nowait(message)
        except asyncio.QueueFull:
            return False
        return True


class PortForwardSessionBroker:
    def __init__(
        self,
        *,
        authorize: PortForwardAuthorizer,
        audit: PortForwardAuditor,
        connections: AgentConnectionRegistry,
    ) -> None:
        self.authorize = authorize
        self.audit = audit
        self.connections = connections
        self.sessions: dict[str, BrowserPortForwardSession] = {}

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
            await self._error(
                session,
                "agent_unavailable",
                "Target agent disconnected.",
                retryable=True,
            )

    async def handle_agent_payload(
        self,
        cluster_id: str,
        connection: AgentConnection,
        payload: object,
    ) -> bool | None:
        if not isinstance(payload, dict) or not str(payload.get("type") or "").startswith(
            "port_forward."
        ):
            return None
        try:
            message = parse_agent_port_forward_event(payload)
        except ValueError:
            session = self._payload_session(payload)
            if session is not None:
                await self._protocol_error(session, "Invalid agent port-forward control frame.")
            return True
        session = self.sessions.get(message.session_id or "")
        if session is None:
            return True
        if session.cluster_id != cluster_id:
            return False
        if session.agent is not connection:
            return True
        if message.generation != session.generation:
            await self._protocol_error(session, "Port-forward generation is stale.")
            return True
        session.last_activity_at = time.monotonic()

        if isinstance(message, PortForwardOpened):
            resource = session.start.resource
            if (
                session.opened_target is not None
                or message.target_kind.casefold() != resource.kind.casefold()
                or message.target_name != resource.name
                or message.target_uid != resource.uid
                or message.target_port != session.start.remote_port
            ):
                await self._protocol_error(
                    session, "Port-forward target receipt does not match the authorized target."
                )
                return True
            session.opened_target = message
        elif isinstance(message, PortForwardConnectionOpened):
            state = session.connections.get(message.connection_id)
            if state is None or state.opened or state.closed:
                await self._protocol_error(session, "Unknown port-forward connection was opened.")
                return True
            state.opened = True
        elif isinstance(message, PortForwardWindow):
            state = session.connections.get(message.connection_id)
            if (
                state is None
                or state.closed
                or message.direction != "desktop_to_target"
                or state.desktop_credit + message.credit_bytes > PORT_FORWARD_CREDIT_WINDOW_BYTES
            ):
                await self._protocol_error(session, "Invalid desktop-to-target credit window.")
                return True
            state.desktop_credit += message.credit_bytes
        elif isinstance(message, PortForwardHalfClose):
            if (
                message.connection_id not in session.connections
                or message.direction != "target_to_desktop"
            ):
                await self._protocol_error(session, "Unknown port-forward half-close.")
                return True
        elif isinstance(message, PortForwardConnectionEnd):
            state = session.connections.get(message.connection_id)
            if state is None or state.closed:
                await self._protocol_error(session, "Unknown port-forward connection ended.")
                return True
            if (
                message.desktop_to_target_bytes != state.desktop_to_target_bytes
                or message.target_to_desktop_bytes != state.target_to_desktop_bytes
            ):
                await self._protocol_error(
                    session, "Port-forward connection byte receipt is inconsistent."
                )
                return True
            state.closed = True
            session.connections.pop(message.connection_id, None)
        elif isinstance(message, PortForwardEnd):
            if (
                session.connections
                or message.desktop_to_target_bytes != session.desktop_to_target_bytes
                or message.target_to_desktop_bytes != session.target_to_desktop_bytes
            ):
                await self._protocol_error(session, "Port-forward byte receipt is inconsistent.")
                return True
            await self._finish(session, message)
            return True
        elif isinstance(message, PortForwardError):
            await self._finish(session, message)
            return True

        if not session.offer(message):
            await self._protocol_error(session, "Desktop port-forward delivery queue is full.")
        return True

    async def handle_agent_binary(
        self,
        cluster_id: str,
        connection: AgentConnection,
        raw: bytes,
    ) -> bool:
        try:
            frame = decode_port_forward_data(raw)
        except ValueError:
            # An un-attributable malformed binary frame is dropped under the
            # shared ingress budget; it must not tear down live summaries.
            return True
        session = self.sessions.get(frame.session_id)
        if session is None:
            return True
        if session.cluster_id != cluster_id:
            return False
        if session.agent is not connection:
            return True
        state = session.connections.get(frame.connection_id)
        if (
            frame.generation != session.generation
            or frame.direction != "target_to_desktop"
            or state is None
            or not state.opened
            or state.closed
            or frame.sequence != state.next_target_sequence
            or len(frame.payload) > state.target_credit
        ):
            await self._protocol_error(session, "Invalid target-to-desktop data frame.")
            return True
        next_total = session.target_to_desktop_bytes + len(frame.payload)
        if next_total > MAX_PORT_FORWARD_BYTES_PER_DIRECTION:
            await self._finish(
                session,
                PortForwardEnd(
                    session_id=session.session_id,
                    generation=session.generation,
                    reason="byte_limit",
                    desktop_to_target_bytes=session.desktop_to_target_bytes,
                    target_to_desktop_bytes=session.target_to_desktop_bytes,
                ),
            )
            return True
        state.next_target_sequence += 1
        state.target_credit -= len(frame.payload)
        state.target_to_desktop_bytes += len(frame.payload)
        session.target_to_desktop_bytes = next_total
        session.last_activity_at = time.monotonic()
        if not session.offer(raw):
            await self._protocol_error(session, "Desktop port-forward delivery queue is full.")
        return True

    async def serve_browser(self, websocket: WebSocket, session_identity: Any) -> None:
        workspace_id = websocket.query_params.get(Gateway.WORKSPACE_ID, "")
        cluster_id = websocket.query_params.get(Gateway.CLUSTER_ID, "")
        if not workspace_id or not cluster_id:
            await websocket.close(code=CLOSE_BAD_REQUEST)
            return
        if workspace_id != _session_value(session_identity, Gateway.WORKSPACE_ID):
            await websocket.close(code=CLOSE_UNAUTHORIZED)
            return
        user_id = _session_value(session_identity, "user_id")
        if not user_id:
            await websocket.close(code=CLOSE_UNAUTHORIZED)
            return
        if self._user_session_count(workspace_id, user_id) >= MAX_PORT_FORWARD_SESSIONS_PER_USER:
            await self._unbound_error(websocket, "session_limit", "Port-forward limit reached.")
            return
        if self._agent_session_count(cluster_id) >= MAX_PORT_FORWARD_SESSIONS_PER_AGENT:
            await self._unbound_error(
                websocket, "session_limit", "Agent port-forward limit reached."
            )
            return
        agent = self.connections.current(cluster_id)
        if agent is None:
            await self._unbound_error(
                websocket, "agent_unavailable", "Target agent is offline.", retryable=True
            )
            return
        try:
            raw = await asyncio.wait_for(
                websocket.receive_json(), timeout=MAX_PORT_FORWARD_OPEN_SECONDS
            )
            start = parse_desktop_port_forward_request(raw)
        except (TimeoutError, ValueError, WebSocketDisconnect):
            await websocket.close(code=CLOSE_BAD_REQUEST)
            return
        if not isinstance(start, PortForwardStart):
            await websocket.close(code=CLOSE_BAD_REQUEST)
            return
        if not await self.authorize(session_identity, workspace_id, cluster_id, start):
            await websocket.close(code=CLOSE_UNAUTHORIZED)
            return

        session = BrowserPortForwardSession(
            session_id=secrets.token_urlsafe(24),
            generation=1,
            workspace_id=workspace_id,
            cluster_id=cluster_id,
            user_id=user_id,
            start=start,
            browser=websocket,
            agent=agent,
        )
        try:
            await self._audit_started(session)
        except Exception:
            await self._unbound_error(
                websocket, "audit_unavailable", "Audit record unavailable.", retryable=True
            )
            return
        self.sessions[session.session_id] = session
        sender = asyncio.create_task(self._browser_sender(session))
        try:
            await agent.send_json(
                PortForwardOpen(
                    session_id=session.session_id,
                    generation=session.generation,
                    capability_revision=start.capability_revision,
                    resource=start.resource,
                    remote_port=start.remote_port,
                )
            )
            await self._browser_receive_loop(session)
        except WebSocketDisconnect:
            await self._browser_closed(session)
        except Exception:
            await self._error(
                session,
                "agent_unavailable",
                "Port-forward transport failed.",
                retryable=True,
            )
        finally:
            if session.done.is_set():
                with suppress(asyncio.TimeoutError, WebSocketDisconnect):
                    await asyncio.wait_for(sender, timeout=1)
            sender.cancel()
            with suppress(asyncio.CancelledError):
                await sender

    async def _browser_receive_loop(self, session: BrowserPortForwardSession) -> None:
        deadline = session.started_at + MAX_PORT_FORWARD_SESSION_SECONDS
        while not session.done.is_set():
            now = time.monotonic()
            if now >= deadline:
                await self._close_with_reason(session, "session_timeout")
                return
            if now - session.last_activity_at >= MAX_PORT_FORWARD_IDLE_SECONDS:
                await self._close_with_reason(session, "idle_timeout")
                return
            try:
                raw = await asyncio.wait_for(session.browser.receive(), timeout=1)
            except TimeoutError:
                continue
            if raw["type"] == "websocket.disconnect":
                raise WebSocketDisconnect(int(raw.get("code") or 1000))
            if raw.get("bytes") is not None:
                await self._browser_binary(session, bytes(raw["bytes"]))
                continue
            try:
                payload = json.loads(str(raw.get("text") or ""))
                message = parse_desktop_port_forward_request(payload)
            except (json.JSONDecodeError, ValueError):
                await self._protocol_error(session, "Invalid desktop port-forward control frame.")
                return
            if isinstance(message, PortForwardStart) or not self._owns(session, message):
                await self._protocol_error(session, "Port-forward session ownership mismatch.")
                return
            session.last_activity_at = time.monotonic()
            if isinstance(message, PortForwardConnectionOpen):
                if (
                    session.opened_target is None
                    or message.connection_id in session.connections
                    or len(session.connections) >= MAX_PORT_FORWARD_CONNECTIONS_PER_SESSION
                ):
                    await self._protocol_error(session, "Port-forward connection cannot be opened.")
                    return
                session.connections[message.connection_id] = PortForwardConnectionState(
                    connection_id=message.connection_id
                )
                session.connection_count += 1
            elif isinstance(message, PortForwardWindow):
                state = session.connections.get(message.connection_id)
                if (
                    state is None
                    or state.closed
                    or message.direction != "target_to_desktop"
                    or state.target_credit + message.credit_bytes > PORT_FORWARD_CREDIT_WINDOW_BYTES
                ):
                    await self._protocol_error(session, "Invalid target-to-desktop credit window.")
                    return
                state.target_credit += message.credit_bytes
            elif isinstance(message, PortForwardHalfClose):
                if (
                    message.connection_id not in session.connections
                    or message.direction != "desktop_to_target"
                ):
                    await self._protocol_error(session, "Unknown port-forward half-close.")
                    return
            elif isinstance(message, PortForwardConnectionClose):
                state = session.connections.get(message.connection_id)
                if state is None or state.closed or state.close_requested:
                    await self._protocol_error(session, "Unknown port-forward connection close.")
                    return
                state.close_requested = True
            elif isinstance(message, PortForwardClose):
                await session.agent.send_json(message)
                await self._finish(
                    session,
                    PortForwardEnd(
                        session_id=session.session_id,
                        generation=session.generation,
                        reason="closed",
                        desktop_to_target_bytes=session.desktop_to_target_bytes,
                        target_to_desktop_bytes=session.target_to_desktop_bytes,
                    ),
                )
                return
            await session.agent.send_json(message)

    async def _browser_binary(self, session: BrowserPortForwardSession, raw: bytes) -> None:
        try:
            frame = decode_port_forward_data(raw)
        except ValueError:
            await self._protocol_error(session, "Invalid desktop-to-target data frame.")
            return
        state = session.connections.get(frame.connection_id)
        if (
            frame.session_id != session.session_id
            or frame.generation != session.generation
            or frame.direction != "desktop_to_target"
            or state is None
            or not state.opened
            or state.closed
            or frame.sequence != state.next_desktop_sequence
            or len(frame.payload) > state.desktop_credit
        ):
            await self._protocol_error(session, "Invalid desktop-to-target data frame.")
            return
        next_total = session.desktop_to_target_bytes + len(frame.payload)
        if next_total > MAX_PORT_FORWARD_BYTES_PER_DIRECTION:
            await self._close_with_reason(session, "byte_limit")
            return
        state.next_desktop_sequence += 1
        state.desktop_credit -= len(frame.payload)
        state.desktop_to_target_bytes += len(frame.payload)
        session.desktop_to_target_bytes = next_total
        session.last_activity_at = time.monotonic()
        await session.agent.send_bytes(raw)

    async def _browser_sender(self, session: BrowserPortForwardSession) -> None:
        while True:
            message = await session.outbound.get()
            if isinstance(message, bytes):
                await session.browser.send_bytes(message)
            else:
                await session.browser.send_json(message.model_dump(mode="json"))
                if isinstance(message, (PortForwardEnd, PortForwardError)):
                    return

    async def _browser_closed(self, session: BrowserPortForwardSession) -> None:
        if session.done.is_set():
            return
        with suppress(Exception):
            await session.agent.send_json(
                PortForwardClose(session_id=session.session_id, generation=session.generation)
            )
        await self._finish(
            session,
            PortForwardEnd(
                session_id=session.session_id,
                generation=session.generation,
                reason="closed",
                desktop_to_target_bytes=session.desktop_to_target_bytes,
                target_to_desktop_bytes=session.target_to_desktop_bytes,
            ),
        )

    async def _close_with_reason(self, session: BrowserPortForwardSession, reason: str) -> None:
        with suppress(Exception):
            await session.agent.send_json(
                PortForwardClose(session_id=session.session_id, generation=session.generation)
            )
        await self._finish(
            session,
            PortForwardEnd(
                session_id=session.session_id,
                generation=session.generation,
                reason=reason,
                desktop_to_target_bytes=session.desktop_to_target_bytes,
                target_to_desktop_bytes=session.target_to_desktop_bytes,
            ),
        )

    async def _protocol_error(self, session: BrowserPortForwardSession, message: str) -> None:
        with suppress(Exception):
            await session.agent.send_json(
                PortForwardClose(session_id=session.session_id, generation=session.generation)
            )
        await self._error(session, "protocol_violation", message)

    async def _error(
        self,
        session: BrowserPortForwardSession,
        code: str,
        message: str,
        retryable: bool = False,
    ) -> None:
        await self._finish(
            session,
            PortForwardError(
                session_id=session.session_id,
                generation=session.generation,
                code=code,
                message=message,
                retryable=retryable,
            ),
        )

    async def _finish(
        self,
        session: BrowserPortForwardSession,
        message: PortForwardEnd | PortForwardError,
    ) -> None:
        if session.done.is_set():
            return
        while not session.outbound.empty():
            with suppress(asyncio.QueueEmpty):
                session.outbound.get_nowait()
        session.offer(message)
        session.done.set()
        self.sessions.pop(session.session_id, None)
        await self._record_finished(
            session,
            reason=message.reason if isinstance(message, PortForwardEnd) else "error",
            error_code=message.code if isinstance(message, PortForwardError) else None,
        )

    async def _audit_started(self, session: BrowserPortForwardSession) -> None:
        resource = session.start.resource
        await self.audit(
            "port_forward.session.started",
            session.workspace_id,
            {
                "session_id": session.session_id,
                "generation": session.generation,
                "cluster_id": session.cluster_id,
                "namespace": resource.namespace,
                "resource_kind": resource.kind,
                "resource_name": resource.name,
                "resource_uid": resource.uid,
                "remote_port": session.start.remote_port,
                "capability_revision": session.start.capability_revision,
                "requested_by": session.user_id,
            },
        )

    async def _record_finished(
        self,
        session: BrowserPortForwardSession,
        *,
        reason: str,
        error_code: str | None,
    ) -> None:
        if session.terminal_recorded:
            return
        session.terminal_recorded = True
        target = session.opened_target
        try:
            await self.audit(
                "port_forward.session.finished",
                session.workspace_id,
                {
                    "session_id": session.session_id,
                    "generation": session.generation,
                    "cluster_id": session.cluster_id,
                    "namespace": session.start.resource.namespace,
                    "resource_kind": session.start.resource.kind,
                    "resource_name": session.start.resource.name,
                    "resource_uid": session.start.resource.uid,
                    "remote_port": session.start.remote_port,
                    "capability_revision": session.start.capability_revision,
                    "requested_by": session.user_id,
                    "target_kind": target.target_kind if target else None,
                    "target_name": target.target_name if target else None,
                    "target_uid": target.target_uid if target else None,
                    "target_port": target.target_port if target else None,
                    "connections": session.connection_count,
                    "desktop_to_target_bytes": session.desktop_to_target_bytes,
                    "target_to_desktop_bytes": session.target_to_desktop_bytes,
                    "duration_ms": int((time.monotonic() - session.started_at) * 1000),
                    "reason": reason,
                    "error_code": error_code,
                },
            )
        except Exception:
            pass

    async def _unbound_error(
        self,
        websocket: WebSocket,
        code: str,
        message: str,
        retryable: bool = False,
    ) -> None:
        await websocket.send_json(
            PortForwardError(
                session_id=None,
                generation=None,
                code=code,
                message=message,
                retryable=retryable,
            ).model_dump(mode="json")
        )

    def _payload_session(self, payload: Mapping[str, Any]) -> BrowserPortForwardSession | None:
        session_id = str(payload.get("session_id") or "")
        generation = payload.get("generation")
        session = self.sessions.get(session_id)
        return session if session is not None and generation == session.generation else None

    @staticmethod
    def _owns(session: BrowserPortForwardSession, message: Any) -> bool:
        return (
            getattr(message, "session_id", None) == session.session_id
            and getattr(message, "generation", None) == session.generation
        )

    def _user_session_count(self, workspace_id: str, user_id: str) -> int:
        return sum(
            1
            for session in self.sessions.values()
            if session.workspace_id == workspace_id and session.user_id == user_id
        )

    def _agent_session_count(self, cluster_id: str) -> int:
        return sum(1 for session in self.sessions.values() if session.cluster_id == cluster_id)


def _session_view(session: Any) -> Any:
    if not isinstance(session, dict):
        return session
    return SimpleNamespace(
        user_id=str(session.get("user_id") or ""),
        workspace_id=str(session.get(Gateway.WORKSPACE_ID) or ""),
        roles=tuple(str(role) for role in (session.get("roles") or ())),
    )


def _session_value(session: Any, name: str) -> str:
    if session is None:
        return ""
    value = session.get(name, "") if isinstance(session, dict) else getattr(session, name, "")
    return str(value or "")
