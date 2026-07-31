"""Bounded binary stream contract for audited agent-owned port forwarding.

The desktop owns only a loopback TCP listener.  Target identity and the
Kubernetes connection remain owned by the cluster agent and are multiplexed on
the agent's existing outbound realtime connection.
"""

from __future__ import annotations

import re
import struct
from dataclasses import dataclass
from typing import Annotated, Any, Literal

from pydantic import Field, TypeAdapter, model_validator

from packages.contracts.modeling import StrictModel
from packages.contracts.parity import ResourceRef

PORT_FORWARD_PROTOCOL = "port-forward.v1"
BROWSER_PORT_FORWARD_PATH = "/live/port-forward"

MAX_PORT_FORWARD_SESSION_SECONDS = 60 * 60
MAX_PORT_FORWARD_IDLE_SECONDS = 5 * 60
MAX_PORT_FORWARD_OPEN_SECONDS = 15
MAX_PORT_FORWARD_SESSIONS_PER_USER = 3
MAX_PORT_FORWARD_SESSIONS_PER_AGENT = 8
MAX_PORT_FORWARD_CONNECTIONS_PER_SESSION = 4
MAX_PORT_FORWARD_FRAME_BYTES = 16 * 1024
PORT_FORWARD_CREDIT_WINDOW_BYTES = 256 * 1024
MAX_PORT_FORWARD_BYTES_PER_DIRECTION = 1024 * 1024 * 1024

PortForwardDirection = Literal["desktop_to_target", "target_to_desktop"]
PortForwardEndReason = Literal[
    "closed",
    "idle_timeout",
    "session_timeout",
    "byte_limit",
    "target_closed",
]
PortForwardErrorCode = Literal[
    "agent_unavailable",
    "audit_unavailable",
    "credit_violation",
    "invalid_target",
    "protocol_violation",
    "session_limit",
    "target_unavailable",
    "timeout",
]

_SESSION_PATTERN_TEXT = r"^[A-Za-z0-9_-]{16,64}$"
_SESSION_PATTERN = re.compile(_SESSION_PATTERN_TEXT)
_CAPABILITY_REVISION_PATTERN = r"^[0-9a-f]{64}$"


class PortForwardStart(StrictModel):
    """Desktop-to-gateway start request after one explicit confirmation."""

    type: Literal["port_forward.start"] = "port_forward.start"
    capability_revision: str = Field(pattern=_CAPABILITY_REVISION_PATTERN)
    resource: ResourceRef
    remote_port: int = Field(ge=1, le=65_535)
    confirmation: Literal[True]

    @model_validator(mode="after")
    def exact_namespaced_core_target(self) -> PortForwardStart:
        _validate_port_forward_target(self.resource)
        return self


class PortForwardOpen(StrictModel):
    """Gateway-to-agent request after session, RBAC, revision and UID checks."""

    type: Literal["port_forward.open"] = "port_forward.open"
    session_id: str = Field(pattern=_SESSION_PATTERN_TEXT)
    generation: int = Field(ge=1)
    capability_revision: str = Field(pattern=_CAPABILITY_REVISION_PATTERN)
    resource: ResourceRef
    remote_port: int = Field(ge=1, le=65_535)
    initial_credit_bytes: int = Field(
        default=PORT_FORWARD_CREDIT_WINDOW_BYTES,
        ge=MAX_PORT_FORWARD_FRAME_BYTES,
        le=PORT_FORWARD_CREDIT_WINDOW_BYTES,
    )

    @model_validator(mode="after")
    def exact_namespaced_core_target(self) -> PortForwardOpen:
        _validate_port_forward_target(self.resource)
        return self


class PortForwardOpened(StrictModel):
    type: Literal["port_forward.opened"] = "port_forward.opened"
    session_id: str = Field(pattern=_SESSION_PATTERN_TEXT)
    generation: int = Field(ge=1)
    target_kind: Literal["Pod", "Service"]
    target_name: str = Field(min_length=1, max_length=253)
    target_uid: str = Field(min_length=1, max_length=128)
    target_port: int = Field(ge=1, le=65_535)


class PortForwardConnectionOpen(StrictModel):
    type: Literal["port_forward.connection.open"] = "port_forward.connection.open"
    session_id: str = Field(pattern=_SESSION_PATTERN_TEXT)
    generation: int = Field(ge=1)
    connection_id: int = Field(ge=1, le=2**32 - 1)


