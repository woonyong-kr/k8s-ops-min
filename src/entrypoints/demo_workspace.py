"""Seed or reset the descriptor-owned UI demo workspace through repository contracts."""

from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import sys
from collections.abc import Mapping, Sequence
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
SRC = ROOT / "src"
if str(SRC) not in sys.path:
    sys.path.insert(0, str(SRC))

from domains.dashboard.repository import timeline_update_from_event  # noqa: E402
from domains.demo_workspace.policy import require_demo_workspace_mutation_opt_in  # noqa: E402
from domains.gitops.repository import derive_application_id, derive_workflow_run_id  # noqa: E402
from domains.gitops.repository_discovery import (  # noqa: E402
    RepositoryDiscoveryError,
    RepositoryDiscoveryService,
)
from domains.inventory.events import InventorySnapshotRecordedBody  # noqa: E402
from domains.inventory.ingest import ingest_inventory_snapshot  # noqa: E402
from domains.rca.events import (  # noqa: E402
    ClusterEvidenceReceivedBody,
    IncidentDetectedBody,
    IncidentRecord,
    RcaCompletedBody,
    RcaReportDetail,
    compact_cluster_evidence_payload,
)
from packages.contracts.cost.observations import (  # noqa: E402
    COST_NAMESPACE_HOURLY_METRIC,
    COST_NAMESPACE_STORAGE_METRIC,
)
from packages.contracts.demo_workspace import (  # noqa: E402
    DEMO_SEED_MARKER_KEY,
    DemoGitOpsSourceDescriptor,
    DemoWorkspaceDescriptor,
)
from packages.contracts.event_bus.interfaces import EventEnvelope  # noqa: E402
from packages.contracts.event_bus.subjects import EventSubject  # noqa: E402
from packages.contracts.gateway.requests import (  # noqa: E402
    InventorySnapshotRequest,
    RepositoryManifestValidationRequest,
    RepositoryProbeRequest,
)
from packages.contracts.gitops import (  # noqa: E402
    ManifestArtifactStatus,
    WorkflowRunStatus,
    WorkflowStepName,
    WorkflowStepStatus,
)
from packages.contracts.traffic.observations import TRAFFIC_CARETTA_FLOW_METRIC  # noqa: E402
from packages.events.context import event_workspace  # noqa: E402
from packages.events.envelope import event  # noqa: E402
from packages.runtime.gateway import ApiEventGateway  # noqa: E402
from packages.storage.database import Database  # noqa: E402
from packages.storage.engine import unit_of_work_or_null  # noqa: E402

DEFAULT_DESCRIPTOR = ROOT / "src" / "samples" / "demo-workspace" / "v1.json"
DEMO_EVENT_SOURCE = "demo-workspace-seed"


class OutboxRequiredPublisher:
    """Reject accidental broker publication when the durable DB outbox is unavailable."""

    async def emit(self, *_args: Any, **_kwargs: Any) -> Any:
        raise RuntimeError("demo inventory events require the database outbox")


