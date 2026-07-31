"""Real, bounded Kubernetes pod exec over the agent's outbound WebSocket."""

from __future__ import annotations

import asyncio
import ssl
from collections.abc import Awaitable, Callable
from contextlib import AbstractAsyncContextManager, suppress
from dataclasses import dataclass, field
from pathlib import Path
from typing import Protocol
from urllib.parse import urlencode, urlsplit, urlunsplit

from commands.exec_transport import (
    STATUS_CHANNEL,
    STDERR_CHANNEL,
    STDIN_CHANNEL,
    STDOUT_CHANNEL,
    kubernetes_exec_connector,
    kubernetes_exit_code,
)
from kubernetes_api import kubernetes_api_base_url, service_account_token

from config import (
    KUBERNETES_SERVICEACCOUNT_CA_CERT_PATH,
    KUBERNETES_SERVICEACCOUNT_TOKEN_PATH,
)
from packages.config.terminal import pod_exec_namespace_allowed
from packages.contracts.terminal import (
    MAX_TERMINAL_INPUT_BYTES,
    MAX_TERMINAL_OUTPUT_BYTES,
    MAX_TERMINAL_OUTPUT_CHUNK_LENGTH,
    MAX_TERMINAL_SESSIONS_PER_AGENT,
    AgentTerminalRequest,
    TerminalClose,
    TerminalConnected,
    TerminalEnd,
    TerminalError,
    TerminalExec,
    TerminalInput,
    TerminalOutput,
    parse_agent_terminal_request,
)
from packages.security.log_lines import redact_log_line

TerminalEmitter = Callable[
    [TerminalConnected | TerminalOutput | TerminalEnd | TerminalError], Awaitable[None]
]


class ExecConnection(Protocol):
    async def recv(self) -> str | bytes: ...

    async def send(self, message: bytes) -> None: ...


ExecConnector = Callable[
    [str, dict[str, str], ssl.SSLContext],
    AbstractAsyncContextManager[ExecConnection],
]


@dataclass
class AgentExecSession:
    request: TerminalExec
    emit: TerminalEmitter
    stdin: asyncio.Queue[str | None] = field(default_factory=asyncio.Queue)
    input_bytes: int = 0
    task: asyncio.Task[None] | None = None


