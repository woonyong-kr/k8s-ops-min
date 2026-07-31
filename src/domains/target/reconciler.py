"""target desired-state 비교 로직.

이 모듈은 Kubernetes API를 직접 호출하지 않음. 운영 구현에서는
ActualStateReader port 뒤에 Kubernetes watch/cache/agent 보고를 연결함.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Protocol

from domains.target.events import TargetDesiredComponent, TargetDrift
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.target import TargetReconcileStatus

COMPONENT_MISSING_REASON = "component missing from actual state"
VERSION_MISMATCH_REASON = "component version differs"
SPEC_MISMATCH_REASON = "component spec differs"
NO_DRIFT_MESSAGE = "desired and actual state are in sync"
DRIFT_MESSAGE = "desired and actual state differ"


@dataclass(frozen=True)
class ActualStateSnapshot:
    components: Mapping[str, JsonObject] = field(default_factory=dict)


class ActualStateReader(Protocol):
    async def read_actual_state(
        self, workspace_id: str, cluster_id: str
    ) -> ActualStateSnapshot: ...


@dataclass(frozen=True)
class ReconcileDecision:
    status: str
    drifted: bool
    message: str
    drifts: list[TargetDrift]


class TargetReconciler:
    """desired component 목록과 actual snapshot을 비교해 drift를 판정함."""

    def evaluate(
        self,
        desired_components: Sequence[TargetDesiredComponent],
        actual: ActualStateSnapshot,
    ) -> ReconcileDecision:
        drifts: list[TargetDrift] = []
        for desired in desired_components:
            actual_component = actual.components.get(desired.component)
            if actual_component is None:
                drifts.append(component_drift(desired, COMPONENT_MISSING_REASON, None))
                continue
            if str(actual_component.get("version", "")) != desired.version:
                drifts.append(component_drift(desired, VERSION_MISMATCH_REASON, actual_component))
                continue
            if dict(actual_component.get("spec", {})) != desired.spec:
                drifts.append(component_drift(desired, SPEC_MISMATCH_REASON, actual_component))

        if drifts:
            return ReconcileDecision(
                status=TargetReconcileStatus.DRIFTED.value,
                drifted=True,
                message=DRIFT_MESSAGE,
                drifts=drifts,
            )
        return ReconcileDecision(
            status=TargetReconcileStatus.IN_SYNC.value,
            drifted=False,
            message=NO_DRIFT_MESSAGE,
            drifts=[],
        )


def component_drift(
    desired: TargetDesiredComponent, reason: str, actual: JsonObject | None
) -> TargetDrift:
    return TargetDrift(
        component=desired.component,
        reason=reason,
        desired=desired.to_body(),
        actual=actual,
    )


def desired_state_version(components: Sequence[TargetDesiredComponent]) -> str:
    body = [component.to_body() for component in components]
    encoded = json.dumps(body, sort_keys=True, separators=(",", ":")).encode()
    return hashlib.sha256(encoded).hexdigest()[:16]
