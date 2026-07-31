"""카탈로그가 쓰는 상태·심각도 어휘.

여기 있는 이유는 세 곳이 같은 값을 각자 적고 있었기 때문입니다. 모델 정의에
`("error", "warning")` 이 있고, MCP 도구 스키마에 `["error", "warning"]` 이 있고,
조회 API 에 `"^(error|warning)$"` 가 있었습니다. 값이 하나 늘 때 세 곳을 다
고쳐야 하는데, 실제로는 한 곳만 고치고 나머지가 조용히 어긋납니다.

이 모듈에는 DB 의존이 없습니다. MCP 서버가 읽어도 SQLAlchemy 가 딸려 오지
않아야 하기 때문입니다.
"""

from __future__ import annotations

# 배치 전체의 결과. PARTIAL 과 FAILED 를 나누는 이유는 재처리 대상이 다르기
# 때문입니다 — PARTIAL 은 실패한 원천만, FAILED 는 전체를 다시 봅니다.
DAG_RUN_STATUSES: tuple[str, ...] = ("SUCCESS", "PARTIAL", "FAILED", "INCOMPLETE")

# 원천 하나의 수집 결과. NO_DATA 와 FAILED 를 합치면 "없는 것"과 "못 가져온 것"이
# 같아지고, 그러면 매일 전량을 다시 수집하게 됩니다.
COLLECTION_RUN_STATUSES: tuple[str, ...] = ("SUCCESS", "NO_DATA", "TRUNCATED", "FAILED")

# 검사 결과. 통과도 적재하므로 두 값이 다 필요합니다 — 검사하지 않은 것과
# 검사해서 통과한 것을 구분하기 위해서입니다.
QUALITY_STATUSES: tuple[str, ...] = ("passed", "failed")

QUALITY_SEVERITIES: tuple[str, ...] = ("error", "warning")

CHECK_TYPES: tuple[str, ...] = (
    "SOURCE_COVERAGE",
    "REQUIRED_FIELD",
    "SCHEMA_DRIFT",
    "FRESHNESS",
    "LINEAGE_BREAK",
    "RUN_CONSISTENCY",
)

# 검사 종류별 심각도. 최신성만 warning 인 이유는 SLA 안에서 늦은 것과 아예 안
# 들어온 것이 다르고, 전자는 다음 배치에서 회복될 수 있기 때문입니다. 나머지는
# 소비자가 이미 틀린 데이터를 읽고 있다는 뜻이라 error 입니다.
CHECK_SEVERITY: dict[str, str] = {
    "SOURCE_COVERAGE": "error",
    "REQUIRED_FIELD": "error",
    "SCHEMA_DRIFT": "error",
    "FRESHNESS": "warning",
    "LINEAGE_BREAK": "error",
    "RUN_CONSISTENCY": "error",
}


def severity_pattern() -> str:
    """조회 API 의 쿼리 검증용 정규식. 어휘가 늘면 여기도 같이 늡니다."""
    return "^(" + "|".join(QUALITY_SEVERITIES) + ")$"
