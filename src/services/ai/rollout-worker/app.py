"""rollout-worker — command.completed -> rollout.diagnosed."""

from __future__ import annotations

from collections.abc import AsyncIterator, Mapping

from domains.command.events import CommandCompletedBody
from domains.rca.events import RolloutDiagnosedBody
from packages.config.constants import CommandStatus
from packages.contracts.event_bus.bodies import EventBody
from packages.contracts.event_bus.interfaces import JsonObject
from packages.runtime.app import App

app = App("rollout-worker")

NEXT_OBSERVE = "observe"
NEXT_RETRY = "retry"
NEXT_MANUAL_REVIEW = "manual_review"


def failed_resources(result: JsonObject) -> list[JsonObject]:
    resources = result.get("resources")
    if not isinstance(resources, list):
        return []
    failed: list[JsonObject] = []
    for item in resources:
        if not isinstance(item, Mapping):
            continue
        if item.get("applied") is False or str(item.get("status", "")).lower() == "failed":
            failed.append(dict(item))
    return failed


def rollout_ready(result: JsonObject) -> bool:
    rollout = result.get("rollout")
    if isinstance(rollout, Mapping) and rollout.get("ready") is False:
        return False
    return True


def rollout_applied(result: JsonObject) -> bool:
    return (
        result.get("status") == CommandStatus.COMPLETED
        and result.get("applied") is True
        and not failed_resources(result)
        and rollout_ready(result)
    )


def next_action_for_result(result: JsonObject) -> str:
    if rollout_applied(result):
        return NEXT_OBSERVE
    if result.get("retryable") is True:
        return NEXT_RETRY
    return NEXT_MANUAL_REVIEW


def diagnosis_for_result(result: JsonObject) -> str:
    if rollout_applied(result):
        return "rollout command applied and reported healthy"
    failures = failed_resources(result)
    if failures:
        return f"rollout command reported {len(failures)} failed resource(s)"
    message = str(result.get("message") or result.get("status") or "rollout command failed")
    return message


@app.on(CommandCompletedBody)
async def on_command_completed(evt: CommandCompletedBody) -> AsyncIterator[EventBody]:
    details = {
        **evt.result,
        "command_id": evt.command_id,
        "failed_resources": failed_resources(evt.result),
    }
    yield RolloutDiagnosedBody(
        diagnosis=diagnosis_for_result(evt.result),
        next_action=next_action_for_result(evt.result),
        details=details,
        workspace_id=str(evt.result.get("workspace_id", "default")),
    )


if __name__ == "__main__":
    app.run()