class PortForwardConnectionOpened(StrictModel):
    type: Literal["port_forward.connection.opened"] = "port_forward.connection.opened"
    session_id: str = Field(pattern=_SESSION_PATTERN_TEXT)
    generation: int = Field(ge=1)
    connection_id: int = Field(ge=1, le=2**32 - 1)


class PortForwardWindow(StrictModel):
    type: Literal["port_forward.window"] = "port_forward.window"
    session_id: str = Field(pattern=_SESSION_PATTERN_TEXT)
    generation: int = Field(ge=1)
    connection_id: int = Field(ge=1, le=2**32 - 1)
    direction: PortForwardDirection
    credit_bytes: int = Field(ge=1, le=PORT_FORWARD_CREDIT_WINDOW_BYTES)


class PortForwardHalfClose(StrictModel):
    type: Literal["port_forward.half_close"] = "port_forward.half_close"
    session_id: str = Field(pattern=_SESSION_PATTERN_TEXT)
    generation: int = Field(ge=1)
    connection_id: int = Field(ge=1, le=2**32 - 1)
    direction: PortForwardDirection


class PortForwardClose(StrictModel):
    type: Literal["port_forward.close"] = "port_forward.close"
    session_id: str = Field(pattern=_SESSION_PATTERN_TEXT)
    generation: int = Field(ge=1)


class PortForwardConnectionClose(StrictModel):
    type: Literal["port_forward.connection.close"] = "port_forward.connection.close"
    session_id: str = Field(pattern=_SESSION_PATTERN_TEXT)
    generation: int = Field(ge=1)
    connection_id: int = Field(ge=1, le=2**32 - 1)


class PortForwardConnectionEnd(StrictModel):
    type: Literal["port_forward.connection.end"] = "port_forward.connection.end"
    session_id: str = Field(pattern=_SESSION_PATTERN_TEXT)
    generation: int = Field(ge=1)
    connection_id: int = Field(ge=1, le=2**32 - 1)
    reason: PortForwardEndReason
    desktop_to_target_bytes: int = Field(ge=0, le=MAX_PORT_FORWARD_BYTES_PER_DIRECTION)
    target_to_desktop_bytes: int = Field(ge=0, le=MAX_PORT_FORWARD_BYTES_PER_DIRECTION)


class PortForwardEnd(StrictModel):
    type: Literal["port_forward.end"] = "port_forward.end"
    session_id: str = Field(pattern=_SESSION_PATTERN_TEXT)
    generation: int = Field(ge=1)
    reason: PortForwardEndReason
    desktop_to_target_bytes: int = Field(ge=0, le=MAX_PORT_FORWARD_BYTES_PER_DIRECTION)
    target_to_desktop_bytes: int = Field(ge=0, le=MAX_PORT_FORWARD_BYTES_PER_DIRECTION)


class PortForwardError(StrictModel):
    type: Literal["port_forward.error"] = "port_forward.error"
    session_id: str | None = Field(default=None, pattern=_SESSION_PATTERN_TEXT)
    generation: int | None = Field(default=None, ge=1)
    code: PortForwardErrorCode
    message: str = Field(min_length=1, max_length=240)
    retryable: bool = False


DesktopPortForwardRequest = Annotated[
    PortForwardStart
    | PortForwardConnectionOpen
    | PortForwardWindow
    | PortForwardHalfClose
    | PortForwardConnectionClose
    | PortForwardClose,
    Field(discriminator="type"),
]
AgentPortForwardRequest = Annotated[
    PortForwardOpen
    | PortForwardConnectionOpen
    | PortForwardWindow
    | PortForwardHalfClose
    | PortForwardConnectionClose
    | PortForwardClose,
    Field(discriminator="type"),
]
AgentPortForwardEvent = Annotated[
    PortForwardOpened
    | PortForwardConnectionOpened
    | PortForwardWindow
    | PortForwardHalfClose
    | PortForwardConnectionEnd
    | PortForwardEnd
    | PortForwardError,
    Field(discriminator="type"),
]
DesktopPortForwardEvent = Annotated[
    PortForwardOpened
    | PortForwardConnectionOpened
    | PortForwardWindow
    | PortForwardHalfClose
    | PortForwardConnectionEnd
    | PortForwardEnd
    | PortForwardError,
    Field(discriminator="type"),
]

DesktopPortForwardRequestAdapter: TypeAdapter[DesktopPortForwardRequest] = TypeAdapter(
    DesktopPortForwardRequest
)
AgentPortForwardRequestAdapter: TypeAdapter[AgentPortForwardRequest] = TypeAdapter(
    AgentPortForwardRequest
)
AgentPortForwardEventAdapter: TypeAdapter[AgentPortForwardEvent] = TypeAdapter(
    AgentPortForwardEvent
)
DesktopPortForwardEventAdapter: TypeAdapter[DesktopPortForwardEvent] = TypeAdapter(
    DesktopPortForwardEvent
)


