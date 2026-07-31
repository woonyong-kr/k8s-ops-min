from __future__ import annotations

import json

from services.ai.agent.pipeline.incident import (
    APPLICATION_5XX_SIGNAL,
    derive_log_incident_signal,
)


def stream_log(namespace: str, container: str, payload: dict[str, object]) -> list[dict]:
    return [
        {
            "query": f'{{k8s_namespace_name="{namespace}"}}',
            "source": "loki",
            "streams": [
                {
                    "stream": {
                        "k8s_namespace_name": namespace,
                        "k8s_container_name": container,
                    },
                    "values": [{"line": json.dumps(payload)}],
                }
            ],
        }
    ]


def test_target_agent_timeout_is_not_promoted_as_application_incident() -> None:
    logs = stream_log(
        "target",
        "cluster-agent",
        {
            "level": "warning",
            "service": "cluster-agent",
            "action": "command_polling_failed",
            "exception_type": "ReadTimeout",
        },
    )

    assert derive_log_incident_signal(logs) is None


def test_sandbox_application_5xx_remains_incident_eligible() -> None:
    logs = stream_log(
        "sandbox",
        "api-server",
        {
            "service": "api-server",
            "status": 503,
            "message": "admission request failed",
        },
    )

    signal = derive_log_incident_signal(logs)

    assert signal is not None
    assert signal.signal == APPLICATION_5XX_SIGNAL
    assert signal.resource_name == "api-server"
    assert signal.namespace == "sandbox"
