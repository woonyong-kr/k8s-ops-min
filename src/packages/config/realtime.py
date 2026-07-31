"""Realtime gateway 주소와 ingress 보호 한계."""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlsplit

from packages.config.settings import env
from packages.contracts.realtime import RealtimeIngressLimits

DEFAULT_REALTIME_GATEWAY_NODEPORT = 30090  # legacy local-up manifest compatibility
MANAGEMENT_REALTIME_GATEWAY_URL = "ws://realtime-gateway.management.svc.cluster.local:8000"


REALTIME_DELTA_KEY_MAX_LENGTH_ENV = "REALTIME_DELTA_KEY_MAX_LENGTH"
REALTIME_DELTA_VALUE_MAX_BYTES_ENV = "REALTIME_DELTA_VALUE_MAX_BYTES"
REALTIME_DELTA_VALUE_MAX_FIELDS_ENV = "REALTIME_DELTA_VALUE_MAX_FIELDS"
REALTIME_DELTA_VALUE_MAX_DEPTH_ENV = "REALTIME_DELTA_VALUE_MAX_DEPTH"
REALTIME_AGENT_MESSAGE_MAX_BYTES_ENV = "REALTIME_AGENT_MESSAGE_MAX_BYTES"
REALTIME_AGENT_INGRESS_WINDOW_SECONDS_ENV = "REALTIME_AGENT_INGRESS_WINDOW_SECONDS"
REALTIME_AGENT_MESSAGES_PER_WINDOW_ENV = "REALTIME_AGENT_MESSAGES_PER_WINDOW"
REALTIME_AGENT_BYTES_PER_WINDOW_ENV = "REALTIME_AGENT_BYTES_PER_WINDOW"
REALTIME_CLUSTER_RETAINED_RESOURCES_ENV = "REALTIME_CLUSTER_RETAINED_RESOURCES"
REALTIME_SNAPSHOT_MAX_RESOURCES_ENV = "REALTIME_SNAPSHOT_MAX_RESOURCES"
REALTIME_SNAPSHOT_MAX_BYTES_ENV = "REALTIME_SNAPSHOT_MAX_BYTES"


@dataclass(frozen=True)
class RealtimeGatewayLimitDefaults:
    """Deployment defaults; every value can be tightened or raised by environment."""

    delta_key_max_length: int = 1_024
    delta_value_max_bytes: int = 16 * 1_024
    delta_value_max_fields: int = 256
    delta_value_max_depth: int = 8
    agent_message_max_bytes: int = 32 * 1_024
    agent_ingress_window_seconds: float = 60.0
    agent_messages_per_window: int = 6_000
    agent_bytes_per_window: int = 32 * 1_024 * 1_024
    cluster_retained_resources: int = 5_000
    snapshot_max_resources: int = 5_000
    snapshot_max_bytes: int = 16 * 1_024 * 1_024


REALTIME_GATEWAY_LIMIT_DEFAULTS = RealtimeGatewayLimitDefaults()


def realtime_gateway_limits() -> RealtimeIngressLimits:
    """Load validated, explicit protection budgets for one gateway process."""

    defaults = REALTIME_GATEWAY_LIMIT_DEFAULTS
    return RealtimeIngressLimits(
        delta_key_max_length=_positive_int(
            REALTIME_DELTA_KEY_MAX_LENGTH_ENV, defaults.delta_key_max_length
        ),
        delta_value_max_bytes=_positive_int(
            REALTIME_DELTA_VALUE_MAX_BYTES_ENV, defaults.delta_value_max_bytes
        ),
        delta_value_max_fields=_positive_int(
            REALTIME_DELTA_VALUE_MAX_FIELDS_ENV, defaults.delta_value_max_fields
        ),
        delta_value_max_depth=_positive_int(
            REALTIME_DELTA_VALUE_MAX_DEPTH_ENV, defaults.delta_value_max_depth
        ),
        agent_message_max_bytes=_positive_int(
            REALTIME_AGENT_MESSAGE_MAX_BYTES_ENV, defaults.agent_message_max_bytes
        ),
        agent_ingress_window_seconds=_positive_float(
            REALTIME_AGENT_INGRESS_WINDOW_SECONDS_ENV,
            defaults.agent_ingress_window_seconds,
        ),
        agent_messages_per_window=_positive_int(
            REALTIME_AGENT_MESSAGES_PER_WINDOW_ENV, defaults.agent_messages_per_window
        ),
        agent_bytes_per_window=_positive_int(
            REALTIME_AGENT_BYTES_PER_WINDOW_ENV, defaults.agent_bytes_per_window
        ),
        cluster_retained_resources=_positive_int(
            REALTIME_CLUSTER_RETAINED_RESOURCES_ENV, defaults.cluster_retained_resources
        ),
        snapshot_max_resources=_positive_int(
            REALTIME_SNAPSHOT_MAX_RESOURCES_ENV, defaults.snapshot_max_resources
        ),
        snapshot_max_bytes=_positive_int(
            REALTIME_SNAPSHOT_MAX_BYTES_ENV, defaults.snapshot_max_bytes
        ),
    )


def _positive_int(name: str, default: int) -> int:
    try:
        value = int(env(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def _positive_float(name: str, default: float) -> float:
    try:
        value = float(env(name, str(default)))
    except ValueError:
        return default
    return value if value > 0 else default


def derive_realtime_gateway_url(
    management_base_url: str,
    *,
    management_cluster: bool = False,
) -> str:
    """관리 API 주소에서 agent 전용 WebSocket 주소를 계산한다."""
    if management_cluster and not management_base_url.strip():
        return MANAGEMENT_REALTIME_GATEWAY_URL

    parts = urlsplit(management_base_url)
    if not parts.hostname:
        return ""

    if parts.scheme not in {"http", "https"}:
        return ""
    host = f"[{parts.hostname}]" if ":" in parts.hostname else parts.hostname
    default_port = 443 if parts.scheme == "https" else 80
    suffix = f":{parts.port}" if parts.port not in {None, default_port} else ""
    scheme = "wss" if parts.scheme == "https" else "ws"
    # 등록 API는 보통 /api 아래에 있지만 agent 전용 proxy는 origin의
    # 정확한 /live/agent 경로만 노출한다. 관리 API path를 이어 붙이지 않는다.
    return f"{scheme}://{host}{suffix}"
