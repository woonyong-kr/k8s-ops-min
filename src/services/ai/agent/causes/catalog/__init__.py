"""RCA 룰 카탈로그 합성 루트 — 코드 룰(자동 발견) + YAML 룰(카탈로그 파일) 등록.

이 패키지 디렉터리의 `*.yaml` 파일이 곧 룰 데이터다. 새 장애 시나리오 추가 =
YAML 파일 1개(코드 수정 없음). 코드 정의 룰(`@rca.cause` 모듈)도 여전히 허용된다.
"""

from __future__ import annotations

from services.ai.agent.causes.loader import register_catalog_profiles
from services.ai.agent.playbooks.cause import cause_rules, evidence_rules
from services.ai.agent.playbooks.discovery import load_rule_modules

# 1) 코드 정의 룰(@rca.cause 모듈) 자동 발견 — 여전히 허용되는 경로.
load_rule_modules(
    package_name="services.ai.agent.causes",
    excluded=("catalog", "engine", "loader"),
)
# 2) YAML 카탈로그 룰 등록 — 잘못된 파일/중복 id 는 기동 시점에 즉시 실패.
register_catalog_profiles()

__all__ = ["cause_rules", "evidence_rules"]
