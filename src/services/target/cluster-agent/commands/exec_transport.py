"""Shared Kubernetes exec WebSocket transport for terminal and bounded file reads."""

from __future__ import annotations

import json
import ssl
from contextlib import AbstractAsyncContextManager
from typing import Any

KUBERNETES_EXEC_SUBPROTOCOL = "v4.channel.k8s.io"
STDIN_CHANNEL = 0
STDOUT_CHANNEL = 1
STDERR_CHANNEL = 2
STATUS_CHANNEL = 3
KUBERNETES_EXEC_OPEN_TIMEOUT_SECONDS = 10


def kubernetes_exec_connector(
    url: str,
    headers: dict[str, str],
    ssl_context: ssl.SSLContext,
) -> AbstractAsyncContextManager[Any]:
    import websockets

    return websockets.connect(
        url,
        additional_headers=headers,
        subprotocols=[KUBERNETES_EXEC_SUBPROTOCOL],
        ssl=ssl_context,
        open_timeout=KUBERNETES_EXEC_OPEN_TIMEOUT_SECONDS,
        close_timeout=2,
    )


def kubernetes_exit_code(payload: bytes) -> int | None:
    try:
        status = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return 1
    if not isinstance(status, dict):
        return 1
    if status.get("status") == "Success":
        return 0
    details = status.get("details")
    causes = details.get("causes", []) if isinstance(details, dict) else []
    for cause in causes if isinstance(causes, list) else []:
        if isinstance(cause, dict) and cause.get("reason") == "ExitCode":
            try:
                return int(cause.get("message"))
            except (TypeError, ValueError):
                return 1
    return 1