def parse_desktop_port_forward_request(payload: Any) -> DesktopPortForwardRequest:
    return DesktopPortForwardRequestAdapter.validate_python(payload)


def parse_agent_port_forward_request(payload: Any) -> AgentPortForwardRequest:
    return AgentPortForwardRequestAdapter.validate_python(payload)


def parse_agent_port_forward_event(payload: Any) -> AgentPortForwardEvent:
    return AgentPortForwardEventAdapter.validate_python(payload)


def parse_desktop_port_forward_event(payload: Any) -> DesktopPortForwardEvent:
    return DesktopPortForwardEventAdapter.validate_python(payload)


def _validate_port_forward_target(resource: ResourceRef) -> None:
    if resource.api_group not in {"", "core"} or resource.version != "v1":
        raise ValueError("port-forward requires a core/v1 target")
    if resource.kind.casefold() not in {"pod", "service"}:
        raise ValueError("port-forward requires a Pod or Service target")
    if resource.namespace is None:
        raise ValueError("port-forward requires a namespaced target")


_FRAME_MAGIC = b"OPF1"
_FRAME_VERSION = 1
_DIRECTION_TO_CODE: dict[PortForwardDirection, int] = {
    "desktop_to_target": 1,
    "target_to_desktop": 2,
}
_CODE_TO_DIRECTION = {value: key for key, value in _DIRECTION_TO_CODE.items()}
_FRAME_HEADER = struct.Struct("!4sBBHQQII")


@dataclass(frozen=True, slots=True)
class PortForwardDataFrame:
    session_id: str
    generation: int
    connection_id: int
    sequence: int
    direction: PortForwardDirection
    payload: bytes

    def __post_init__(self) -> None:
        if _SESSION_PATTERN.fullmatch(self.session_id) is None:
            raise ValueError("invalid port-forward session id")
        if self.generation < 1:
            raise ValueError("invalid port-forward generation")
        if not 1 <= self.connection_id <= 2**32 - 1:
            raise ValueError("invalid port-forward connection id")
        if self.sequence < 0:
            raise ValueError("invalid port-forward sequence")
        if not 1 <= len(self.payload) <= MAX_PORT_FORWARD_FRAME_BYTES:
            raise ValueError("port-forward payload is outside the frame budget")


def encode_port_forward_data(frame: PortForwardDataFrame) -> bytes:
    session = frame.session_id.encode("ascii")
    header = _FRAME_HEADER.pack(
        _FRAME_MAGIC,
        _FRAME_VERSION,
        _DIRECTION_TO_CODE[frame.direction],
        len(session),
        frame.generation,
        frame.sequence,
        frame.connection_id,
        len(frame.payload),
    )
    return header + session + frame.payload


def decode_port_forward_data(raw: bytes) -> PortForwardDataFrame:
    if len(raw) < _FRAME_HEADER.size:
        raise ValueError("port-forward frame header is truncated")
    (
        magic,
        version,
        direction_code,
        session_length,
        generation,
        sequence,
        connection_id,
        payload_length,
    ) = _FRAME_HEADER.unpack_from(raw)
    if magic != _FRAME_MAGIC or version != _FRAME_VERSION:
        raise ValueError("unsupported port-forward frame")
    direction = _CODE_TO_DIRECTION.get(direction_code)
    if direction is None:
        raise ValueError("invalid port-forward frame direction")
    if not 16 <= session_length <= 64:
        raise ValueError("invalid port-forward frame session length")
    if not 1 <= payload_length <= MAX_PORT_FORWARD_FRAME_BYTES:
        raise ValueError("invalid port-forward frame payload length")
    expected = _FRAME_HEADER.size + session_length + payload_length
    if len(raw) != expected:
        raise ValueError("port-forward frame length is inconsistent")
    session_end = _FRAME_HEADER.size + session_length
    try:
        session_id = raw[_FRAME_HEADER.size : session_end].decode("ascii")
    except UnicodeDecodeError as exc:
        raise ValueError("port-forward session id must be ASCII") from exc
    return PortForwardDataFrame(
        session_id=session_id,
        generation=generation,
        connection_id=connection_id,
        sequence=sequence,
        direction=direction,
        payload=raw[session_end:],
    )
