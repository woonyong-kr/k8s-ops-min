from __future__ import annotations

from gateway import create_app
from settings import Settings

from packages.runtime.service import FastApiService


def main() -> None:
    FastApiService(Settings.SERVICE_NAME, create_app).run()


if __name__ == "__main__":
    main()