async def validate_demo_gitops_sources(
    descriptor: DemoWorkspaceDescriptor,
    discovery: Any,
) -> list[dict[str, object]]:
    """Validate the public repository using the same discovery contract as connect."""

    gitops = descriptor.gitops
    if gitops is None:
        return []
    if discovery is None:
        raise RuntimeError("demo GitOps seed requires repository discovery")

    probe = await discovery.probe_repository(RepositoryProbeRequest(repo_ref=gitops.repo_ref))
    if not probe.valid or not probe.reachable:
        detail = probe.errors[0] if probe.errors else "repository is not reachable"
        raise RuntimeError(f"demo GitOps repository validation failed: {detail}")
    if probe.private is not False:
        raise RuntimeError("demo GitOps repository must be confirmed public")
    if probe.default_branch != gitops.default_branch:
        raise RuntimeError("demo GitOps repository default branch does not match descriptor")

    normalized_repo_ref = probe.normalized_repo_ref
    validation_requests = [
        RepositoryManifestValidationRequest(
            repo_ref=normalized_repo_ref,
            branch=gitops.default_branch,
            manifest_path=source.manifest_path,
            source_type=source.source_type,
            values_path=source.values_path,
        )
        for source in gitops.sources
    ]
    try:
        branches, batch = await asyncio.gather(
            # probe(89행)가 이미 repository metadata를 조회했으므로 default_branch를 재사용해
            # list_branches의 중복 GitHub repository GET을 생략한다(rate-limit 완화).
            discovery.list_branches(
                normalized_repo_ref, metadata={"default_branch": probe.default_branch}
            ),
            discovery.validate_manifests_at_revision(
                validation_requests,
                expected_revision=gitops.revision,
            ),
        )
    except RepositoryDiscoveryError as exc:
        if exc.status_code == 409:
            raise RuntimeError("demo GitOps repository changed during source validation") from exc
        raise
    branch_names = {branch.name for branch in branches.branches}
    if gitops.default_branch not in branch_names:
        raise RuntimeError("demo GitOps default branch is unavailable")
    if (
        batch.repo_ref != normalized_repo_ref
        or batch.branch != gitops.default_branch
        or batch.revision != gitops.revision
        or len(batch.validations) != len(gitops.sources)
    ):
        raise RuntimeError("demo GitOps repository validation batch is incomplete")
    evidence: list[dict[str, object]] = []
    for source, validation in zip(gitops.sources, batch.validations, strict=True):
        if (
            not validation.valid
            or validation.repo_ref != normalized_repo_ref
            or validation.branch != gitops.default_branch
            or validation.manifest_path != source.manifest_path
        ):
            detail = validation.errors[0] if validation.errors else "manifest validation failed"
            raise RuntimeError(f"demo GitOps source validation failed ({source.name}): {detail}")
        resources = [resource.model_dump(mode="json") for resource in validation.resources]
        identities = {
            (
                str(resource.get("kind") or "").casefold(),
                str(resource.get("name") or ""),
            )
            for resource in resources
        }
        if (
            validation.resource_count < 1
            or validation.resource_count != len(resources)
            or len(identities) != len(resources)
            or any(
                not all(
                    (
                        str(resource.get("api_version") or ""),
                        str(resource.get("kind") or ""),
                        str(resource.get("name") or ""),
                    )
                )
                for resource in resources
            )
        ):
            raise RuntimeError(
                f"demo GitOps source validation evidence is incomplete ({source.name})"
            )
        evidence.append(
            {
                "source": source,
                "repo_ref": normalized_repo_ref,
                "validation_mode": validation.validation_mode,
                "resource_count": validation.resource_count,
                "resources": resources,
                "warnings": list(validation.warnings),
            }
        )
    return evidence


def persist_demo_gitops_sources(
    db: Any,
    descriptor: DemoWorkspaceDescriptor,
    *,
    marker: Mapping[str, object],
    evidence: Sequence[Mapping[str, object]],
) -> int:
    """Persist validated sources and read-only runtime evidence through canonical stores."""

    gitops = descriptor.gitops
    if gitops is None:
        return 0
    if len(evidence) != len(gitops.sources):
        raise RuntimeError("demo GitOps validation evidence is incomplete")
    normalized_repo_refs = {str(item.get("repo_ref") or "") for item in evidence}
    if len(normalized_repo_refs) != 1 or "" in normalized_repo_refs:
        raise RuntimeError("demo GitOps repository identity is incomplete")

    workspace_id = descriptor.workspace.workspace_id
    validated_at = datetime.now(UTC)
    repository = db.register_repository(
        {
            "workspace_id": workspace_id,
            "user_id": descriptor.workspace.owner_user_id,
            "provider": "github",
            "repo_ref": normalized_repo_refs.pop(),
            "default_branch": gitops.default_branch,
            "credential_ref": None,
            "status": "active",
            "access_policy": {
                DEMO_SEED_MARKER_KEY: dict(marker),
                "visibility": "public",
                "mutation": "read-only-demo",
                "revision": gitops.revision,
                "catalog_scenario_count": gitops.catalog_scenario_count,
            },
        }
    )
    repository_id = str(repository["repository_id"])
    for item in evidence:
        source = item.get("source")
        if not isinstance(source, DemoGitOpsSourceDescriptor):
            raise RuntimeError("demo GitOps source validation evidence is invalid")
        validation_metadata = {
            "branch": gitops.default_branch,
            "source_type": source.source_type,
            "validation_mode": str(item.get("validation_mode") or ""),
            "validated_resource_count": int(item.get("resource_count") or 0),
            "validation_warnings": list(item.get("warnings") or []),
            "repository_revision": gitops.revision,
            "catalog_scenario_count": gitops.catalog_scenario_count,
            "values_path": source.values_path,
            DEMO_SEED_MARKER_KEY: dict(marker),
        }
        body = {
            "workspace_id": workspace_id,
            "user_id": descriptor.workspace.owner_user_id,
            "repository_id": repository_id,
            "repo_ref": str(repository["repo_ref"]),
            "name": source.name,
            "default_branch": gitops.default_branch,
            "branch": gitops.default_branch,
            "manifest_path": source.manifest_path,
            "metadata": validation_metadata,
            "cluster_id": descriptor.cluster.cluster_id,
            "namespace": source.namespace,
            "environment": source.environment,
            "resource_class": "application",
            "status": "active",
            "interval_seconds": source.interval_seconds,
            "settings": {
                "source_type": source.source_type,
                "poll_status": "ok",
                "poll_status_code": 200,
                "poll_error_kind": "",
                "poll_error": "",
                DEMO_SEED_MARKER_KEY: dict(marker),
            },
            "last_seen_commit_sha": gitops.revision,
            "last_polled_at": validated_at,
            "deploy_policy": {
                "manifest_source": source.source_type,
                "validation_mode": validation_metadata["validation_mode"],
                "values_path": source.values_path,
                "read_only": True,
            },
            "access_policy": {
                DEMO_SEED_MARKER_KEY: dict(marker),
                "mutation": "read-only-demo",
            },
        }
        application = ensure_demo_gitops_application(
            db,
            body,
            marker=marker,
        )
        binding_body = {
            **body,
            "application_id": str(application["application_id"]),
            "app_name": str(application.get("name") or source.name),
        }
        watch_target = db.register_watch_target(binding_body)
        binding = db.register_deployment_binding(binding_body)
        _persist_demo_gitops_runtime(
            db,
            descriptor,
            source=source,
            application=application,
            binding=binding,
            watch_target=watch_target,
            validation=item,
            marker=marker,
        )
    return len(evidence)


