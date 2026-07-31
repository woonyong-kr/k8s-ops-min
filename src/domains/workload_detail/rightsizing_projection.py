"""Rightsizing projection from an optional persisted observation repository."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from pydantic import ValidationError

from packages.contracts.parity import ResourceRef
from packages.contracts.rightsizing import (
    RightsizingObservedScan,
    RightsizingObservedWorkload,
    RightsizingUnavailableScan,
    RightsizingUnavailableWorkload,
    RightsizingWorkloadEvidence,
)

RIGHTSIZING_OBSERVATION_UNAVAILABLE = "rightsizing_observation_not_integrated"
RIGHTSIZING_OBSERVATION_INVALID = "rightsizing_observation_invalid"
RIGHTSIZING_SUPPORTED_KINDS = frozenset({"Deployment", "StatefulSet", "DaemonSet"})


def workload_rightsizing_evidence(
    db: Any,
    *,
    workspace_id: str,
    cluster_id: str,
    resource: ResourceRef,
) -> RightsizingWorkloadEvidence:
    if resource.kind not in RIGHTSIZING_SUPPORTED_KINDS:
        return RightsizingUnavailableWorkload(
            reason_codes=("rightsizing_workload_kind_not_supported",)
        )
    reader = getattr(db, "get_rightsizing_observation", None)
    if not callable(reader):
        return RightsizingUnavailableWorkload(reason_codes=(RIGHTSIZING_OBSERVATION_UNAVAILABLE,))
    payload = reader(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        resource_uid=resource.uid,
    )
    if not isinstance(payload, Mapping):
        return RightsizingUnavailableWorkload(reason_codes=(RIGHTSIZING_OBSERVATION_UNAVAILABLE,))
    try:
        observed = RightsizingObservedWorkload.model_validate(payload)
    except ValidationError:
        return RightsizingUnavailableWorkload(reason_codes=(RIGHTSIZING_OBSERVATION_INVALID,))
    if observed.resource != resource:
        return RightsizingUnavailableWorkload(reason_codes=(RIGHTSIZING_OBSERVATION_INVALID,))
    return observed


def rightsizing_scan_result(
    db: Any,
    *,
    workspace_id: str,
    cluster_id: str,
    namespaces: tuple[str, ...],
    limit: int,
) -> RightsizingObservedScan | RightsizingUnavailableScan:
    reader = getattr(db, "list_rightsizing_observations", None)
    if not callable(reader):
        return RightsizingUnavailableScan(reason_codes=(RIGHTSIZING_OBSERVATION_UNAVAILABLE,))
    payload = reader(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        namespaces=namespaces,
        limit=limit,
    )
    if not isinstance(payload, Mapping):
        return RightsizingUnavailableScan(reason_codes=(RIGHTSIZING_OBSERVATION_UNAVAILABLE,))
    try:
        observed = RightsizingObservedScan.model_validate(payload)
    except ValidationError:
        return RightsizingUnavailableScan(reason_codes=(RIGHTSIZING_OBSERVATION_INVALID,))
    if len(observed.workloads) > limit or observed.coverage.workloads_evaluated > limit:
        return RightsizingUnavailableScan(reason_codes=(RIGHTSIZING_OBSERVATION_INVALID,))
    if namespaces and any(
        workload.resource.namespace not in namespaces for workload in observed.workloads
    ):
        return RightsizingUnavailableScan(reason_codes=(RIGHTSIZING_OBSERVATION_INVALID,))
    return observed
