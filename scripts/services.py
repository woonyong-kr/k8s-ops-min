"""서비스 명부를 한눈에 출력한다.

사용: python scripts/services.py   (또는 make services)
수동 목록 없음 — src/services/**/app.py 자동 발견(discovery)이 단일 출처.
"""

from __future__ import annotations

import sys
from pathlib import Path

ROOT_DIR = Path(__file__).resolve().parents[1]
if str(ROOT_DIR / "src") not in sys.path:
    sys.path.insert(0, str(ROOT_DIR / "src"))

from packages.runtime.discovery import describe_services, discover_services  # noqa: E402


def main() -> None:
    print(describe_services(discover_services(ROOT_DIR)))


if __name__ == "__main__":
    main()