def derive_demo_seed_application_id(
    payload: Mapping[str, object],
    marker: Mapping[str, object],
) -> str:
    """Derive the collision fallback identity for one descriptor-owned app."""

    descriptor_id = marker.get("descriptor_id")
    schema_version = marker.get("schema_version")
    identity = (
        descriptor_id,
        schema_version,
        payload.get("workspace_id"),
        payload.get("repository_id"),
        payload.get("manifest_path"),
        payload.get("name"),
    )
    if (
        not isinstance(descriptor_id, str)
        or not descriptor_id
        or not isinstance(schema_version, int)
        or any(not str(value or "") for value in identity[2:])
    ):
        raise RuntimeError("demo GitOps application fallback identity is incomplete")
    raw = "\0".join(("demo-seed-application-v1", *(str(value) for value in identity)))
    return f"app-{hashlib.sha256(raw.encode()).hexdigest()[:32]}"


def ensure_demo_gitops_application(
    db: Any,
    payload: Mapping[str, object],
    *,
    marker: Mapping[str, object],
) -> Mapping[str, object]:
    """Create a demo-owned app or update the exact marker-owned identity."""

    workspace_id = str(payload.get("workspace_id") or "")
    repository_id = str(payload.get("repository_id") or "")
    name = str(payload.get("name") or "")
    if not all((workspace_id, repository_id, name)):
        raise RuntimeError("demo GitOps application identity is incomplete")
    lookup = getattr(db, "get_application_by_identity", None)
    if not callable(lookup):
        raise RuntimeError("demo GitOps application identity lookup is unavailable")

    def require_demo_owned(application: object) -> Mapping[str, object]:
        if not isinstance(application, Mapping):
            raise RuntimeError("demo GitOps application identity is unavailable")
        metadata = application.get("metadata")
        if (
            str(application.get("workspace_id") or "") != workspace_id
            or str(application.get("repository_id") or "") != repository_id
            or str(application.get("name") or "") != name
            or not isinstance(metadata, Mapping)
            or metadata.get(DEMO_SEED_MARKER_KEY) != marker
        ):
            raise RuntimeError("demo GitOps application identity is not seed-owned")
        return application

    def reconcile_legacy_identity(
        persisted_application_id: object | None = None,
    ) -> Mapping[str, object] | None:
        reconcile = getattr(db, "reconcile_seed_owned_application", None)
        if not callable(reconcile):
            return None
        derived_payload = dict(payload)
        derived_payload.pop("application_id", None)
        application_id = str(persisted_application_id or "") or derive_application_id(
            derived_payload
        )
        return reconcile(
            workspace_id=workspace_id,
            application_id=application_id,
            repository_id=repository_id,
            name=name,
            manifest_path=str(payload.get("manifest_path") or ""),
            status=str(payload.get("status") or "active"),
            metadata_=dict(payload.get("metadata") or {}),
            expected_marker=marker,
        )

    def create_collision_fallback() -> Mapping[str, object]:
        fallback_payload = dict(payload)
        fallback_payload["application_id"] = derive_demo_seed_application_id(payload, marker)
        return require_demo_owned(db.upsert_application(fallback_payload))

    existing = lookup(workspace_id, repository_id, name)
    if existing is None:
        try:
            application = db.upsert_application(dict(payload))
        except LookupError:
            # A concurrent seed can win the exact identity. Legacy descriptor
            # rows use a separate, marker-authorized reconciliation boundary.
            concurrent = lookup(workspace_id, repository_id, name)
            if concurrent is None:
                application = reconcile_legacy_identity()
                if application is None:
                    # The canonical id is occupied outside this exact demo
                    # identity. Preserve that row and create a stable,
                    # descriptor-scoped application id instead.
                    application = create_collision_fallback()
                else:
                    application = require_demo_owned(application)
            else:
                try:
                    require_demo_owned(concurrent)
                except RuntimeError:
                    application = require_demo_owned(
                        reconcile_legacy_identity(concurrent.get("application_id"))
                    )
                else:
                    update_payload = dict(payload)
                    update_payload.pop("user_id", None)
                    application = db.upsert_application(update_payload)
    else:
        try:
            require_demo_owned(existing)
        except RuntimeError:
            application = require_demo_owned(
                reconcile_legacy_identity(existing.get("application_id"))
            )
        else:
            update_payload = dict(payload)
            update_payload.pop("user_id", None)
            application = db.upsert_application(update_payload)

    return require_demo_owned(application)