class PodExecController:
    """Owns active non-TTY pod exec sessions for one target agent."""

    def __init__(
        self,
        *,
        connector: ExecConnector | None = None,
        token_path: str = KUBERNETES_SERVICEACCOUNT_TOKEN_PATH,
        ca_cert_path: str = KUBERNETES_SERVICEACCOUNT_CA_CERT_PATH,
        base_url: str | None = None,
    ) -> None:
        self.connector = connector or kubernetes_exec_connector
        self.token_path = token_path
        self.ca_cert_path = ca_cert_path
        self.base_url = base_url
        self.sessions: dict[str, AgentExecSession] = {}

    async def handle(self, payload: object, emit: TerminalEmitter) -> bool:
        """Handle one terminal frame; return False for a non-terminal frame."""
        try:
            message: AgentTerminalRequest = parse_agent_terminal_request(payload)
        except ValueError:
            return False
        if isinstance(message, TerminalExec):
            await self._open(message, emit)
        elif isinstance(message, TerminalInput):
            await self._input(message)
        elif isinstance(message, TerminalClose):
            await self._close(message.session_id)
        return True

    async def close_all(self) -> None:
        tasks = [session.task for session in self.sessions.values() if session.task is not None]
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)

    async def _open(self, request: TerminalExec, emit: TerminalEmitter) -> None:
        if request.session_id in self.sessions:
            await emit(
                self._error(request.session_id, "exec_failed", "Terminal session already exists.")
            )
            return
        if len(self.sessions) >= MAX_TERMINAL_SESSIONS_PER_AGENT:
            await emit(
                self._error(
                    request.session_id, "session_limit", "Agent terminal session limit reached."
                )
            )
            return
        if not pod_exec_namespace_allowed(request.namespace):
            await emit(
                self._error(
                    request.session_id, "invalid_target", "Pod exec namespace is not allowed."
                )
            )
            return
        session = AgentExecSession(request=request, emit=emit)
        self.sessions[request.session_id] = session
        session.task = asyncio.create_task(self._run(session))

    async def _input(self, message: TerminalInput) -> None:
        session = self.sessions.get(message.session_id)
        if session is None:
            return
        encoded_length = len(message.data.encode("utf-8"))
        if session.input_bytes + encoded_length > MAX_TERMINAL_INPUT_BYTES:
            await self._close(message.session_id)
            return
        session.input_bytes += encoded_length
        await session.stdin.put(message.data)

    async def _close(self, session_id: str) -> None:
        session = self.sessions.get(session_id)
        if session is None or session.task is None:
            return
        session.task.cancel()

    async def _run(self, session: AgentExecSession) -> None:
        request = session.request
        stdout = StreamingOutputRedactor()
        stderr = StreamingOutputRedactor()
        output_bytes = 0
        writer: asyncio.Task[None] | None = None
        try:
            async with self.connector(
                self._exec_url(request),
                {"Authorization": f"Bearer {self._token()}"},
                self._ssl_context(),
            ) as connection:
                await session.emit(TerminalConnected(session_id=request.session_id))
                writer = asyncio.create_task(self._write_stdin(session, connection))
                async with asyncio.timeout(request.timeout_seconds):
                    exit_code: int | None = 0
                    while True:
                        try:
                            raw = await connection.recv()
                        except Exception as exc:
                            if _normal_websocket_close(exc):
                                break
                            raise
                        frame = raw.encode("utf-8") if isinstance(raw, str) else raw
                        if not frame:
                            continue
                        channel, data = frame[0], frame[1:]
                        if channel in {STDOUT_CHANNEL, STDERR_CHANNEL}:
                            redactor = stdout if channel == STDOUT_CHANNEL else stderr
                            stream = "stdout" if channel == STDOUT_CHANNEL else "stderr"
                            for chunk in redactor.feed(data.decode("utf-8", errors="replace")):
                                output_bytes = await self._emit_output(
                                    session, stream, chunk, output_bytes
                                )
                        elif channel == STATUS_CHANNEL:
                            exit_code = kubernetes_exit_code(data)
                            break
                for stream, redactor in (("stdout", stdout), ("stderr", stderr)):
                    for chunk in redactor.flush():
                        output_bytes = await self._emit_output(session, stream, chunk, output_bytes)
                await session.emit(
                    TerminalEnd(
                        session_id=request.session_id,
                        exit_code=exit_code,
                        reason="completed",
                    )
                )
        except TimeoutError:
            await session.emit(
                TerminalEnd(session_id=request.session_id, exit_code=None, reason="timeout")
            )
        except asyncio.CancelledError:
            await session.emit(
                TerminalEnd(session_id=request.session_id, exit_code=None, reason="closed")
            )
        except OutputLimitReached:
            await session.emit(
                TerminalEnd(session_id=request.session_id, exit_code=None, reason="output_limit")
            )
        except Exception:
            await session.emit(
                self._error(request.session_id, "exec_failed", "Kubernetes pod exec failed.", True)
            )
        finally:
            if writer is not None:
                writer.cancel()
                with suppress(asyncio.CancelledError):
                    await writer
            self.sessions.pop(request.session_id, None)

    async def _write_stdin(
        self,
        session: AgentExecSession,
        connection: ExecConnection,
    ) -> None:
        while True:
            value = await session.stdin.get()
            if value is None:
                return
            await connection.send(bytes([STDIN_CHANNEL]) + value.encode("utf-8"))

    async def _emit_output(
        self,
        session: AgentExecSession,
        stream: str,
        chunk: str,
        output_bytes: int,
    ) -> int:
        next_size = output_bytes + len(chunk.encode("utf-8"))
        if next_size > MAX_TERMINAL_OUTPUT_BYTES:
            raise OutputLimitReached
        await session.emit(
            TerminalOutput(
                session_id=session.request.session_id,
                stream=stream,
                data=chunk,
            )
        )
        return next_size

    def _exec_url(self, request: TerminalExec) -> str:
        base_url = (self.base_url or kubernetes_api_base_url() or "").rstrip("/")
        if not base_url:
            raise RuntimeError("Kubernetes API is not configured")
        parsed = urlsplit(base_url)
        scheme = "wss" if parsed.scheme == "https" else "ws"
        path = f"/api/v1/namespaces/{request.namespace}/pods/{request.pod}/exec"
        query = urlencode(
            [
                ("container", request.container),
                ("command", "/bin/sh"),
                ("command", "-lc"),
                ("command", request.command),
                ("stdin", "true"),
                ("stdout", "true"),
                ("stderr", "true"),
                ("tty", "false"),
            ]
        )
        return urlunsplit((scheme, parsed.netloc, path, query, ""))

    def _token(self) -> str:
        if self.token_path == KUBERNETES_SERVICEACCOUNT_TOKEN_PATH:
            token = service_account_token()
        else:
            token = Path(self.token_path).read_text(encoding="utf-8").strip()
        if not token:
            raise RuntimeError("Kubernetes service account token is unavailable")
        return token

    def _ssl_context(self) -> ssl.SSLContext:
        return ssl.create_default_context(cafile=self.ca_cert_path)

    @staticmethod
    def _error(
        session_id: str,
        code: str,
        message: str,
        retryable: bool = False,
    ) -> TerminalError:
        return TerminalError(
            session_id=session_id,
            code=code,
            message=message,
            retryable=retryable,
        )


class OutputLimitReached(RuntimeError):
    pass


class StreamingOutputRedactor:
    """Redact complete lines before streaming; never split a sensitive value across frames."""

    def __init__(self) -> None:
        self.buffer = ""

    def feed(self, value: str) -> list[str]:
        self.buffer += value
        chunks: list[str] = []
        while "\n" in self.buffer:
            line, self.buffer = self.buffer.split("\n", 1)
            chunks.extend(_bounded_chunks(redact_log_line(line) + "\n"))
        if len(self.buffer) > MAX_TERMINAL_OUTPUT_CHUNK_LENGTH:
            chunks.extend(_bounded_chunks(redact_log_line(self.buffer)))
            self.buffer = ""
        return chunks

    def flush(self) -> list[str]:
        if not self.buffer:
            return []
        value = self.buffer
        self.buffer = ""
        return _bounded_chunks(redact_log_line(value))


def _bounded_chunks(value: str) -> list[str]:
    return [
        value[index : index + MAX_TERMINAL_OUTPUT_CHUNK_LENGTH]
        for index in range(0, len(value), MAX_TERMINAL_OUTPUT_CHUNK_LENGTH)
        if value[index : index + MAX_TERMINAL_OUTPUT_CHUNK_LENGTH]
    ]


def _normal_websocket_close(exc: Exception) -> bool:
    return type(exc).__name__ == "ConnectionClosedOK" or getattr(exc, "code", None) == 1000
