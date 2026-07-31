"""운영 플레이북(playbooks) — 장애 원인/복구 지식의 단일 출처.

규칙: 등록은 @rca.<단어>, 조회는 registered_*().
    @rca.cause(...)     증상 → 원인 후보 진단 프로파일
    @rca.recovery(...)  원인 → 복구 액션 룰
    @rca.fallback(...)  어떤 룰도 안 맞을 때의 안전망

새 장애 시나리오 추가 = 원인 룰은 causes/catalog/ 아래 YAML 파일 1개,
복구 룰은 recovery/ 아래 파일 1개(엔진 수정 없음).
"""

from __future__ import annotations

from services.ai.agent.playbooks.cause import CauseCandidateSpec, causes_for
from services.ai.agent.playbooks.recovery import (
    RecoveryActionSpec,
    fallback_recovery,
    recovery_for,
)


class RcaPlaybooks:
    """RCA 룰 등록 네임스페이스."""

    cause = staticmethod(causes_for)
    recovery = staticmethod(recovery_for)
    fallback = staticmethod(fallback_recovery)


rca = RcaPlaybooks()

__all__ = [
    "CauseCandidateSpec",
    "RcaPlaybooks",
    "RecoveryActionSpec",
    "causes_for",
    "fallback_recovery",
    "recovery_for",
    "rca",
]
