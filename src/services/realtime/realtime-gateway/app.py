"""realtime-gateway — cluster-agent live stream 을 browser 로 fan-out 하는 실시간 게이트웨이.

경로: cluster-agent --(outbound WS /live/agent)--> hub cache --(WS /live/browser)--> browser.
node-collector 는 여기 연결하지 않음 — /metrics 는 Prometheus scrape 경로를 유지함.
agent 인증은 api-gateway 와 동일한 per-cluster 토큰(x-agent-token 해시 → DB 조회)을 재사용함.
"""

from __future__ import annotations

import asyncio
import inspect
import json
import time
from collections.abc import Awaitable, Callable
from contextlib import asynccontextmanager, suppress
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from agent_connections import AgentConnectionRegistry
from fastapi import FastAPI, WebSocket, WebSocketDisconnect
from fastapi.responses import PlainTextResponse
from hub import BrowserClient, RealtimeHub, RealtimeSnapshotLimitError
from port_forward_sessions import (
    PortForwardAuditor,
    PortForwardAuthorizer,
    PortForwardSessionBroker,
    database_port_forward_auditor,
    database_port_forward_authorizer,
    port_forward_capability_revision,
    port_forward_local_availability,
    port_forward_resource_ports,
    port_forward_scope_and_resource,
)
from terminal_sessions import (
    TerminalAuditor,
    TerminalAuthorizer,
    TerminalSessionBroker,
    database_terminal_auditor,
    database_terminal_authorizer,
)

from domains.identity.dependencies import (
    AGENT_TOKEN_HEADER,
    hash_agent_token,
)
from packages.config.constants import Auth
from packages.config.constants import Redis as RedisConfig
from packages.config.logs import CONTEXT_KEY, get_logger
from packages.config.realtime import realtime_gateway_limits
from packages.config.settings import env
from packages.contracts.gateway.fields import Gateway
from packages.contracts.identity import (
    DEFAULT_WORKSPACE_ID,
    AccessResourceType,
    Permission,
    ServiceRole,
)
from packages.contracts.port_forward import BROWSER_PORT_FORWARD_PATH, PortForwardStart
from packages.contracts.realtime import (
    AGENT_LIVE_PATH,
    BROWSER_LIVE_PATH,
    HelloMessage,
    LiveSummaryMessage,
    PingMessage,
    RealtimeIngressLimits,
    RealtimeLimitError,
    ResourceDelta,
    ResyncRequiredMessage,
    Subscription,
    delta_key_parts,
    parse_realtime_message,
    serialized_json_bytes,
)
from packages.contracts.terminal import BROWSER_TERMINAL_PATH
from packages.runtime.service import FastApiService
from packages.security.trusted_proxy import (
    assert_trusted_proxy_config_safe,
    trusted_proxy_identity,
)
from packages.storage.database import Database, wait_for_database
from packages.storage.sessions import RedisSessionStore, RedisSessionStoreConfig

__all__ = [
    "PortForwardStart",
    "port_forward_capability_revision",
    "port_forward_local_availability",
    "port_forward_resource_ports",
    "port_forward_scope_and_resource",
]

LOGGER = get_logger(__name__)

GATEWAY_NAME = "realtime-gateway"

# browser 로 보낼 것이 없을 때 keepalive ping 주기.
BROWSER_PING_INTERVAL_SECONDS = 15.0

# WebSocket close code — 1008(policy violation)은 표준, 44xx 는 애플리케이션 정의.
CLOSE_BAD_REQUEST = 4400
CLOSE_UNAUTHORIZED = 4401
CLOSE_PROTOCOL_VIOLATION = 1008
CLOSE_TRY_AGAIN_LATER = 1013

# authenticate: 원문 토큰 → {"workspace_id", "cluster_id"} | None (fail-closed)
AgentAuthenticator = Callable[[str], Any]
BrowserSessionAuthenticator = Callable[[str | None], Awaitable[Any]]
BrowserClusterAuthorizer = Callable[[Any, str, str], Awaitable[bool]]
LiveUsagePersister = Callable[[str, str, datetime, dict[str, Any]], Any]