def _persist_demo_gitops_runtime(
    db: Any,
    descriptor: DemoWorkspaceDescriptor,
    *,
    source: DemoGitOpsSourceDescriptor,
    application: Mapping[str, object],
    binding: Mapping[str, object],
    watch_target: Mapping[str, object],
    validation: Mapping[str, object],
    marker: Mapping[str, object],
) -> None:
    """Record one idempotent, read-only validation run for an attached source.

    The run proves repository access and server-side rendering only. Apply, live
    diff, and rollout steps stay explicitly skipped so the demo cannot be
    mistaken for a target-cluster mutation.
    """

    gitops = descriptor.gitops
    if gitops is None:
        raise RuntimeError("demo GitOps runtime requires repository evidence")
    resources = validation.get("resources")
    resource_rows = (
        [dict(resource) for resource in resources if isinstance(resource, Mapping)]
        if isinstance(resources, Sequence) and not isinstance(resources, (str, bytes))
        else []
    )
    resource_count = int(validation.get("resource_count") or 0)
    if resource_count < 1 or len(resource_rows) != resource_count:
        raise RuntimeError("demo GitOps render evidence is incomplete")

    application_id = str(application.get("application_id") or "")
    binding_id = str(binding.get("binding_id") or "")
    watch_target_id = str(watch_target.get("watch_target_id") or "")
    repository_id = str(application.get("repository_id") or binding.get("repository_id") or "")
    if not all((application_id, binding_id, watch_target_id, repository_id)):
        raise RuntimeError("demo GitOps runtime identity is incomplete")

    identity = {
        "workspace_id": descriptor.workspace.workspace_id,
        "repository_id": repository_id,
        "watch_target_id": watch_target_id,
        "binding_id": binding_id,
        "application_id": application_id,
        "environment": source.environment,
        "cluster_id": descriptor.cluster.cluster_id,
        "commit_sha": gitops.revision,
        "manifest_path": source.manifest_path,
        "repo_ref": str(validation.get("repo_ref") or gitops.repo_ref),
        "branch": gitops.default_branch,
    }
    workflow_run_id = derive_workflow_run_id(identity)
    runtime_metadata = {
        "runtime_mode": "read-only-demo",
        "evidence_kind": "repository_manifest_validation",
        "version": gitops.revision[:12],
        "deployed_by": DEMO_EVENT_SOURCE,
        "source_type": source.source_type,
        "validation_mode": str(validation.get("validation_mode") or ""),
        "validated_resource_count": resource_count,
        "repository_revision": gitops.revision,
        DEMO_SEED_MARKER_KEY: dict(marker),
    }
    db.start_workflow_run(
        {
            **identity,
            "workflow_run_id": workflow_run_id,
            "status": WorkflowRunStatus.SUCCEEDED.value,
            "current_step": WorkflowStepName.RENDER.value,
            "summary": (
                f"Validated {resource_count} rendered resources from {source.manifest_path}; "
                "read-only demo, no cluster mutation"
            ),
            "metadata": runtime_metadata,
        }
    )

    common_step = {
        **identity,
        "workflow_run_id": workflow_run_id,
    }
    for name, status, message, details in (
        (
            WorkflowStepName.GIT.value,
            WorkflowStepStatus.SUCCEEDED.value,
            "Pinned public repository revision validated",
            {
                "repo_ref": identity["repo_ref"],
                "branch": gitops.default_branch,
                "commit_sha": gitops.revision,
            },
        ),
        (
            WorkflowStepName.RENDER.value,
            WorkflowStepStatus.SUCCEEDED.value,
            "Repository source rendered and validated",
            {
                "source_type": source.source_type,
                "validation_mode": runtime_metadata["validation_mode"],
                "resource_count": resource_count,
                "warnings": list(validation.get("warnings") or []),
            },
        ),
        (
            WorkflowStepName.DIFF.value,
            WorkflowStepStatus.SKIPPED.value,
            "Live comparison is not claimed by the read-only demo seed",
            {"reason_code": "live_observation_not_seeded"},
        ),
        (
            WorkflowStepName.POLICY.value,
            WorkflowStepStatus.SUCCEEDED.value,
            "Read-only demo policy validated",
            {"read_only": True, "mutation": "read-only-demo"},
        ),
        (
            WorkflowStepName.APPROVAL.value,
            WorkflowStepStatus.SKIPPED.value,
            "Approval is not required for repository validation",
            {"reason_code": "no_cluster_mutation"},
        ),
        (
            WorkflowStepName.APPLY.value,
            WorkflowStepStatus.SKIPPED.value,
            "Cluster apply is disabled for the read-only demo seed",
            {"reason_code": "read_only_demo"},
        ),
        (
            WorkflowStepName.HEALTH.value,
            WorkflowStepStatus.SKIPPED.value,
            "Rollout health is unavailable without a cluster apply",
            {"reason_code": "rollout_not_started"},
        ),
    ):
        db.record_workflow_step(
            {
                **common_step,
                "name": name,
                "status": status,
                "message": message,
                "details": details,
            }
        )

    source_summary = {
        "repo_ref": identity["repo_ref"],
        "branch": gitops.default_branch,
        "manifest_path": source.manifest_path,
        "source_type": source.source_type,
        "source_origin": "repository_manifest_validation",
        "source_document_count": resource_count,
        "validation_mode": runtime_metadata["validation_mode"],
        "cluster_id": descriptor.cluster.cluster_id,
        "application_id": application_id,
        "workflow_run_id": workflow_run_id,
        "environment": source.environment,
        DEMO_SEED_MARKER_KEY: dict(marker),
    }
    for resource in resource_rows:
        api_version = str(resource.get("api_version") or "")
        kind = str(resource.get("kind") or "")
        name = str(resource.get("name") or "")
        namespace = resource.get("namespace")
        if not all((api_version, kind, name)):
            raise RuntimeError("demo GitOps rendered resource identity is incomplete")
        rendered = {
            "apiVersion": api_version,
            "kind": kind,
            "metadata": {
                "name": name,
                **({"namespace": str(namespace)} if namespace is not None else {}),
            },
        }
        artifact_digest = (
            "sha256:"
            + hashlib.sha256(
                json.dumps(rendered, sort_keys=True, separators=(",", ":")).encode()
            ).hexdigest()
        )
        db.record_manifest_artifact(
            {
                **identity,
                "workflow_run_id": workflow_run_id,
                "manifest_path": f"{source.manifest_path}#{kind.casefold()}/{name}",
                "status": ManifestArtifactStatus.RENDERED.value,
                "rendered_manifest": {**rendered, "artifact_digest": artifact_digest},
                "source_summary": {
                    **source_summary,
                    "resource": f"{kind.casefold()}/{name}",
                    "artifact_digest": artifact_digest,
                },
            }
        )


