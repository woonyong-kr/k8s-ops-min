"""제어(쓰기) 명령 허용 네임스페이스 정책 — 단일 기준.

기존에는 "sandbox 만 허용"이 게이트웨이 검증·command-worker 정책·cluster-agent
3곳에 각각 고정값 사용되어 있었다. 이 모듈이 유일한 기준이 된다:

- 기본값은 기존과 동일하게 sandbox 뿐(배포 호환 — env 미설정 시 동작 변화 없음).
- CONTROL_ALLOWED_NAMESPACES (콤마 구분) 로 확장한다. 예: "sandbox,staging,prod-web"
- management 네임스페이스는 보호 네임스페이스라 allowlist 에 들어와도 항상 제거한다.
- 관리 플레인(게이트웨이·워커)은 프로세스 env, 대상 클러스터의 agent 는 설치
  manifest ConfigMap 으로 각자 주입받는다 — 클러스터별로 다르게 줄 수 있다.

매 호출 시 env 를 읽는다(재기동 없이 테스트 가능, 호출 빈도 대비 비용 무시 가능).
"""

from __future__ import annotations

from packages.config.constants import Sandbox
from packages.config.settings import env

CONTROL_ALLOWED_NAMESPACES_ENV = "CONTROL_ALLOWED_NAMESPACES"
CONTROL_NAMESPACE_DENIED_CODE = "control_namespace_not_allowed"
CONTROL_NAMESPACE_DENIED_MESSAGE = "namespace is not allowed by control policy"
CONTROL_PROTECTED_NAMESPACES = ("management",)


def control_namespace_protected(namespace: str) -> bool:
    return namespace in CONTROL_PROTECTED_NAMESPACES


def control_allowed_namespaces() -> tuple[str, ...]:
    raw = env(CONTROL_ALLOWED_NAMESPACES_ENV, Sandbox.NAMESPACE)
    values = tuple(dict.fromkeys(item.strip() for item in raw.split(",") if item.strip()))
    if not values:
        values = (Sandbox.NAMESPACE,)
    return tuple(value for value in values if not control_namespace_protected(value))


def control_namespace_allowed(namespace: str) -> bool:
    return namespace in control_allowed_namespaces()
