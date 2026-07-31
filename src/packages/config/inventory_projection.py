"""인벤토리 필터 프로젝션 임계구역(워크스페이스 advisory 락) 축소 플래그.

late-lock 경로는 워크스페이스 advisory 락을 무거운 read/diff 계산 이후,
revision 할당 직전에만 잡아 임계구역을 축소한다. 의미론은 legacy 와 동일하다
(revision 할당~커밋이 여전히 같은 락 구간 안이라 커서 갭이 생기지 않음).

라이브에서 예기치 못한 문제가 관측되면 INVENTORY_LATE_PROJECTION_LOCK=0 으로
즉시 legacy(함수 시작 시 락 획득) 경로로 되돌릴 수 있다. env 미설정 기본은 활성.
"""

from __future__ import annotations

from packages.config.settings import env

INVENTORY_LATE_PROJECTION_LOCK_ENV = "INVENTORY_LATE_PROJECTION_LOCK"
_TRUE_ENV_VALUES = frozenset({"1", "true", "yes", "on"})


def late_projection_lock_enabled() -> bool:
    """워크스페이스 프로젝션 락을 무거운 계산 이후로 늦춰 임계구역을 축소할지 여부."""
    return env(INVENTORY_LATE_PROJECTION_LOCK_ENV, "1").strip().lower() in _TRUE_ENV_VALUES