@dataclass
class AgentIngressBudget:
    """Per-agent rolling ingress budget; breach closes only that producer connection."""

    limits: RealtimeIngressLimits
    window_started_at: float = field(default_factory=time.monotonic)
    message_count: int = 0
    byte_count: int = 0

    def consume(self, payload: object, *, now: float | None = None) -> None:
        self._consume_size(serialized_json_bytes(payload), now=now)

    def consume_binary(self, payload: bytes, *, now: float | None = None) -> None:
        self._consume_size(len(payload), now=now)

    def _consume_size(self, payload_bytes: int, *, now: float | None = None) -> None:
        observed_at = time.monotonic() if now is None else now
        if observed_at - self.window_started_at >= self.limits.agent_ingress_window_seconds:
            self.window_started_at = observed_at
            self.message_count = 0
            self.byte_count = 0
        if payload_bytes > self.limits.agent_message_max_bytes:
            raise RealtimeLimitError("agent_message_too_large")
        if self.message_count + 1 > self.limits.agent_messages_per_window:
            raise RealtimeLimitError("agent_message_rate_exceeded")
        if self.byte_count + payload_bytes > self.limits.agent_bytes_per_window:
            raise RealtimeLimitError("agent_byte_rate_exceeded")
        self.message_count += 1
        self.byte_count += payload_bytes


REDIS_URL_ENV = "REDIS_URL"
SESSION_KEY_PREFIX = "session"
RATE_LIMIT_KEY_PREFIX = "rate"
EMAIL_VERIFICATION_KEY_PREFIX = "email_verify"
SESSION_TOKEN_BYTES = 32
EMAIL_VERIFICATION_TOKEN_BYTES = 32
DEFAULT_RATE_LIMIT = 120
RATE_LIMIT_WINDOW_SECONDS = 60
EMAIL_VERIFICATION_TTL_SECONDS = 60 * 60
SESSION_TOKEN_HEADER = "x-session-token"
AUTHORIZATION_HEADER = "authorization"
BEARER_PREFIX = "bearer "


def database_authenticator(db: Database) -> AgentAuthenticator:
    """api-gateway 의 require_cluster_agent 와 동일한 인증 경로(해시 조회) 재사용."""

    def authenticate(token: str) -> Any:
        if not token:
            return None
        return db.authenticate_cluster_agent(hash_agent_token(token))

    return authenticate


def session_store_config() -> RedisSessionStoreConfig:
    return RedisSessionStoreConfig(
        url=env(REDIS_URL_ENV, RedisConfig.DEFAULT_URL),
        ttl_seconds=int(env(Auth.SESSION_TTL_ENV, Auth.DEFAULT_SESSION_TTL_SECONDS)),
        key_prefix=SESSION_KEY_PREFIX,
        token_bytes=SESSION_TOKEN_BYTES,
        default_roles=(ServiceRole.USER.value,),
        default_workspace_id=DEFAULT_WORKSPACE_ID,
        rate_limit_key_prefix=RATE_LIMIT_KEY_PREFIX,
        rate_limit=DEFAULT_RATE_LIMIT,
        rate_limit_window_seconds=RATE_LIMIT_WINDOW_SECONDS,
        email_verification_key_prefix=EMAIL_VERIFICATION_KEY_PREFIX,
        email_verification_ttl_seconds=EMAIL_VERIFICATION_TTL_SECONDS,
        email_verification_token_bytes=EMAIL_VERIFICATION_TOKEN_BYTES,
    )


def redis_session_authenticator(session_store: RedisSessionStore) -> BrowserSessionAuthenticator:
    async def authenticate(token: str | None) -> Any:
        return await session_store.get_session(token)

    return authenticate


def database_browser_cluster_authorizer(db: Database) -> BrowserClusterAuthorizer:
    """세션 사용자의 workspace-scoped cluster.read 권한을 fail-closed로 확인한다."""

    async def authorize(session: Any, workspace_id: str, cluster_id: str) -> bool:
        user_id = session_user_id(session)
        if not user_id or not workspace_id or not cluster_id:
            return False
        try:
            registration = await asyncio.to_thread(
                db.get_cluster_registration, workspace_id, cluster_id
            )
            if registration is None:
                return False
            if ServiceRole.SERVICE_ADMIN.value in session_roles(session):
                return True
            return bool(
                await asyncio.to_thread(
                    db.can_access,
                    user_id,
                    workspace_id,
                    AccessResourceType.CLUSTER.value,
                    cluster_id,
                    Permission.CLUSTER_READ.value,
                )
            )
        except Exception as exc:
            LOGGER.warning(
                "browser_cluster_authorization_failed",
                extra={
                    CONTEXT_KEY: {
                        Gateway.WORKSPACE_ID: workspace_id,
                        Gateway.CLUSTER_ID: cluster_id,
                        "exception_type": type(exc).__name__,
                    }
                },
            )
            return False

    return authorize


