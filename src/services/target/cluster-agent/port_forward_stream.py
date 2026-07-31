"""Agent-owned bounded TCP relay multiplexed on the existing realtime socket."""

from __future__ import annotations

import asyncio
import ipaddress
import time
from collections.abc import Awaitable, Callable
from contextlib import suppress
from dataclasses import dataclass, field
from typing import Any, Protocol
from urllib.parse import quote

import httpx
from kubernetes_api import (
    kubernetes_api_base_url,
    kubernetes_client,
    kubernetes_headers,
    service_account_token,
)

from packages.contracts.parity import ResourceRef
from packages.contracts.port_forward import (
    MAX_PORT_FORWARD_BYTES_PER_DIRECTION,
    MAX_PORT_FORWARD_CONNECTIONS_PER_SESSION,
    MAX_PORT_FORWARD_FRAME_BYTES,
    MAX_PORT_FORWARD_IDLE_SECONDS,
    MAX_PORT_FORWARD_OPEN_SECONDS,
    MAX_PORT_FORWARD_SESSION_SECONDS,
    MAX_PORT_FORWARD_SESSIONS_PER_AGENT,
    PORT_FORWARD_CREDIT_WINDOW_BYTES,
    AgentPortForwardEvent,
    PortForwardClose,
    PortForwardConnectionClose,
    PortForwardConnectionEnd,
    PortForwardConnectionOpen,
    PortForwardConnectionOpened,
    PortForwardDataFrame,
    PortForwardEnd,
    PortForwardError,
    PortForwardHalfClose,
    PortForwardOpen,
    PortForwardOpened,
    PortForwardWindow,
    decode_port_forward_data,
    encode_port_forward_data,
    parse_agent_port_forward_request,
)

PortForwardEventEmitter = Callable[[AgentPortForwardEvent], Awaitable[None]]
PortForwardDataEmitter = Callable[[bytes], Awaitable[None]]


class TcpReader(Protocol):
    async def read(self, size: int = -1) -> bytes: ...


class TcpWriter(Protocol):
    def write(self, data: bytes) -> None: ...

    async def drain(self) -> None: ...

    def can_write_eof(self) -> bool: ...

    def write_eof(self) -> None: ...

    def close(self) -> None: ...

    async def wait_closed(self) -> None: ...


@dataclass(frozen=True, slots=True)
class ResolvedTcpTarget:
    kind: str
    name: str
    uid: str
    host: str
    port: int


TargetResolver = Callable[[ResourceRef, int], Awaitable[ResolvedTcpTarget]]
TcpConnector = Callable[[str, int], Awaitable[tuple[TcpReader, TcpWriter]]]


class PortForwardTargetError(RuntimeError):
    pass


class KubernetesTcpTargetResolver:
    """Resolve only an exact current Pod or Service observed by Kubernetes."""

    def __init__(self, transport: httpx.AsyncBaseTransport | None = None) -> None:
        self.transport = transport

    async def __call__(self, resource: ResourceRef, remote_port: int) -> ResolvedTcpTarget:
        base_url = (kubernetes_api_base_url() or "").rstrip("/")
        token = service_account_token() or ""
        namespace = resource.namespace or ""
        if not base_url or not token or not namespace:
            raise PortForwardTargetError("Kubernetes target identity is unavailable")
        kind = resource.kind.casefold()
        collection = "pods" if kind == "pod" else "services" if kind == "service" else ""
        if not collection:
            raise PortForwardTargetError("Unsupported port-forward target kind")
        path = (
            f"{base_url}/api/v1/namespaces/{quote(namespace, safe='')}/"
            f"{collection}/{quote(resource.name, safe='')}"
        )
        async with kubernetes_client(self.transport) as client:
            response = await client.get(path, headers=kubernetes_headers(token))
            if response.status_code == 404:
                raise PortForwardTargetError("Port-forward target no longer exists")
            response.raise_for_status()
            body = response.json()
        if not isinstance(body, dict):
            raise PortForwardTargetError("Port-forward target response is invalid")
        metadata = body.get("metadata")
        if not isinstance(metadata, dict) or (
            str(metadata.get("namespace") or ""),
            str(metadata.get("name") or ""),
            str(metadata.get("uid") or ""),
        ) != (namespace, resource.name, resource.uid):
            raise PortForwardTargetError("Port-forward target identity changed")
        if kind == "pod":
            return _resolve_pod(body, remote_port)
        return _resolve_service(body, remote_port)


