"""target reconcile worker 이벤트 핸들러."""

from __future__ import annotations

from collections.abc import AsyncIterator

from domains.target.events import (
    ClusterDesiredStateChangedBody,
    ClusterDriftDetectedBody,
    ClusterReconcileCompletedBody,
    ClusterReconcileFailedBody,
    ClusterReconcileRequestedBody,
    ClusterReconcileStartedBody,
    TargetDesiredComponent,
)
from domains.target.reconciler import ActualStateSnapshot, TargetReconciler
from packages.contracts.event_bus.bodies import EventBody
from packages.contracts.event_bus.interfaces import JsonObject
from packages.contracts.stores import TargetReconcileStore
from packages.contracts.target import TargetReconcileStatus
from packages.runtime.app import EventContext

ACTUAL_STATE_PENDING_MESSAGE = "actual state snapshot is not available yet"


async def on_desired_state_changed(
    evt: ClusterDesiredStateChangedBody, ctx: EventContext
) -> AsyncIterator[EventBody]:
    yield ClusterReconcileRequestedBody(
        workspace_id=evt.workspace_id,
        cluster_id=evt.cluster_id,
        desired_state_version=evt.desired_state_version,
        reason=evt.reason,
        requested_by=evt.requested_by,
    )


async def on_reconcile_requested(
    evt: ClusterReconcileRequestedBody, ctx: EventContext[TargetReconcileStore]
) -> AsyncIterator[EventBody]:
    rows = await ctx.db.list_target_desired_states(evt.workspace_id, evt.cluster_id)
    try:
        components = [component_from_row(row) for row in rows]
        yield ClusterReconcileStartedBody(
            workspace_id=evt.workspace_id,
            cluster_id=evt.cluster_id,
            desired_state_version=evt.desired_state_version,
            component_count=len(components),
        )

        if evt.actual_state is None:
            completed = ClusterReconcileCompletedBody(
                workspace_id=evt.workspace_id,
                cluster_id=evt.cluster_id,
                desired_state_version=evt.desired_state_version,
                status=TargetReconcileStatus.REQUESTED.value,
                drifted=False,
                applied=False,
                message=ACTUAL_STATE_PENDING_MESSAGE,
            )
            await record_reconcile(ctx, completed, components, evt.actual_state)
            yield completed
            return

        decision = TargetReconciler().evaluate(
            components,
            ActualStateSnapshot(components=actual_components(evt.actual_state)),
        )
        if decision.drifted:
            yield ClusterDriftDetectedBody(
                workspace_id=evt.workspace_id,
                cluster_id=evt.cluster_id,
                desired_state_version=evt.desired_state_version,
                drifts=decision.drifts,
            )

        completed = ClusterReconcileCompletedBody(
            workspace_id=evt.workspace_id,
            cluster_id=evt.cluster_id,
            desired_state_version=evt.desired_state_version,
            status=decision.status,
            drifted=decision.drifted,
            applied=False,
            message=decision.message,
            drifts=decision.drifts,
        )
        await record_reconcile(ctx, completed, components, evt.actual_state)
        yield completed
    except Exception as exc:
        failed = ClusterReconcileFailedBody(
            workspace_id=evt.workspace_id,
            cluster_id=evt.cluster_id,
            desired_state_version=evt.desired_state_version,
            error_type=type(exc).__name__,
            message=str(exc) or type(exc).__name__,
        )
        await record_reconcile_failure(ctx, failed, evt.actual_state)
        yield failed


def component_from_row(row: JsonObject) -> TargetDesiredComponent:
    return TargetDesiredComponent(
        component=str(row["component"]),
        namespace=str(row["namespace"]),
        version=str(row["version"]),
        spec=dict(row.get("spec", {})),
    )


def actual_components(actual_state: JsonObject) -> dict[str, JsonObject]:
    components = actual_state.get("components", {})
    return dict(components) if isinstance(components, dict) else {}


async def record_reconcile(
    ctx: EventContext[TargetReconcileStore],
    completed: ClusterReconcileCompletedBody,
    desired_components: list[TargetDesiredComponent],
    actual_state: JsonObject | None,
) -> None:
    await ctx.db.record_target_reconcile_result(
        {
            "workspace_id": completed.workspace_id,
            "cluster_id": completed.cluster_id,
            "desired_state_version": completed.desired_state_version,
            "status": completed.status,
            "drifted": completed.drifted,
            "applied": completed.applied,
            "message": completed.message,
            "details": {
                "desired_components": [component.to_body() for component in desired_components],
                "actual_state": actual_state,
                "drifts": [drift.to_body() for drift in completed.drifts],
            },
        }
    )


async def record_reconcile_failure(
    ctx: EventContext[TargetReconcileStore],
    failed: ClusterReconcileFailedBody,
    actual_state: JsonObject | None,
) -> None:
    await ctx.db.record_target_reconcile_result(
        {
            "workspace_id": failed.workspace_id,
            "cluster_id": failed.cluster_id,
            "desired_state_version": failed.desired_state_version,
            "status": failed.status,
            "drifted": False,
            "applied": False,
            "message": failed.message,
            "details": {
                "error_type": failed.error_type,
                "actual_state": actual_state,
            },
        }
    )
