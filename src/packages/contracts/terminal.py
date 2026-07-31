"""Strict bidirectional contract for audited Kubernetes pod exec sessions."""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter

from packages.contracts.gateway.base import StrictModel

TERMINAL_PROTOCOL = "terminal.v1"
BROWSER_TERMINAL_PATH = "/live/terminal"
POD_EXEC_AGENT_CAPABILITY = "pod_exec_stream"

MAX_TERMINAL_COMMAND_LENGTH = 1_024
MAX_TERMINAL_INPUT_LENGTH = 4_096
MAX_TERMINAL_INPUT_BYTES = 64 * 1_024
MAX_TERMINAL_OUTPUT_CHUNK_LENGTH = 8_192
MAX_TERMINAL_OUTPUT_BYTES = 256 * 1_024
MAX_TERMINAL_SESSION_SECONDS = 300
MAX_TERMINAL_SESSIONS_PER_USER = 3
MAX_TERMINAL_SESSIONS_PER_AGENT = 4

TerminalStream = Literal["stdout", "stderr"]
TerminalEndReason = Literal["completed", "closed", "timeout", "output_limit"]
TerminalErrorCode = Literal[
    "agent_unavailable",
    "audit_unavailable",
    "exec_failed",
    "invalid_target",
    "output_limit",
    "session_limit",
    "timeout",
]


class TerminalStart(StrictModel):
    """Browser's first frame. Target identity stays in the authorized URL."""

    type: Literal["terminal.start"] = "terminal.start"
    command: str = Field(min_length=1, max_length=MAX_TERMINAL_COMMAND_LENGTH)


class TerminalInput(StrictModel):
    type: Literal["terminal.input"] = "terminal.input"
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    data: str = Field(min_length=1, max_length=MAX_TERMINAL_INPUT_LENGTH)


class TerminalClose(StrictModel):
    type: Literal["terminal.close"] = "terminal.close"
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class TerminalExec(StrictModel):
    """Gateway-to-agent execution request after server-side authorization."""

    type: Literal["terminal.exec"] = "terminal.exec"
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    namespace: str = Field(min_length=1, max_length=253)
    pod: str = Field(min_length=1, max_length=253)
    container: str = Field(min_length=1, max_length=253)
    command: str = Field(min_length=1, max_length=MAX_TERMINAL_COMMAND_LENGTH)
    timeout_seconds: int = Field(
        default=MAX_TERMINAL_SESSION_SECONDS, ge=1, le=MAX_TERMINAL_SESSION_SECONDS
    )
    tty: Literal[False] = False


class TerminalConnected(StrictModel):
    type: Literal["terminal.connected"] = "terminal.connected"
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")


class TerminalOutput(StrictModel):
    type: Literal["terminal.output"] = "terminal.output"
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    stream: TerminalStream
    data: str = Field(min_length=1, max_length=MAX_TERMINAL_OUTPUT_CHUNK_LENGTH)


class TerminalEnd(StrictModel):
    type: Literal["terminal.end"] = "terminal.end"
    session_id: str = Field(min_length=1, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    exit_code: int | None = None
    reason: TerminalEndReason


class TerminalError(StrictModel):
    type: Literal["terminal.error"] = "terminal.error"
    session_id: str | None = Field(default=None, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    code: TerminalErrorCode
    message: str = Field(min_length=1, max_length=240)
    retryable: bool = False


BrowserTerminalRequest = Annotated[
    TerminalStart | TerminalInput | TerminalClose,
    Field(discriminator="type"),
]
AgentTerminalRequest = Annotated[
    TerminalExec | TerminalInput | TerminalClose,
    Field(discriminator="type"),
]
AgentTerminalEvent = Annotated[
    TerminalConnected | TerminalOutput | TerminalEnd | TerminalError,
    Field(discriminator="type"),
]
BrowserTerminalEvent = Annotated[
    TerminalConnected | TerminalOutput | TerminalEnd | TerminalError,
    Field(discriminator="type"),
]

BrowserTerminalRequestAdapter: TypeAdapter[BrowserTerminalRequest] = TypeAdapter(
    BrowserTerminalRequest
)
AgentTerminalRequestAdapter: TypeAdapter[AgentTerminalRequest] = TypeAdapter(AgentTerminalRequest)
AgentTerminalEventAdapter: TypeAdapter[AgentTerminalEvent] = TypeAdapter(AgentTerminalEvent)
BrowserTerminalEventAdapter: TypeAdapter[BrowserTerminalEvent] = TypeAdapter(BrowserTerminalEvent)


def parse_browser_terminal_request(payload: Any) -> BrowserTerminalRequest:
    return BrowserTerminalRequestAdapter.validate_python(payload)


def parse_agent_terminal_request(payload: Any) -> AgentTerminalRequest:
    return AgentTerminalRequestAdapter.validate_python(payload)


def parse_agent_terminal_event(payload: Any) -> AgentTerminalEvent:
    return AgentTerminalEventAdapter.validate_python(payload)


def parse_browser_terminal_event(payload: Any) -> BrowserTerminalEvent:
    return BrowserTerminalEventAdapter.validate_python(payload)