def create_app(
    db: Database | None = None,
    authenticate_agent: AgentAuthenticator | None = None,
    authenticate_browser: BrowserSessionAuthenticator | None = None,
    authorize_browser_cluster: BrowserClusterAuthorizer | None = None,
    authorize_browser_terminal: TerminalAuthorizer | None = None,
    audit_terminal: TerminalAuditor | None = None,
    authorize_browser_port_forward: PortForwardAuthorizer | None = None,
    audit_port_forward: PortForwardAuditor | None = None,
    persist_live_usage: LiveUsagePersister | None = None,
    realtime_limits: RealtimeIngressLimits | None = None,
) -> FastAPI:
    if db is None and (authenticate_agent is None or authorize_browser_cluster is None):
        db = Database()
    if authenticate_agent is None:
        db = db or Database()
        authenticate_agent = database_authenticator(db)
    browser_session_store: RedisSessionStore | None = None
    if authenticate_browser is None:
        browser_session_store = RedisSessionStore(session_store_config())
        authenticate_browser = redis_session_authenticator(browser_session_store)
    if authorize_browser_cluster is None:
        if db is None:  # pragma: no cover - create_app 위의 DB 생성 불변식 방어
            raise RuntimeError("database is required for browser cluster authorization")
        authorize_browser_cluster = database_browser_cluster_authorizer(db)
    if authorize_browser_terminal is None:
        authorize_browser_terminal = (
            database_terminal_authorizer(db) if db is not None else _deny_terminal
        )
    if audit_terminal is None:
        audit_terminal = database_terminal_auditor(db) if db is not None else _fail_terminal_audit
    if authorize_browser_port_forward is None:
        authorize_browser_port_forward = (
            database_port_forward_authorizer(db) if db is not None else _deny_port_forward
        )
    if audit_port_forward is None:
        audit_port_forward = (
            database_port_forward_auditor(db) if db is not None else _fail_port_forward_audit
        )
    if persist_live_usage is None and db is not None:
        save_live_usage = getattr(db, "save_live_cluster_usage_sample", None)
        if callable(save_live_usage):

            async def persist_live_usage(
                workspace_id: str,
                cluster_id: str,
                sampled_at: datetime,
                usage: dict[str, Any],
            ) -> Any:
                return await asyncio.to_thread(
                    save_live_usage,
                    workspace_id=workspace_id,
                    cluster_id=cluster_id,
                    sampled_at=sampled_at,
                    usage=usage,
                )

    limits = realtime_limits or realtime_gateway_limits()
    hub = RealtimeHub(limits=limits)
    agent_connections = AgentConnectionRegistry()
    terminal_broker = TerminalSessionBroker(
        authorize=authorize_browser_terminal,
        audit=audit_terminal,
        connections=agent_connections,
    )
    port_forward_broker = PortForwardSessionBroker(
        authorize=authorize_browser_port_forward,
        audit=audit_port_forward,
        connections=agent_connections,
    )

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        assert_trusted_proxy_config_safe()
        if db is not None:
            await wait_for_database(db)
        if browser_session_store is not None:
            await browser_session_store.connect()
        try:
            yield
        finally:
            if browser_session_store is not None:
                await browser_session_store.close()

    app = FastAPI(title=GATEWAY_NAME, lifespan=lifespan)
    app.state.hub = hub
    app.state.agent_connections = agent_connections
    app.state.terminal_broker = terminal_broker
    app.state.port_forward_broker = port_forward_broker

    @app.get("/healthz", response_class=PlainTextResponse)
    async def healthz() -> str:
        return "ok"

    @app.get("/readyz", response_class=PlainTextResponse)
    async def readyz() -> str:
        return "ok"

    @app.websocket(AGENT_LIVE_PATH)
    async def agent_live(websocket: WebSocket) -> None:
        await websocket.accept()
        token = websocket.headers.get(AGENT_TOKEN_HEADER, "")
        identity = authenticate_agent(token)
        requested_cluster = websocket.query_params.get(Gateway.CLUSTER_ID, "")
        if identity is None:
            await websocket.close(code=CLOSE_UNAUTHORIZED)
            return
        cluster_id = str(identity[Gateway.CLUSTER_ID])
        requested_cluster = requested_cluster or cluster_id
        if requested_cluster != cluster_id:
            # 토큰의 클러스터가 권위 — 다른 클러스터로의 발행 시도는 차단(크로스 테넌트 금지).
            await websocket.close(code=CLOSE_UNAUTHORIZED)
            return

        agent_connection, previous_connection = agent_connections.register(cluster_id, websocket)
        if previous_connection is not None:
            await terminal_broker.agent_disconnected(cluster_id, previous_connection)
            await port_forward_broker.agent_disconnected(cluster_id, previous_connection)
            with suppress(Exception):
                await previous_connection.websocket.close(code=1012)
        ingress_budget = AgentIngressBudget(limits)
        await agent_connection.send_json(HelloMessage())
        LOGGER.info("agent_stream_connected", extra={CONTEXT_KEY: {Gateway.CLUSTER_ID: cluster_id}})
        try:
            while True:
                incoming = await websocket.receive()
                if incoming["type"] == "websocket.disconnect":
                    raise WebSocketDisconnect(int(incoming.get("code") or 1000))
                try:
                    binary = incoming.get("bytes")
                    if binary is not None:
                        payload_bytes = bytes(binary)
                        ingress_budget.consume_binary(payload_bytes)
                        await port_forward_broker.handle_agent_binary(
                            cluster_id, agent_connection, payload_bytes
                        )
                        continue
                    payload = json.loads(str(incoming.get("text") or ""))
                    ingress_budget.consume(payload)
                except (json.JSONDecodeError, RealtimeLimitError, ValueError):
                    LOGGER.warning(
                        "agent_ingress_limit_exceeded",
                        extra={CONTEXT_KEY: {Gateway.CLUSTER_ID: cluster_id}},
                    )
                    await websocket.close(code=CLOSE_PROTOCOL_VIOLATION)
                    return
                terminal_result = await terminal_broker.handle_agent_payload(
                    cluster_id, agent_connection, payload
                )
                if terminal_result is True:
                    continue
                port_forward_result = await port_forward_broker.handle_agent_payload(
                    cluster_id, agent_connection, payload
                )
                if port_forward_result is True:
                    continue
                message = (
                    _ingest(hub, cluster_id, payload, limits=limits)
                    if terminal_result is not False and port_forward_result is not False
                    else None
                )
                if message is None:
                    await websocket.close(code=CLOSE_PROTOCOL_VIOLATION)
                    return
                if isinstance(message, LiveSummaryMessage) and persist_live_usage is not None:
                    usage = live_usage_payload(
                        message.summary.model_dump(mode="json"),
                        hub.resources_for_cluster(cluster_id),
                    )
                    if usage.get("pods"):
                        try:
                            saved = persist_live_usage(
                                str(identity[Gateway.WORKSPACE_ID]),
                                cluster_id,
                                datetime.now(UTC),
                                usage,
                            )
                            if inspect.isawaitable(saved):
                                await saved
                        except Exception as exc:
                            # 실시간 fan-out은 저장소 일시 장애와 독립적으로 계속 제공한다.
                            LOGGER.warning(
                                "live_usage_persist_failed",
                                extra={
                                    CONTEXT_KEY: {
                                        Gateway.CLUSTER_ID: cluster_id,
                                        "exception_type": type(exc).__name__,
                                    }
                                },
                            )
        except WebSocketDisconnect:
            LOGGER.info(
                "agent_stream_disconnected", extra={CONTEXT_KEY: {Gateway.CLUSTER_ID: cluster_id}}
            )
        finally:
            if agent_connections.unregister(cluster_id, agent_connection):
                await terminal_broker.agent_disconnected(cluster_id, agent_connection)
                await port_forward_broker.agent_disconnected(cluster_id, agent_connection)

    @app.websocket(BROWSER_LIVE_PATH)
    async def browser_live(websocket: WebSocket) -> None:
        await websocket.accept()
        # query param 이름은 Subscription 계약 필드가 단일 출처(별도 리터럴 금지).
        params = {name: websocket.query_params.get(name, "") for name in Subscription.model_fields}
        if not params[Gateway.WORKSPACE_ID] or not params[Gateway.CLUSTER_ID]:
            await websocket.close(code=CLOSE_BAD_REQUEST)
            return
        session = await authenticated_browser_session(websocket, authenticate_browser)
        session_workspace = session_workspace_id(session)
        if not session_workspace or params[Gateway.WORKSPACE_ID] != session_workspace:
            await websocket.close(code=CLOSE_UNAUTHORIZED)
            return
        try:
            cluster_allowed = await authorize_browser_cluster(
                session,
                session_workspace,
                params[Gateway.CLUSTER_ID],
            )
        except Exception as exc:
            LOGGER.warning(
                "browser_cluster_authorization_failed",
                extra={
                    CONTEXT_KEY: {
                        Gateway.WORKSPACE_ID: session_workspace,
                        Gateway.CLUSTER_ID: params[Gateway.CLUSTER_ID],
                        "exception_type": type(exc).__name__,
                    }
                },
            )
            cluster_allowed = False
        if not cluster_allowed:
            await websocket.close(code=CLOSE_UNAUTHORIZED)
            return
        subscription = Subscription(**params)
        client = hub.register_browser(subscription)
        sender = asyncio.create_task(_browser_send_loop(websocket, hub, client))
        try:
            # 수신 내용은 쓰지 않지만, receive 가 disconnect 감지의 유일한 방법임.
            while True:
                await websocket.receive_text()
        except WebSocketDisconnect:
            pass
        finally:
            hub.unregister_browser(client)
            sender.cancel()
            with suppress(asyncio.CancelledError):
                await sender

    @app.websocket(BROWSER_TERMINAL_PATH)
    async def browser_terminal(websocket: WebSocket) -> None:
        await websocket.accept()
        session = await authenticated_browser_session(websocket, authenticate_browser)
        if session is None:
            await websocket.close(code=CLOSE_UNAUTHORIZED)
            return
        await terminal_broker.serve_browser(websocket, session)

    @app.websocket(BROWSER_PORT_FORWARD_PATH)
    async def browser_port_forward(websocket: WebSocket) -> None:
        await websocket.accept()
        session = await authenticated_browser_session(websocket, authenticate_browser)
        if session is None:
            await websocket.close(code=CLOSE_UNAUTHORIZED)
            return
        await port_forward_broker.serve_browser(websocket, session)

    return app


