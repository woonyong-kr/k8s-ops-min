"""chat-worker 전용 도구 — 서비스 레이어 능력(RCA 플레이북 카탈로그)을 @ai.tool 로 등록.

domains 는 services 를 import 할 수 없으므로(계층 규칙) 플레이북 조회 도구는
서비스 로컬 모듈에 등록함. app.py 가 이 모듈을 import 하면 등록이 유발됨.
"""

from __future__ import annotations

from typing import Any

from packages.ai.tools import ToolContext, ai
from services.ai.agent.playbooks.cause import registered_cause_profiles
from services.ai.agent.playbooks.recovery import registered_recovery_rules


@ai.tool(
    name="list_recovery_playbooks",
    description="Registered RCA playbooks: symptom→cause profiles and cause→recovery actions.",
)
async def list_recovery_playbooks(context: ToolContext) -> dict[str, Any]:
    causes = [
        {
            "symptoms": list(profile.symptoms),
            "required_sources": list(profile.required_sources),
            "candidates": [spec.candidate_id for spec in profile.candidate_specs],
        }
        for profile in registered_cause_profiles()
    ]
    recoveries = []
    for rule in registered_recovery_rules():
        recoveries.append(
            {
                "rule": type(rule).__name__,
                # 폴백(AlwaysAvailable) 룰은 root_causes 가 없음 — "*" 로 표기함.
                "root_causes": list(getattr(rule, "root_causes", ())) or ["*"],
                "actions": [
                    {
                        "action_type": spec.action_type,
                        "title": spec.title,
                        "risk_level": spec.risk_level,
                        "approval_required": spec.approval_required,
                    }
                    for spec in getattr(rule, "action_specs", ())
                ],
            }
        )
    return {"causes": causes, "recoveries": recoveries}