def persist_demo_observation(
    db: Any,
    descriptor: DemoWorkspaceDescriptor,
    *,
    marker: Mapping[str, object],
    observed_at: datetime,
) -> int:
    """Persist one explicit synthetic demo window through the Agent evidence ledger."""

    observation = descriptor.observations
    if observation is None:
        return 0
    workspace_id = descriptor.workspace.workspace_id
    cluster_id = descriptor.cluster.cluster_id
    window_start = _aware_utc(observed_at).isoformat()
    digest = descriptor.digest()
    evidence_key = f"demo-observation:{workspace_id}:{cluster_id}:{digest}"
    correlation_id = f"demo-observation-{digest[:24]}"
    results = {
        COST_NAMESPACE_HOURLY_METRIC: {
            "samples": [
                {
                    "metric": {"namespace": item.namespace},
                    "value": _micro_usd(item.hourly_rate_micros),
                }
                for item in observation.cost_namespace_rates
            ]
        },
        COST_NAMESPACE_STORAGE_METRIC: {
            "samples": [
                {
                    "metric": {"namespace": item.namespace},
                    "value": _micro_usd(item.storage_rate_micros),
                }
                for item in observation.cost_namespace_rates
            ]
        },
        TRAFFIC_CARETTA_FLOW_METRIC: {
            "samples": [
                {
                    "metric": {
                        "client_name": item.source_name,
                        "client_namespace": item.source_namespace,
                        "client_kind": item.source_kind,
                        "server_name": item.target_name,
                        "server_namespace": item.target_namespace or "",
                        "server_kind": item.target_kind,
                        "server_service": item.target_service or "",
                        "server_port": str(item.port),
                    },
                    "timestamp": _aware_utc(observed_at).timestamp(),
                    "value": item.connections,
                }
                for item in observation.traffic_flows
            ]
        },
    }
    evidence_body = ClusterEvidenceReceivedBody(
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        agent_id=descriptor.cluster.agent_id,
        source_id="cluster-snapshot",
        window_start=window_start,
        evidence_key=evidence_key,
        correlation_id=correlation_id,
        kubernetes={},
        metrics={"results": results},
        logs=[],
        traces={},
        collection_status={
            "availability": "observed",
            "mode": observation.origin,
            "cost_namespace_count": len(observation.cost_namespace_rates),
            "traffic_source": observation.traffic_source,
            "traffic_flow_count": len(observation.traffic_flows),
        },
        metadata={
            "runtime_evidence_version": observation.runtime_evidence_version,
            "synthetic": True,
            DEMO_SEED_MARKER_KEY: dict(marker),
        },
    )
    envelope = event(
        evidence_body.__subject__,
        DEMO_EVENT_SOURCE,
        compact_cluster_evidence_payload(evidence_body, correlation_id),
        correlation_id,
        workspace_id=workspace_id,
    )
    db.record_evidence_event_once(
        evidence_key=evidence_key,
        workspace_id=workspace_id,
        cluster_id=cluster_id,
        source_id="cluster-snapshot",
        window_start=window_start,
        agent_id=descriptor.cluster.agent_id,
        event_envelope=envelope,
        payload=evidence_body.to_body(),
    )
    return 1