async def _deny_terminal(*_args: object) -> bool:
    return False


async def _fail_terminal_audit(*_args: object) -> None:
    raise RuntimeError("terminal audit store is unavailable")


async def _deny_port_forward(*_args: object) -> bool:
    return False


async def _fail_port_forward_audit(*_args: object) -> None:
    raise RuntimeError("port-forward audit store is unavailable")


async def authenticated_browser_session(
    websocket: WebSocket,
    authenticate: BrowserSessionAuthenticator,
) -> Any:
    for token in browser_session_tokens(websocket):
        session = await authenticate(token)
        if session is not None:
            return session
    proxy_identity = trusted_proxy_identity(websocket.headers)
    if proxy_identity is not None:
        return {
            Gateway.WORKSPACE_ID: proxy_identity.workspace_id,
            "user_id": proxy_identity.user_id,
            "roles": [ServiceRole.SERVICE_ADMIN.value],
        }
    return None


def browser_session_token(websocket: WebSocket) -> str | None:
    tokens = browser_session_tokens(websocket)
    return tokens[0] if tokens else None


def browser_session_tokens(websocket: WebSocket) -> tuple[str, ...]:
    candidates: list[str] = []
    authorization = websocket.headers.get(AUTHORIZATION_HEADER, "")
    if authorization.lower().startswith(BEARER_PREFIX):
        candidates.append(authorization.split(" ", 1)[1].strip())
    if websocket.headers.get(SESSION_TOKEN_HEADER):
        candidates.append(websocket.headers[SESSION_TOKEN_HEADER])
    if websocket.cookies.get(Auth.SESSION_COOKIE_NAME):
        candidates.append(websocket.cookies[Auth.SESSION_COOKIE_NAME])
    return tuple(dict.fromkeys(token for token in candidates if token))


