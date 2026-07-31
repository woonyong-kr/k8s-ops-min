"""dead-letter-monitor — dead_letter.created 를 운영 alert 흐름으로 연결."""

from __future__ import annotations

from collections.abc import AsyncIterator

from domains.alert.events import AlertRequestedBody
from packages.config.logs import get_logger
from packages.config.settings import env
from packages.contracts.event_bus.bodies import EventBody
from packages.contracts.event_bus.bodies.platform import DeadLetterCreatedBody
from packages.contracts.identity import DEFAULT_WORKSPACE_ID
from packages.runtime.app import App

app = App("dead-letter-monitor")
LOGGER = get_logger(__name__)
DEAD_LETTER_ALERT_CLUSTER_ID_ENV = "DEAD_LETTER_ALERT_CLUSTER_ID"
DEAD_LETTER_ALERT_NAMESPACE_ENV = "DEAD_LETTER_ALERT_NAMESPACE"
DEAD_LETTER_ALERT_SEVERITY_ENV = "DEAD_LETTER_ALERT_SEVERITY"
DEFAULT_DEAD_LETTER_ALERT_CLUSTER_ID = "management-plane"
DEFAULT_DEAD_LETTER_ALERT_NAMESPACE = "platform"
DEFAULT_DEAD_LETTER_ALERT_SEVERITY = "warning"


@app.on(DeadLetterCreatedBody)
async def on_dead_letter_created(evt: DeadLetterCreatedBody) -> AsyncIterator[EventBody]:
    LOGGER.warning(
        "dead letter created",
        extra={
            "context": {
                "dead_letter_id": evt.dead_letter_id,
                "original_subject": evt.original_subject,
                "consumer": evt.consumer,
                "attempts": evt.attempts,
                "error": evt.error,
            }
        },
    )
    yield AlertRequestedBody(
        cluster_id=env(DEAD_LETTER_ALERT_CLUSTER_ID_ENV, DEFAULT_DEAD_LETTER_ALERT_CLUSTER_ID),
        namespace=env(DEAD_LETTER_ALERT_NAMESPACE_ENV, DEFAULT_DEAD_LETTER_ALERT_NAMESPACE),
        severity=env(DEAD_LETTER_ALERT_SEVERITY_ENV, DEFAULT_DEAD_LETTER_ALERT_SEVERITY),
        message=f"dead letter captured for {evt.original_subject}",
        reason=f"{evt.consumer}: {evt.error}",
        workspace_id=env("WORKSPACE_ID", DEFAULT_WORKSPACE_ID),
    )


if __name__ == "__main__":
    app.run()