def persist_demo_rca_scenario(
    db: Any,
    descriptor: DemoWorkspaceDescriptor,
    *,
    observed_at: datetime,
) -> int:
    """Project one typed synthetic scenario through the canonical RCA read-model writer."""

    scenario = descriptor.rca
    if scenario is None:
        return 0
    workspace_id = descriptor.workspace.workspace_id
    cluster_id = descriptor.cluster.cluster_id
    digest = descriptor.digest()
    correlation_id = f"demo-rca-{digest[:24]}"
    evidence_ref = f"synthetic://demo-workspace/{descriptor.descriptor_id}/{scenario.incident_id}"
    incident = IncidentRecord(
        incident_id=scenario.incident_id,
        cluster_id=cluster_id,
        resource_kind=scenario.resource_kind,
        resource_name=scenario.resource_name,
        namespace=scenario.namespace,
        symptom=scenario.symptom,
        severity=scenario.severity,
        first_seen_at=(
            _aware_utc(observed_at) + timedelta(seconds=scenario.timeline[0].offset_seconds)
        ).isoformat(),
        summary=scenario.summary,
        category=scenario.category,
        workspace_id=workspace_id,
    )
    detected = IncidentDetectedBody(
        cluster_id=cluster_id,
        detected=True,
        reason=scenario.timeline[0].summary,
        workspace_id=workspace_id,
        severity=scenario.severity,
        incident=incident,
    )
    completed = RcaCompletedBody(
        root_cause=scenario.root_cause,
        action=scenario.action,
        evidence_ref=evidence_ref,
        workspace_id=workspace_id,
        incident=incident,
        rca_detail=RcaReportDetail(
            root_cause=scenario.root_cause,
            confidence=scenario.confidence,
            selected_candidate_id=scenario.cause_id,
            supporting_evidence=[
                *(f"descriptor-evidence:{item}" for item in scenario.supporting_evidence),
                *(f"descriptor-impact:{item}" for item in scenario.impact),
            ],
            missing_evidence=list(scenario.missing_evidence),
            reason=scenario.timeline[1].summary,
        ),
    )
    stages = (
        (EventSubject.INCIDENT_DETECTED.value, detected, scenario.timeline[0]),
        (EventSubject.RCA_COMPLETED.value, completed, scenario.timeline[1]),
    )
    causation_id: str | None = None
    for index, (subject, body, step) in enumerate(stages, start=1):
        event_id = f"demo-rca-{digest[:20]}-{index}"
        envelope = EventEnvelope(
            event_id=event_id,
            subject=subject,
            source=DEMO_EVENT_SOURCE,
            correlation_id=correlation_id,
            causation_id=causation_id,
            created_at=(
                _aware_utc(observed_at) + timedelta(seconds=step.offset_seconds)
            ).isoformat(),
            payload=body.to_body(),
            workspace_id=workspace_id,
        )
        row = timeline_update_from_event(envelope)
        if row is None:
            raise RuntimeError(f"demo RCA stage was not projected: {step.stage}")
        db.upsert_rca_timeline(row)
        causation_id = event_id
    return 1


