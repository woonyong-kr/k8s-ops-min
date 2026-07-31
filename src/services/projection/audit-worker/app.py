"""audit-worker — 모든 이벤트를 감사 로그로 적재.

@app.on_any 로 전체(>) 구독. 봉투 그대로 audit 로그에 append.
체이닝 없음(말단 소비자).
"""

from __future__ import annotations

from domains.audit.repository import audit_log_row
from packages.contracts.event_bus.interfaces import EventEnvelope
from packages.contracts.stores import AuditStore
from packages.runtime.app import App, EventContext

app = App("audit-worker")


@app.on_any
async def on_event(evt: EventEnvelope, ctx: EventContext[AuditStore]) -> None:
    # 벌크 INSERT 경로로 단일화 — 지금은 이벤트 1건 = 트랜잭션 1건(ledger 원자성)이라
    # 이벤트 간 버퍼링(진짜 배치 적재)은 원자성 경계를 다시 설계한 뒤로 보류함.
    await ctx.db.append_audit_logs([audit_log_row(evt)])


if __name__ == "__main__":
    app.run()