def session_workspace_id(session: Any) -> str:
    if session is None:
        return ""
    if isinstance(session, dict):
        return str(session.get(Gateway.WORKSPACE_ID, ""))
    return str(getattr(session, Gateway.WORKSPACE_ID, ""))


def session_user_id(session: Any) -> str:
    if session is None:
        return ""
    if isinstance(session, dict):
        return str(session.get("user_id", ""))
    return str(getattr(session, "user_id", ""))


def session_roles(session: Any) -> set[str]:
    if session is None:
        return set()
    raw_roles = (
        session.get("roles", []) if isinstance(session, dict) else getattr(session, "roles", [])
    )
    if not isinstance(raw_roles, list | tuple | set):
        return set()
    return {str(role) for role in raw_roles}


def _ingest(
    hub: RealtimeHub,
    cluster_id: str,
    payload: Any,
    *,
    limits: RealtimeIngressLimits | None = None,
) -> Any | None:
    """agent 수신 1건 처리. 계약 위반/권한 밖 클러스터는 None(연결 종료)."""
    try:
        message = parse_realtime_message(payload, limits=limits)
        if isinstance(message, PingMessage):
            return message
        if isinstance(message, LiveSummaryMessage):
            if message.cluster_id != cluster_id or message.summary.cluster_id != cluster_id:
                return None
            hub.publish_summary(message.summary)
            return message
        if isinstance(message, ResourceDelta):
            if delta_key_parts(message.key)[0] != cluster_id:
                return None
            hub.publish_delta(message)
            return message
    except (RealtimeLimitError, ValueError):
        LOGGER.warning(
            "agent_message_invalid", extra={CONTEXT_KEY: {Gateway.CLUSTER_ID: cluster_id}}
        )
        return None
    return None  # hello/snapshot 은 gateway → client 방향 전용


