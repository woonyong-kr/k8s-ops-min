from __future__ import annotations

from poller import GitHubPoller
from settings import Settings

from packages.config.settings import env
from packages.runtime.service import AsyncService
from packages.storage.database import Database
from packages.storage.engine import DATABASE_URL_ENV


async def run() -> None:
    db = Database() if env(DATABASE_URL_ENV, "").strip() else None
    await GitHubPoller(db=db).run()


def main() -> None:
    AsyncService(Settings.SERVICE_NAME, run).run()


if __name__ == "__main__":
    main()