def _micro_usd(value: int) -> str:
    units, micros = divmod(value, 1_000_000)
    return f"{units}.{micros:06d}"


def _aware_utc(value: datetime) -> datetime:
    return value.replace(tzinfo=UTC) if value.tzinfo is None else value.astimezone(UTC)


def load_descriptor(
    path: Path,
    *,
    owner_user_id: str | None = None,
) -> DemoWorkspaceDescriptor:
    with path.open(encoding="utf-8") as handle:
        descriptor = DemoWorkspaceDescriptor.model_validate(json.load(handle))
    if owner_user_id is None:
        return descriptor
    return descriptor.with_owner_user_id(owner_user_id)


def _persisted_registration_marker(registration: object) -> object:
    if not isinstance(registration, Mapping):
        return None
    settings = registration.get("settings")
    return settings.get(DEMO_SEED_MARKER_KEY) if isinstance(settings, Mapping) else None


def _persisted_snapshot_marker(snapshot: object) -> object:
    if not isinstance(snapshot, Mapping):
        return None
    envelope = snapshot.get("summary")
    if not isinstance(envelope, Mapping):
        return None
    summary = envelope.get("summary")
    return summary.get(DEMO_SEED_MARKER_KEY) if isinstance(summary, Mapping) else None


async def seed_demo_workspace(
    db: Any,
    descriptor: DemoWorkspaceDescriptor,
    *,
    events: Any,
    discovery: Any | None = None,
    observed_at: datetime | None = None,
) -> dict[str, object]:
    require_demo_workspace_mutation_opt_in()
    workspace_id = descriptor.workspace.workspace_id
    cluster_id = descriptor.cluster.cluster_id
    marker = descriptor.seed_marker()
    registration = db.get_cluster_registration(workspace_id, cluster_id)
    snapshot = db.latest_inventory_snapshot(workspace_id, cluster_id)
    registration_current = _persisted_registration_marker(registration) == marker
    snapshot_current = _persisted_snapshot_marker(snapshot) == marker
    collected_at = _aware_utc(observed_at or datetime.now(UTC))

    if registration_current and snapshot_current:
        return {
            "action": "unchanged",
            "descriptor_id": descriptor.descriptor_id,
            "digest": descriptor.digest(),
            "workspace_id": workspace_id,
            "cluster_id": cluster_id,
            "snapshot_id": str(snapshot["snapshot_id"]),
            "gitops_source_count": len(descriptor.gitops.sources) if descriptor.gitops else 0,
            "observation_window_count": 1 if descriptor.observations else 0,
            "rca_scenario_count": 1 if descriptor.rca else 0,
        }

    gitops_evidence = await validate_demo_gitops_sources(descriptor, discovery)
    result: Mapping[str, object] = snapshot if isinstance(snapshot, Mapping) else {}

    async def register_demo_target() -> None:
        if not registration_current:
            db.register_target_cluster(
                {
                    "workspace_id": workspace_id,
                    "organization_id": workspace_id,
                    "user_id": descriptor.workspace.owner_user_id,
                    "cluster_id": cluster_id,
                    "name": descriptor.cluster.name,
                    "environment": descriptor.cluster.environment,
                    "status": "registered",
                    "settings": {
                        **descriptor.cluster.settings,
                        DEMO_SEED_MARKER_KEY: marker,
                    },
                }
            )

    def persist_gitops() -> int:
        return persist_demo_gitops_sources(
            db,
            descriptor,
            marker=marker,
            evidence=gitops_evidence,
        )

    def persist_observation() -> int:
        return persist_demo_observation(
            db,
            descriptor,
            marker=marker,
            observed_at=collected_at,
        )

    def persist_rca() -> int:
        return persist_demo_rca_scenario(
            db,
            descriptor,
            observed_at=collected_at,
        )

    gitops_source_count = 0
    observation_window_count = 0
    rca_scenario_count = 0
    if snapshot_current:
        with unit_of_work_or_null(db):
            await register_demo_target()
            observation_window_count = persist_observation()
            rca_scenario_count = persist_rca()
            gitops_source_count = persist_gitops()
    else:
        inventory = InventorySnapshotRequest.model_validate(
            {
                "cluster_id": cluster_id,
                "agent_id": descriptor.cluster.agent_id,
                "source": f"{DEMO_EVENT_SOURCE}:v{descriptor.schema_version}",
                "collected_at": collected_at.isoformat(),
                "replace": descriptor.inventory.replace,
                "resources": [
                    item.model_dump(mode="json") for item in descriptor.inventory.resources
                ],
                "summary": {
                    **descriptor.inventory.summary,
                    DEMO_SEED_MARKER_KEY: marker,
                },
                "health": descriptor.inventory.health,
                "usage": descriptor.inventory.usage,
            }
        )

        async def persist_snapshot_dependencies(saved: dict[str, Any]) -> None:
            nonlocal gitops_source_count, observation_window_count, rca_scenario_count
            if saved.get("accepted") is not True:
                raise RuntimeError("demo inventory snapshot was not accepted")
            observation_window_count = persist_observation()
            rca_scenario_count = persist_rca()
            gitops_source_count = persist_gitops()
            await events.accept_body(
                InventorySnapshotRecordedBody(
                    workspace_id=workspace_id,
                    cluster_id=cluster_id,
                    snapshot_id=str(saved["snapshot_id"]),
                    agent_id=descriptor.cluster.agent_id,
                    resource_count=int(saved["resource_count"]),
                    resource_types=list(saved["resource_types"]),
                )
            )

        with event_workspace(workspace_id):
            result = await ingest_inventory_snapshot(
                db=db,
                workspace_id=workspace_id,
                cluster_id=cluster_id,
                agent_id=descriptor.cluster.agent_id,
                payload=inventory.model_dump(mode="json"),
                before_persist=register_demo_target,
                after_persist=persist_snapshot_dependencies,
            )

    return {
        "action": "seeded",
        "descriptor_id": descriptor.descriptor_id,
        "digest": descriptor.digest(),
        "workspace_id": workspace_id,
        "cluster_id": cluster_id,
        "snapshot_id": str(result["snapshot_id"]),
        "registration_written": not registration_current,
        "inventory_written": not snapshot_current,
        "gitops_source_count": gitops_source_count,
        "observation_window_count": observation_window_count,
        "rca_scenario_count": rca_scenario_count,
    }


