"""target drift worker 이벤트 핸들러."""

from __future__ import annotations

from collections.abc import AsyncIterator

from domains.alert.events import AlertRequestedBody
from domains.command.events import CommandRequestedBody
from domains.gitops.events import Diff
from domains.target.events import ClusterDriftDetectedBody, TargetDrift
from packages.config.constants import RiskLevel
from packages.contracts.event_bus.bodies import EventBody
from packages.runtime.app import EventContext

DRIFT_ALERT_SEVERITY = "warning"
DRIFT_SYNC_ACTION = "sync"


async def on_drift_detected(
    evt: ClusterDriftDetectedBody, ctx: EventContext
) -> AsyncIterator[EventBody]:
    """드리프트 감지 시 각 항목에 대해 sync 명령을 요청하고 운영자에게 알림을 발행한다."""

    for drift in evt.drifts:
        diff = _diff_from_drift(drift, evt)

        command = CommandRequestedBody(
            cluster_id=evt.cluster_id,
            action=DRIFT_SYNC_ACTION,
            namespace=drift.desired.get("namespace", "default"),
            reason=drift.reason,
            diff=diff,
        )
        yield command

        yield AlertRequestedBody(
            cluster_id=evt.cluster_id,
            namespace=diff.namespace,
            severity=DRIFT_ALERT_SEVERITY,
            message=f"drift detected on {drift.component}: {drift.reason}",
            reason=drift.reason,
            next_command=command,
        )


def _diff_from_drift(drift: TargetDrift, evt: ClusterDriftDetectedBody) -> Diff:
    """TargetDrift 값 객체를 CommandRequestedBody 가 요구하는 Diff 로 변환한다."""

    return Diff(
        resource=drift.component,
        namespace=drift.desired.get("namespace", "default"),
        desired_image=drift.desired.get("image", ""),
        actual_image=(drift.actual or {}).get("image", ""),
        risk=RiskLevel.REVIEW_REQUIRED,
    )
