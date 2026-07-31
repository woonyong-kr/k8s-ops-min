"""카탈로그 응답의 사유 코드. 닫힌 열거다.

처음에는 "SOURCE_FAILED:loki" 같은 문자열이었다. 그러면 소비자가 전부
split(":") 을 쓰게 되고, 그 문자열은 LLM 이 읽는 제어 필드이기도 하다.
코드와 대상을 분리했다.

열거를 닫아 두는 이유: 01번 문서의 원칙이 "소비자는 응답만 보고 결정한다"
인데, 열린 집합에는 switch 를 쓸 수 없다.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ReasonCode(StrEnum):
    # 수집
    SOURCE_FAILED = "SOURCE_FAILED"
    SOURCE_TRUNCATED = "SOURCE_TRUNCATED"
    NO_SOURCE_DATA = "NO_SOURCE_DATA"
    # 검사
    NEVER_RUN = "NEVER_RUN"
    PARTIAL_CHECK_COVERAGE = "PARTIAL_CHECK_COVERAGE"
    # 응답
    RESULT_TRUNCATED = "RESULT_TRUNCATED"
    REASON_CODES_TRUNCATED = "REASON_CODES_TRUNCATED"


@dataclass(frozen=True)
class Reason:
    """사유 하나. 코드와 대상을 분리해서 담는다."""

    code: ReasonCode
    source: str | None = None
    asset_id: str | None = None

    def as_dict(self) -> dict[str, str]:
        payload = {"code": str(self.code)}
        if self.source:
            payload["source"] = self.source
        if self.asset_id:
            payload["asset_id"] = self.asset_id
        return payload


MAX_REASON_CODES = 16
"""사유 목록 상한.

사유가 계속 쌓이면 그것대로 응답을 밀어낸다. 다만 잘렸다는 사실은
REASON_CODES_TRUNCATED 로 남긴다. 잘림을 숨기지 않겠다면서 잘렸다고
알려주는 필드 자체를 조용히 자르면 앞뒤가 맞지 않는다.
"""


def bound_reasons(reasons: list[Reason]) -> tuple[list[dict[str, str]], bool]:
    if len(reasons) <= MAX_REASON_CODES:
        return [r.as_dict() for r in reasons], False
    kept = [r.as_dict() for r in reasons[: MAX_REASON_CODES - 1]]
    kept.append(Reason(ReasonCode.REASON_CODES_TRUNCATED).as_dict())
    return kept, True