@dataclass
class AgentTcpConnection:
    connection_id: int
    reader: TcpReader
    writer: TcpWriter
    input_credit: int
    output_credit: int
    expected_input_sequence: int = 0
    output_sequence: int = 0
    desktop_to_target_bytes: int = 0
    target_to_desktop_bytes: int = 0
    desktop_half_closed: bool = False
    target_half_closed: bool = False
    done: bool = False
    output_credit_changed: asyncio.Condition = field(default_factory=asyncio.Condition)
    reader_task: asyncio.Task[None] | None = None


@dataclass
class AgentPortForwardSession:
    request: PortForwardOpen
    target: ResolvedTcpTarget
    emit_event: PortForwardEventEmitter
    emit_data: PortForwardDataEmitter
    started_at: float = field(default_factory=time.monotonic)
    last_activity: float = field(default_factory=time.monotonic)
    connections: dict[int, AgentTcpConnection] = field(default_factory=dict)
    desktop_to_target_bytes: int = 0
    target_to_desktop_bytes: int = 0
    watcher: asyncio.Task[None] | None = None
    done: bool = False


class PortForwardController:
    """Own target TCP sockets while gateway/desktop own only relay and loopback state."""

    def __init__(
        self,
        *,
        resolver: TargetResolver | None = None,
        connector: TcpConnector | None = None,
    ) -> None:
        self.resolver = resolver or KubernetesTcpTargetResolver()
        self.connector = connector or asyncio.open_connection
        self.sessions: dict[str, AgentPortForwardSession] = {}

    async def handle_control(
        self,
        payload: object,
        emit_event: PortForwardEventEmitter,
        emit_data: PortForwardDataEmitter,
    ) -> bool:
        if not isinstance(payload, dict) or not str(payload.get("type") or "").startswith(
            "port_forward."
        ):
            return False
        try:
            message = parse_agent_port_forward_request(payload)
        except ValueError:
            await emit_event(
                PortForwardError(
                    code="protocol_violation",
                    message="Invalid port-forward control frame.",
                )
            )
            return True
        if isinstance(message, PortForwardOpen):
            await self._open(message, emit_event, emit_data)
        else:
            session = self.sessions.get(message.session_id)
            if session is None or message.generation != session.request.generation:
                return True
            if isinstance(message, PortForwardConnectionOpen):
                await self._open_connection(session, message.connection_id)
            elif isinstance(message, PortForwardWindow):
                await self._grant_output_credit(session, message)
            elif isinstance(message, PortForwardHalfClose):
                await self._half_close(session, message)
            elif isinstance(message, PortForwardConnectionClose):
                await self._finish_connection(session, message.connection_id, "closed")
            elif isinstance(message, PortForwardClose):
                await self._finish_session(session, "closed")
        return True

    async def handle_data(self, raw: bytes) -> bool:
        try:
            frame = decode_port_forward_data(raw)
        except ValueError:
            return False
        session = self.sessions.get(frame.session_id)
        if session is None or frame.generation != session.request.generation:
            return True
        connection = session.connections.get(frame.connection_id)
        if connection is None or connection.done:
            return True
        if frame.direction != "desktop_to_target":
            await self._fail_connection(session, connection, "protocol_violation")
            return True
        if frame.sequence != connection.expected_input_sequence:
            await self._fail_connection(session, connection, "protocol_violation")
            return True
        if len(frame.payload) > connection.input_credit:
            await self._fail_connection(session, connection, "credit_violation")
            return True
        next_total = connection.desktop_to_target_bytes + len(frame.payload)
        session_total = (
            session.desktop_to_target_bytes
            + sum(
                item.desktop_to_target_bytes
                for item in session.connections.values()
                if item is not connection
            )
            + next_total
        )
        if session_total > MAX_PORT_FORWARD_BYTES_PER_DIRECTION:
            await self._finish_connection(session, connection.connection_id, "byte_limit")
            return True
        connection.expected_input_sequence += 1
        connection.input_credit -= len(frame.payload)
        connection.writer.write(frame.payload)
        try:
            await connection.writer.drain()
        except Exception:
            await self._fail_connection(session, connection, "target_unavailable")
            return True
        connection.desktop_to_target_bytes = next_total
        connection.input_credit += len(frame.payload)
        session.last_activity = time.monotonic()
        await session.emit_event(
            PortForwardWindow(
                session_id=session.request.session_id,
                generation=session.request.generation,
                connection_id=connection.connection_id,
                direction="desktop_to_target",
                credit_bytes=len(frame.payload),
            )
        )
        return True

    async def close_all(self) -> None:
        for session in list(self.sessions.values()):
            await self._finish_session(session, "closed")

    async def _open(
        self,
        request: PortForwardOpen,
        emit_event: PortForwardEventEmitter,
        emit_data: PortForwardDataEmitter,
    ) -> None:
        existing = self.sessions.get(request.session_id)
        if existing is not None:
            await emit_event(self._error(request, "protocol_violation", "Session already exists."))
            return
        if len(self.sessions) >= MAX_PORT_FORWARD_SESSIONS_PER_AGENT:
            await emit_event(self._error(request, "session_limit", "Agent session limit reached."))
            return
        try:
            async with asyncio.timeout(MAX_PORT_FORWARD_OPEN_SECONDS):
                target = await self.resolver(request.resource, request.remote_port)
        except TimeoutError:
            await emit_event(self._error(request, "timeout", "Target validation timed out.", True))
            return
        except Exception:
            await emit_event(
                self._error(request, "invalid_target", "Target validation failed.", False)
            )
            return
        session = AgentPortForwardSession(
            request=request,
            target=target,
            emit_event=emit_event,
            emit_data=emit_data,
        )
        self.sessions[request.session_id] = session
        session.watcher = asyncio.create_task(self._watch(session))
        await emit_event(
            PortForwardOpened(
                session_id=request.session_id,
                generation=request.generation,
                target_kind=target.kind,
                target_name=target.name,
                target_uid=target.uid,
                target_port=target.port,
            )
        )

    async def _open_connection(
        self,
        session: AgentPortForwardSession,
        connection_id: int,
    ) -> None:
        if connection_id in session.connections:
            return
        if len(session.connections) >= MAX_PORT_FORWARD_CONNECTIONS_PER_SESSION:
            await session.emit_event(
                self._error(
                    session.request,
                    "session_limit",
                    "Port-forward connection limit reached.",
                )
            )
            return
        try:
            async with asyncio.timeout(MAX_PORT_FORWARD_OPEN_SECONDS):
                reader, writer = await self.connector(session.target.host, session.target.port)
        except Exception:
            await session.emit_event(
                self._error(
                    session.request,
                    "target_unavailable",
                    "Target TCP connection failed.",
                    True,
                )
            )
            return
        connection = AgentTcpConnection(
            connection_id=connection_id,
            reader=reader,
            writer=writer,
            input_credit=session.request.initial_credit_bytes,
            output_credit=session.request.initial_credit_bytes,
        )
        session.connections[connection_id] = connection
        session.last_activity = time.monotonic()
        connection.reader_task = asyncio.create_task(self._read_target(session, connection))
        await session.emit_event(
            PortForwardConnectionOpened(
                session_id=session.request.session_id,
                generation=session.request.generation,
                connection_id=connection_id,
            )
        )
        await session.emit_event(
            PortForwardWindow(
                session_id=session.request.session_id,
                generation=session.request.generation,
                connection_id=connection_id,
                direction="desktop_to_target",
                credit_bytes=session.request.initial_credit_bytes,
            )
        )

    async def _read_target(
        self,
        session: AgentPortForwardSession,
        connection: AgentTcpConnection,
    ) -> None:
        try:
            while not connection.done:
                allowance = await self._reserve_output_credit(connection)
                if allowance == 0:
                    return
                data = await connection.reader.read(min(allowance, MAX_PORT_FORWARD_FRAME_BYTES))
                if not data:
                    await self._restore_output_credit(connection, allowance)
                    connection.target_half_closed = True
                    await session.emit_event(
                        PortForwardHalfClose(
                            session_id=session.request.session_id,
                            generation=session.request.generation,
                            connection_id=connection.connection_id,
                            direction="target_to_desktop",
                        )
                    )
                    if connection.desktop_half_closed:
                        await self._finish_connection(
                            session, connection.connection_id, "target_closed"
                        )
                    return
                if len(data) < allowance:
                    await self._restore_output_credit(connection, allowance - len(data))
                next_total = connection.target_to_desktop_bytes + len(data)
                session_total = (
                    session.target_to_desktop_bytes
                    + sum(
                        item.target_to_desktop_bytes
                        for item in session.connections.values()
                        if item is not connection
                    )
                    + next_total
                )
                if session_total > MAX_PORT_FORWARD_BYTES_PER_DIRECTION:
                    await self._finish_connection(session, connection.connection_id, "byte_limit")
                    return
                frame = PortForwardDataFrame(
                    session_id=session.request.session_id,
                    generation=session.request.generation,
                    connection_id=connection.connection_id,
                    sequence=connection.output_sequence,
                    direction="target_to_desktop",
                    payload=data,
                )
                connection.output_sequence += 1
                connection.target_to_desktop_bytes = next_total
                session.last_activity = time.monotonic()
                await session.emit_data(encode_port_forward_data(frame))
        except asyncio.CancelledError:
            raise
        except Exception:
            await self._fail_connection(session, connection, "target_unavailable")

    async def _reserve_output_credit(self, connection: AgentTcpConnection) -> int:
        async with connection.output_credit_changed:
            while connection.output_credit == 0 and not connection.done:
                await connection.output_credit_changed.wait()
            if connection.done:
                return 0
            amount = min(connection.output_credit, MAX_PORT_FORWARD_FRAME_BYTES)
            connection.output_credit -= amount
            return amount

    async def _restore_output_credit(
        self,
        connection: AgentTcpConnection,
        amount: int,
    ) -> None:
        async with connection.output_credit_changed:
            connection.output_credit = min(
                PORT_FORWARD_CREDIT_WINDOW_BYTES,
                connection.output_credit + amount,
            )
            connection.output_credit_changed.notify_all()

    async def _grant_output_credit(
        self,
        session: AgentPortForwardSession,
        message: PortForwardWindow,
    ) -> None:
        connection = session.connections.get(message.connection_id)
        if connection is None or connection.done or message.direction != "target_to_desktop":
            return
        async with connection.output_credit_changed:
            if connection.output_credit + message.credit_bytes > PORT_FORWARD_CREDIT_WINDOW_BYTES:
                violation = True
            else:
                violation = False
                connection.output_credit += message.credit_bytes
                connection.output_credit_changed.notify_all()
        if violation:
            await self._fail_connection(session, connection, "credit_violation")

    async def _half_close(
        self,
        session: AgentPortForwardSession,
        message: PortForwardHalfClose,
    ) -> None:
        connection = session.connections.get(message.connection_id)
        if connection is None or connection.done:
            return
        if message.direction != "desktop_to_target":
            await self._fail_connection(session, connection, "protocol_violation")
            return
        connection.desktop_half_closed = True
        if connection.writer.can_write_eof():
            with suppress(Exception):
                connection.writer.write_eof()
                await connection.writer.drain()
        if connection.target_half_closed:
            await self._finish_connection(session, connection.connection_id, "target_closed")

    async def _watch(self, session: AgentPortForwardSession) -> None:
        try:
            while not session.done:
                await asyncio.sleep(1)
                now = time.monotonic()
                if now - session.started_at >= MAX_PORT_FORWARD_SESSION_SECONDS:
                    await self._finish_session(session, "session_timeout")
                    return
                if now - session.last_activity >= MAX_PORT_FORWARD_IDLE_SECONDS:
                    await self._finish_session(session, "idle_timeout")
                    return
        except asyncio.CancelledError:
            raise

    async def _fail_connection(
        self,
        session: AgentPortForwardSession,
        connection: AgentTcpConnection,
        code: str,
    ) -> None:
        await session.emit_event(
            self._error(
                session.request,
                code,
                "Port-forward connection failed.",
                code != "protocol_violation",
            )
        )
        await self._finish_connection(session, connection.connection_id, "closed")

    async def _finish_connection(
        self,
        session: AgentPortForwardSession,
        connection_id: int,
        reason: str,
    ) -> None:
        connection = session.connections.pop(connection_id, None)
        if connection is None or connection.done:
            return
        connection.done = True
        async with connection.output_credit_changed:
            connection.output_credit_changed.notify_all()
        current = asyncio.current_task()
        if connection.reader_task is not None and connection.reader_task is not current:
            connection.reader_task.cancel()
            with suppress(asyncio.CancelledError):
                await connection.reader_task
        connection.writer.close()
        with suppress(Exception):
            await connection.writer.wait_closed()
        session.desktop_to_target_bytes += connection.desktop_to_target_bytes
        session.target_to_desktop_bytes += connection.target_to_desktop_bytes
        await session.emit_event(
            PortForwardConnectionEnd(
                session_id=session.request.session_id,
                generation=session.request.generation,
                connection_id=connection.connection_id,
                reason=reason,
                desktop_to_target_bytes=connection.desktop_to_target_bytes,
                target_to_desktop_bytes=connection.target_to_desktop_bytes,
            )
        )

    async def _finish_session(self, session: AgentPortForwardSession, reason: str) -> None:
        if session.done:
            return
        session.done = True
        for connection_id in list(session.connections):
            await self._finish_connection(session, connection_id, reason)
        current = asyncio.current_task()
        if session.watcher is not None and session.watcher is not current:
            session.watcher.cancel()
            with suppress(asyncio.CancelledError):
                await session.watcher
        self.sessions.pop(session.request.session_id, None)
        await session.emit_event(
            PortForwardEnd(
                session_id=session.request.session_id,
                generation=session.request.generation,
                reason=reason,
                desktop_to_target_bytes=session.desktop_to_target_bytes,
                target_to_desktop_bytes=session.target_to_desktop_bytes,
            )
        )

    @staticmethod
    def _error(
        request: PortForwardOpen,
        code: str,
        message: str,
        retryable: bool = False,
    ) -> PortForwardError:
        return PortForwardError(
            session_id=request.session_id,
            generation=request.generation,
            code=code,
            message=message,
            retryable=retryable,
        )


