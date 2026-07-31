from __future__ import annotations

from enum import StrEnum


class EventProcessingStatus(StrEnum):
    """consumer별 이벤트 처리 상태(멱등 ledger에 기록).

    흐름: PROCESSING → PROCESSED(성공)
          또는 PROCESSING → RETRYING → ... → DEAD_LETTERED(소진).
    """

    PROCESSING = "processing"  # 처리 시작(claim)
    PROCESSED = "processed"  # 성공 완료(ack)
    RETRYING = "retrying"  # 실패, 재시도 예정(nak)
    DEAD_LETTERED = "dead_lettered"  # 재시도 소진, DLQ로


# 저장 상태가 아닌 파생 신호 — 다른 소비자 인스턴스의 신선한 PROCESSING 이
# claim 을 거절했음을 뜻함. 워커는 종결(ack)도 획득(처리)도 아니므로 nak 로 미룸.
CLAIM_BLOCKED = "claim_blocked"

# 종결 상태 — 재배달이 와도 다시 처리하지 않고 ack 로 소거함.
TERMINAL_STATUSES = (EventProcessingStatus.PROCESSED, EventProcessingStatus.DEAD_LETTERED)