def live_usage_payload(
    summary: dict[str, Any],
    resources: dict[str, dict[str, Any]],
) -> dict[str, Any]:
    """Build a replay/evaluation sample exclusively from agent-observed live resource deltas."""
    pods: dict[str, dict[str, Any]] = {}
    phases: dict[str, int] = {}
    restart_total = 0
    for key, value in resources.items():
        _cluster, namespace, kind, name = delta_key_parts(key)
        if kind.casefold() != "pod" or not namespace or not name:
            continue
        phase = str(value.get("phase") or "Unknown")
        phases[phase] = phases.get(phase, 0) + 1
        restarts = max(0, int(value.get("restarts") or 0))
        restart_total += restarts
        measured = {
            field: value.get(field)
            for field in (
                "cpu_mcores",
                "cpu_request_mcores",
                "cpu_request_pct",
                "mem_mib",
                "mem_request_mib",
                "mem_request_pct",
                "ready",
                "phase",
                "restarts",
                "node",
            )
            if value.get(field) is not None
        }
        if measured:
            pods[f"{namespace}/{name}"] = measured
    metadata = summary.get("metrics_metadata")
    usage: dict[str, Any] = {
        "pod_total": int(summary.get("pods_total") or len(pods)),
        "pod_running": phases.get("Running", 0),
        "pod_pending": phases.get("Pending", 0),
        "pod_failed": phases.get("Failed", 0),
        "restart_total": restart_total,
        "pods": pods,
    }
    if isinstance(metadata, dict):
        usage["metrics_metadata"] = dict(metadata)
    return usage


async def _browser_send_loop(websocket: WebSocket, hub: RealtimeHub, client: BrowserClient) -> None:
    """접속 인사(hello + snapshot) 후 queue 를 소비. 한가하면 ping 으로 keepalive."""
    await websocket.send_json(HelloMessage().model_dump(mode="json"))
    try:
        snapshot = hub.snapshot_for(client.subscription)
    except RealtimeSnapshotLimitError:
        await websocket.send_json(ResyncRequiredMessage().model_dump(mode="json"))
        await websocket.close(code=CLOSE_TRY_AGAIN_LATER)
        return
    await websocket.send_json(snapshot.model_dump(mode="json"))
    while True:
        try:
            message = await asyncio.wait_for(
                client.queue.get(), timeout=BROWSER_PING_INTERVAL_SECONDS
            )
        except TimeoutError:
            await websocket.send_json(PingMessage(ts=time.time()).model_dump(mode="json"))
            continue
        await websocket.send_json(message.model_dump(mode="json"))
        if isinstance(message, ResyncRequiredMessage):
            await websocket.close(code=CLOSE_TRY_AGAIN_LATER)
            return


def main() -> None:
    FastApiService(GATEWAY_NAME, create_app).run()


if __name__ == "__main__":
    main()