def _resolve_pod(body: dict[str, Any], remote_port: int) -> ResolvedTcpTarget:
    metadata = body.get("metadata")
    spec = body.get("spec")
    status = body.get("status")
    if not isinstance(metadata, dict) or not isinstance(spec, dict) or not isinstance(status, dict):
        raise PortForwardTargetError("Pod target is incomplete")
    if str(status.get("phase") or "").casefold() != "running":
        raise PortForwardTargetError("Pod target is not running")
    declared = any(
        isinstance(port, dict)
        and port.get("containerPort") == remote_port
        and str(port.get("protocol") or "TCP").upper() == "TCP"
        for container in spec.get("containers", [])
        if isinstance(container, dict)
        for port in container.get("ports", [])
        if isinstance(container.get("ports"), list)
    )
    if not declared:
        raise PortForwardTargetError("Pod TCP port is no longer declared")
    host = str(status.get("podIP") or "")
    try:
        address = ipaddress.ip_address(host)
    except ValueError as exc:
        raise PortForwardTargetError("Pod address is unavailable") from exc
    if address.is_unspecified or address.is_loopback or address.is_multicast:
        raise PortForwardTargetError("Pod address is invalid")
    return ResolvedTcpTarget(
        kind="Pod",
        name=str(metadata.get("name") or ""),
        uid=str(metadata.get("uid") or ""),
        host=host,
        port=remote_port,
    )


def _resolve_service(body: dict[str, Any], remote_port: int) -> ResolvedTcpTarget:
    metadata = body.get("metadata")
    spec = body.get("spec")
    if not isinstance(metadata, dict) or not isinstance(spec, dict):
        raise PortForwardTargetError("Service target is incomplete")
    if str(spec.get("type") or "").casefold() == "externalname":
        raise PortForwardTargetError("ExternalName services are forbidden")
    declared = any(
        isinstance(port, dict)
        and port.get("port") == remote_port
        and str(port.get("protocol") or "TCP").upper() == "TCP"
        for port in spec.get("ports", [])
        if isinstance(spec.get("ports"), list)
    )
    if not declared:
        raise PortForwardTargetError("Service TCP port is no longer declared")
    name = str(metadata.get("name") or "")
    namespace = str(metadata.get("namespace") or "")
    return ResolvedTcpTarget(
        kind="Service",
        name=name,
        uid=str(metadata.get("uid") or ""),
        host=f"{name}.{namespace}.svc",
        port=remote_port,
    )
