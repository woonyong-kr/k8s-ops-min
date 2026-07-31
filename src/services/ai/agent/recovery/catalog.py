from __future__ import annotations

from services.ai.agent.playbooks.discovery import load_rule_modules
from services.ai.agent.playbooks.recovery import registered_recovery_rules

load_rule_modules(
    package_name="services.ai.agent.recovery",
    excluded=("catalog", "engine", "select", "dispatch"),
)

__all__ = ["registered_recovery_rules"]