def reset_demo_workspace(db: Any, descriptor: DemoWorkspaceDescriptor) -> dict[str, object]:
    require_demo_workspace_mutation_opt_in()
    counts = db.reset_demo_workspace(
        workspace_id=descriptor.workspace.workspace_id,
        cluster_id=descriptor.cluster.cluster_id,
        expected_marker=descriptor.seed_marker(),
        event_source=DEMO_EVENT_SOURCE,
    )
    return {
        "action": "reset",
        "descriptor_id": descriptor.descriptor_id,
        "digest": descriptor.digest(),
        "workspace_id": descriptor.workspace.workspace_id,
        "cluster_id": descriptor.cluster.cluster_id,
        "deleted": counts,
    }


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("seed", "reset"))
    parser.add_argument(
        "--descriptor",
        type=Path,
        default=DEFAULT_DESCRIPTOR,
        help="versioned demo workspace descriptor",
    )
    parser.add_argument(
        "--owner-user-id",
        help="validated runtime owner included in the effective descriptor digest",
    )
    return parser.parse_args(argv)


def main(argv: Sequence[str] | None = None) -> None:
    args = parse_args(argv)
    descriptor = load_descriptor(args.descriptor, owner_user_id=args.owner_user_id)
    require_demo_workspace_mutation_opt_in()
    db = Database()
    try:
        db.verify_schema()
        if args.action == "seed":
            events = ApiEventGateway(OutboxRequiredPublisher(), db, DEMO_EVENT_SOURCE)
            output = asyncio.run(
                seed_demo_workspace(
                    db,
                    descriptor,
                    events=events,
                    discovery=RepositoryDiscoveryService(),
                )
            )
        else:
            output = reset_demo_workspace(db, descriptor)
        print(json.dumps(output, ensure_ascii=False, sort_keys=True))
    finally:
        db.engine.dispose()
        asyncio.run(db.async_engine.dispose())


if __name__ == "__main__":
    main()
