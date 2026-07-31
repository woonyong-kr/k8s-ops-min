from __future__ import annotations

from agent import AgentConfig, TargetClusterAgent

from packages.runtime.service import AsyncService


async def run() -> None:
    await TargetClusterAgent().run()


def main() -> None:
    AsyncService(AgentConfig.TARGET_AGENT_SERVICE_NAME, run).run()


if __name__ == "__main__":
    main()
