from __future__ import annotations

from node_collector import NodeCollectorConfig, run

from packages.runtime.service import AsyncService

__all__ = ["run"]


def main() -> None:
    AsyncService(NodeCollectorConfig.SERVICE_NAME, run).run()


if __name__ == "__main__":
    main()
