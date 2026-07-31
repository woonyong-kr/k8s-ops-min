"""command-worker 진입점."""

from __future__ import annotations

from collections.abc import AsyncIterator

from domains.command.events import CommandRequestedBody
from domains.command.handler import (
    COMMAND_CONFIG,
    handle_command_requested,
    sweep_expired_agent_commands,
)
from packages.contracts.event_bus.bodies import EventBody
from packages.contracts.stores import AgentCommandStore
from packages.runtime.app import App, EventContext

app = App(COMMAND_CONFIG.service_name)


@app.on(CommandRequestedBody)
async def on_command_requested(
    evt: CommandRequestedBody, ctx: EventContext[AgentCommandStore]
) -> AsyncIterator[EventBody]:
    async for body in handle_command_requested(evt, ctx):
        yield body
    # 전용 command-janitor와 함께, 명령 이벤트 길목에서도 기회적으로 만료 명령을 정리함.
    async for body in sweep_expired_agent_commands(ctx):
        yield body


if __name__ == "__main__":
    app.run()
